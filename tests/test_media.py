from concurrent.futures import CancelledError
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event
import pytest
from livecatch_core.media import ExportOptions, export_command, export_batch, export_one, probe_cuda, probe_media, run_media_process

noop=lambda *a,**k:None

@pytest.mark.parametrize('backend',['cuda','cpu'])
def test_export_commands(backend):
    cmd=export_command('ffmpeg',Path('original.mkv'),Path('temp.mp4'),ExportOptions(height=720,device=1),backend)
    assert cmd[cmd.index('-i')+1]=='original.mkv'
    assert cmd[-1]=='temp.mp4' and '-map' in cmd
    if backend=='cuda':
        assert cmd.index('-hwaccel')<cmd.index('-i')
        assert 'h264_nvenc' in cmd and 'scale_cuda' in cmd[cmd.index('-vf')+1]
    else:assert 'libx264' in cmd and '-hwaccel' not in cmd

@pytest.mark.parametrize(('preset','ffmpeg_preset'), [('balanced','p4'),('fast','p2'),('max_speed','p1')])
def test_nvenc_speed_presets(preset,ffmpeg_preset):
    cmd=export_command('ffmpeg',Path('original.mkv'),Path('temp.mp4'),ExportOptions(preset=preset),'cuda')
    assert cmd[cmd.index('-preset')+1]==ffmpeg_preset

@pytest.mark.parametrize('opts',[ExportOptions(mode='off'),ExportOptions(height=17),ExportOptions(jobs=9),ExportOptions(device=-1),ExportOptions(preset='warp')])
def test_bad_export_options(opts):
    with pytest.raises(ValueError):opts.validate()

def test_never_overwrite(tmp_path):
    src=tmp_path/'a.mp4';src.write_bytes(b'original')
    with pytest.raises(FileExistsError):export_one(src,src,ExportOptions(), 'cpu','none','none',Event(),noop)
    dst=tmp_path/'b.mp4';dst.write_bytes(b'existing')
    with pytest.raises(FileExistsError):export_one(src,dst,ExportOptions(),'cpu','none','none',Event(),noop)
    assert src.read_bytes()==b'original' and dst.read_bytes()==b'existing'

def test_cancel_before_launch():
    event=Event();event.set()
    with pytest.raises(CancelledError):run_media_process(['nonexistent'],event,noop)

def test_missing_cuda_probe():
    ok,message=probe_cuda('/nonexistent/ffmpeg');assert not ok and message

def test_hdr_refused(monkeypatch,tmp_path):
    result=subprocess.CompletedProcess([],0,stdout=json.dumps({'streams':[{'codec_type':'video','color_transfer':'smpte2084'}]}))
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:result)
    with pytest.raises(ValueError,match='HDR'):probe_media('ffprobe',tmp_path/'hdr.mkv')

def test_cpu_ffmpeg_roundtrip(tmp_path):
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:pytest.skip('ffmpeg/ffprobe unavailable')
    src=tmp_path/'owned synthetic source.mkv'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=12:duration=0.5','-f','lavfi','-i','sine=frequency=440:duration=0.5','-c:v','libx264','-threads','2','-c:a','aac','-shortest',str(src)],check=True,timeout=15)
    before=hashlib.sha256(src.read_bytes()).hexdigest()
    result=export_batch([src],ExportOptions(mode='cpu',height=480),ffmpeg,ffprobe,Event(),noop)
    assert len(result)==1 and result[0].is_file()
    info=probe_media(ffprobe,result[0]);v=next(s for s in info['streams'] if s['codec_type']=='video')
    assert v['height']==480 and any(s['codec_type']=='audio' for s in info['streams'])
    assert hashlib.sha256(src.read_bytes()).hexdigest()==before
    assert not list(tmp_path.glob('*.part.mp4'))

def test_auto_falls_back_without_gpu(tmp_path,monkeypatch):
    import livecatch_core.media as media
    seen=[]
    monkeypatch.setattr(media,'probe_cuda',lambda *a:(False,'no hardware'))
    monkeypatch.setattr(media,'export_one',lambda source,target,opts,backend,*a:seen.append(backend) or target)
    result=media.export_batch([tmp_path/'x.mkv'],ExportOptions(mode='auto'),'ffmpeg','ffprobe',Event(),noop)
    assert seen==['cpu'] and len(result)==1
    with pytest.raises(RuntimeError,match='CUDA'):media.export_batch([],ExportOptions(mode='cuda'),'ffmpeg','ffprobe',Event(),noop)
