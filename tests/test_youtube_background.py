from dataclasses import replace
from pathlib import Path
from queue import Empty
import json
import subprocess
import sys
from threading import Event
import time

import pytest

from livecatch_core.background import BackgroundConfig, BackgroundStore, InstanceLease, TrayController, WindowsStartup, startup_command, RUN_NAME
from livecatch_core.channels import broadcast_key, manual_target, normalize_target, parse_targets, target_url, valid_broadcast_id, youtube_target
from livecatch_core.config import Settings
from livecatch_core.events import EventBuffer
from livecatch_core.twitch_watch import (
    WatchChannel, WatchConfig, WatchManager, WatchStore, apply_monitor_preset,
    channel_settings, new_worker,
)
from livecatch_core.ui_text import format_elapsed, repair_mojibake
from livecatch_core.youtube_watch_probe import resolve_live

CHANNEL='UC'+'AbCd_1'*3+'ABCD'
VIDEO='AbCdEf_-123'

@pytest.mark.parametrize('value', ['@Example', 'youtube:@example', 'youtube.com/@Example', 'https://www.youtube.com/@Example/live', 'https://m.youtube.com/@example/streams'])
def test_youtube_handles(value):
    assert normalize_target(value)=='youtube:@example'

@pytest.mark.parametrize('value', ['https://youtube.com/channel/'+CHANNEL, CHANNEL, 'youtube:channel/'+CHANNEL])
def test_channel_id_case_is_preserved(value):
    assert youtube_target(value)=='youtube:channel/'+CHANNEL

@pytest.mark.parametrize('value', ['https://youtube.com/watch?v='+VIDEO, 'https://youtu.be/'+VIDEO, 'https://youtube.com/playlist?list=x', 'https://youtube.com/@ok?list=x', 'https://youtube.com.attacker.test/@ok', 'https://user@youtube.com/@ok', 'https://youtube.com:5000/@ok', 'https://youtube.com/@ok/../../x', 'youtube:@bad%2Fpath', 'youtube:@bad%00', 'https://youtube.com/channel/bad', 'https://youtube.com/', 'youtube:@', 'youtube:c/..'])
def test_bad_youtube_targets(value):
    with pytest.raises((ValueError, UnicodeError)):
        youtube_target(value)


def test_mixed_parse_and_unicode():
    assert parse_targets('Example https://twitch.tv/example, @Example;youtube.com/@example') == ('example','youtube:@example')
    assert normalize_target('twitch:@Example')=='example'
    assert normalize_target('https://youtube.com/@%E3%81%B1%E3%82%8B')=='youtube:@ぱる'
    assert '%E3%81%B1' in target_url('youtube:@ぱる')
    assert target_url('example')=='https://www.twitch.tv/example'

@pytest.mark.parametrize('url', [f'https://youtu.be/{VIDEO}',f'https://youtube.com/watch?v={VIDEO}',f'https://www.youtube.com/live/{VIDEO}?a=b'])
def test_manual_video_identity(url):
    assert manual_target(url)==broadcast_key('youtube:',VIDEO)
    assert manual_target('https://other.invalid/watch?v='+VIDEO) is None


def test_watch_store_keeps_twitch_and_unknown_fields(tmp_path):
    store=WatchStore(tmp_path/'watch.json')
    store.path.write_text(json.dumps({'channels':[{'login':'Example','enabled':False}], 'keep':42}))
    cfg=store.load()
    assert cfg.channels==(WatchChannel('example',False),)
    cfg=replace(cfg,channels=cfg.channels+(WatchChannel('youtube:@example'),))
    store.save(cfg)
    assert store.load()==cfg
    assert json.loads(store.path.read_text())['keep']==42


def test_monitor_presets_are_separate_from_manual_settings():
    base=Settings(concurrent_fragments=8,prefetch=2,gpu_export='cuda')
    fast=apply_monitor_preset(base,'fast')
    extreme=apply_monitor_preset(base,'extreme')
    maximum=apply_monitor_preset(base,'max')
    ultra=apply_monitor_preset(base,'ultra')
    experimental=apply_monitor_preset(base,'experimental_256')
    assert (base.concurrent_fragments,base.prefetch)==(8,2)
    assert (fast.concurrent_fragments,fast.prefetch,fast.gpu_export)==(64,4,'off')
    assert (extreme.concurrent_fragments,extreme.prefetch,extreme.gpu_export)==(96,6,'off')
    assert (maximum.concurrent_fragments,maximum.prefetch,maximum.gpu_export)==(128,8,'off')
    assert (ultra.concurrent_fragments,ultra.prefetch,ultra.gpu_export)==(192,8,'off')
    assert (experimental.concurrent_fragments,experimental.prefetch,experimental.gpu_export)==(256,8,'off')
    assert apply_monitor_preset(base,'manual').gpu_export=='off'


def test_ui_mojibake_repair_and_elapsed_format():
    broken='録画'.encode('utf-8').decode('cp932')
    assert repair_mojibake(broken)=='録画'
    assert repair_mojibake('録画')=='録画'
    assert format_elapsed(0)=='00:00'
    assert format_elapsed(65)=='01:05'
    assert format_elapsed(3661)=='1:01:01'


def test_youtube_settings_and_worker_routing():
    s=channel_settings(Settings(),'youtube:@example',VIDEO)
    s.validate()
    assert s.url.endswith('watch?v='+VIDEO) and not s.live_from_start
    assert s.output_template.startswith('YouTube/%(channel_id)s/'+VIDEO)
    assert channel_settings(Settings(),'youtube:@example',VIDEO).output_template != s.output_template
    assert channel_settings(Settings(gpu_export='cuda'),'youtube:@example',VIDEO,catchup=True).live_from_start is True
    assert channel_settings(Settings(gpu_export='cuda'),'youtube:@example',VIDEO).gpu_export == 'off'
    assert channel_settings(Settings(),'youtube:@example').url.endswith('/@example/live')
    assert new_worker('youtube_probe').command[-1]=='--youtube-probe'
    assert new_worker('youtube_record',VIDEO).command[-2:]==['--youtube-watch-record',VIDEO]
    assert valid_broadcast_id('youtube:@example', VIDEO)
    assert not valid_broadcast_id('youtube:@example','1234')
    with pytest.raises(ValueError): channel_settings(Settings(),'youtube:@example','../x')


class Worker:
    def __init__(self): self.events=EventBuffer(); self.active=False; self.settings=None
    def start(self,settings): self.settings=settings;self.active=True
    def finish(self, status='completed', result=None):
        if result:self.events.put({'event':'probe_result',**result})
        self.events.put({'event':'done','status':status,'code':0 if status=='completed' else 1});self.active=False
    def stop(self): self.finish('cancelled')
    def force_stop(self): self.finish('forced')


def manager(names, cap=3, manual=()):
    now=[100.0]; workers=[]
    def factory(kind,identity):
        w=Worker();workers.append((kind,identity,w));return w
    m=WatchManager(factory,lambda:now[0],lambda:set(manual))
    m.configure(WatchConfig(tuple(WatchChannel(n) for n in names),max_recordings=cap),Settings())
    m.start();now[0]+=10;m.tick()
    return m,now,workers


def test_mixed_global_cap_and_fresh_video_identity():
    m,now,workers=manager(['example','youtube:@example'],cap=1)
    assert {kind for kind,_,_ in workers}=={'probe','youtube_probe'}
    for kind,_,w in workers:w.finish(result={'status':'live','stream_id':VIDEO if kind=='youtube_probe' else '123'})
    m.tick()
    assert len(m.recording_channels)==1 and m.states['youtube:@example'].status=='queued'
    m.states['example'].recording.worker.finish();m.tick()
    assert m.recording_channels=={'youtube:@example'}
    assert m.states['youtube:@example'].recording.worker.settings.url.endswith(VIDEO)


def test_alias_dedup_stop_and_retry():
    a,b='youtube:@example','youtube:channel/'+CHANNEL
    m,now,workers=manager([a,b])
    for _,_,w in workers:w.finish(result={'status':'live','stream_id':VIDEO})
    m.tick()
    assert len(m.recording_channels)==1 and m.states[b].status=='duplicate'
    m.stop_recording(a);m.tick()
    assert not m.recording_channels and m.states[b].status=='skipped'
    m.retry_channel(b);now[0]+=61;m.tick()
    for s in m.states.values():
        if s.probe:s.probe.worker.finish(result={'status':'live','stream_id':VIDEO})
    m.tick()
    assert b in m.recording_channels


def test_manual_video_exclusion_and_upcoming():
    m,now,workers=manager(['youtube:@example'],manual={broadcast_key('youtube:',VIDEO)})
    workers[0][2].finish(result={'status':'live','stream_id':VIDEO});m.tick()
    assert not m.recording_channels and m.states['youtube:@example'].status=='manual'
    now[0]+=61;m.tick();m.states['youtube:@example'].probe.worker.finish(result={'status':'upcoming'});m.tick()
    assert not m.states['youtube:@example'].online and m.states['youtube:@example'].status=='upcoming'


def test_paused_late_results_do_not_start():
    m,now,workers=manager(['youtube:@example'])
    m.stop();workers[0][2].finish(result={'status':'live','stream_id':VIDEO});m.tick()
    assert not m.recording_channels


def live_info(**kw):
    return dict({'id':VIDEO,'extractor_key':'Youtube','is_live':True,'live_status':'is_live','title':'Test'},**kw)


def test_probe_redirect_and_minimal_output():
    class Ydl:
        def extract_info(self,url,**kwargs):
            assert url=='https://www.youtube.com/watch?v='+VIDEO
            assert kwargs=={'download':False,'process':False}
            return live_info(formats=[{'url':'https://secret.invalid/?token=secret'}])
    result=resolve_live(Ydl(),{'_type':'url','ie_key':'Youtube','id':VIDEO,'url':'ignored'})
    assert result=={'status':'live','stream_id':VIDEO,'title':'Test'}
    assert 'secret' not in json.dumps(result)

@pytest.mark.parametrize('state,result',[('is_upcoming','upcoming'),('was_live','offline'),('post_live','offline'),('not_live','offline')])
def test_nonlive_is_not_recorded(state,result):
    assert resolve_live(None,live_info(live_status=state,is_live=False))=={'status':result}

@pytest.mark.parametrize('info',[{},live_info(live_status=None,is_live=None),live_info(id='../x'),{'_type':'url','ie_key':'Generic','id':VIDEO}, {'_type':'playlist','entries':[{}]}])
def test_unknown_probe_state_is_error(info):
    with pytest.raises(RuntimeError):resolve_live(None,info)


def test_playlist_bound():
    def entries():
        for _ in range(10):yield {'live_status':'was_live'}
        raise AssertionError('unbounded iteration')
    assert resolve_live(None,{'_type':'playlist','entries':entries()})=={'status':'offline'}


def test_background_store_atomic_and_no_corrupt_overwrite(tmp_path,monkeypatch):
    store=BackgroundStore(tmp_path/'settings.json')
    assert store.load()==BackgroundConfig()
    store.save(BackgroundConfig(True,True));assert store.load().start_hidden
    before=store.path.read_bytes()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr('livecatch_core.background.os.replace',fail)
    with pytest.raises(OSError):store.save(BackgroundConfig())
    assert store.path.read_bytes()==before and len(list(tmp_path.iterdir()))==1
    store.path.write_text('{broken')
    with pytest.raises(ValueError):store.save(BackgroundConfig())
    assert store.path.read_text()=='{broken'


def test_instance_lease_across_processes(tmp_path):
    lock=tmp_path/'lock'
    code='from pathlib import Path;from livecatch_core.background import InstanceLease;InstanceLease(Path('+repr(str(lock))+')).acquire()'
    with InstanceLease(lock):
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
        assert result.returncode!=0 and 'already running' in result.stderr
    with InstanceLease(lock): pass


class Registry:
    HKEY_CURRENT_USER=1;KEY_READ=2;KEY_SET_VALUE=3;REG_SZ=4
    def __init__(self):self.values={'Unrelated':'keep'}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def OpenKey(self,*args):return self
    CreateKeyEx=OpenKey
    def QueryValueEx(self,key,name):
        if name not in self.values:raise FileNotFoundError()
        return self.values[name],4
    def SetValueEx(self,key,name,reserved,kind,value):self.values[name]=value
    def DeleteValue(self,key,name):
        if name not in self.values:raise FileNotFoundError()
        del self.values[name]


def test_startup_is_opt_in_and_only_own_value():
    registry=Registry();startup=WindowsStartup(registry,'win32')
    assert not startup.enabled() and registry.values=={'Unrelated':'keep'}
    startup.set_enabled(True,'"C:\\Program Files\\LiveCatch.exe" --background')
    assert startup.enabled()
    startup.set_enabled(False)
    assert registry.values=={'Unrelated':'keep'}
    with pytest.raises(RuntimeError):WindowsStartup(platform='linux').set_enabled(True)


def test_startup_quoting(tmp_path):
    exe=tmp_path/'python with space.exe';exe.touch()
    script=tmp_path/'source folder'/'livecatch.py';script.parent.mkdir();script.touch()
    value=startup_command(exe,script,False)
    assert value.startswith('"') and value.endswith(' --background') and '" "' in value


def test_tray_ready_and_stop_without_tk():
    gate=Event()
    class Icon:
        visible=False
        def run(self,setup):setup(self);gate.wait(2)
        def stop(self):gate.set()
    tray=TrayController(lambda q,lang:Icon())
    tray.start();assert tray.ready.wait(1)
    assert tray.commands.get(timeout=1)[0]=='ready'
    tray.stop();tray.thread.join(1)
    assert not tray.ready.is_set() and not tray.thread.is_alive()


def test_tray_failure_reports_to_owner():
    def fail(*args):raise RuntimeError('missing tray')
    tray=TrayController(fail);tray.start()
    command,error=tray.commands.get(timeout=1)
    assert command=='failed' and 'missing tray' in error and not tray.ready.is_set()
    tray.stop()


def test_real_ytdlp_metadata_contract_without_network(monkeypatch):
    pytest.importorskip('yt_dlp')
    from yt_dlp.extractor.youtube import YoutubeIE, YoutubeTabIE
    from livecatch_core.youtube_watch_probe import probe_channel
    monkeypatch.setattr(YoutubeTabIE,'_real_extract',lambda self,url:{'_type':'url','ie_key':'Youtube','id':VIDEO,'url':'https://www.youtube.com/watch?v='+VIDEO})
    monkeypatch.setattr(YoutubeIE,'_real_extract',lambda self,url:live_info())
    result=probe_channel(Settings(url='https://www.youtube.com/@example'))
    assert result['stream_id']==VIDEO


def test_real_ytdlp_typed_offline_and_network_error(monkeypatch):
    pytest.importorskip('yt_dlp')
    from yt_dlp.extractor.youtube import YoutubeTabIE
    from yt_dlp.utils import UserNotLive, ExtractorError
    from livecatch_core.youtube_watch_probe import probe_channel
    def offline(*args):raise UserNotLive(video_id='example')
    monkeypatch.setattr(YoutubeTabIE,'_real_extract',offline)
    assert probe_channel(Settings(url='https://www.youtube.com/@example'))=={'status':'offline'}
    def failed(*args):raise ExtractorError('HTTP 429')
    monkeypatch.setattr(YoutubeTabIE,'_real_extract',failed)
    with pytest.raises(Exception):probe_channel(Settings(url='https://www.youtube.com/@example'))
