from contextlib import nullcontext
from pathlib import Path
import json
import os
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest
from livecatch_core.config import Settings
from livecatch_core import worker

VIDEO='AbCdEf_-123'


def contract(monkeypatch,tmp_path,info,*,live_from_start=False,youtube_recovery=False):
    observed={}
    class PP:
        def __init__(self,*args):pass
    class Error(Exception):pass
    class YDL:
        def __init__(self,options):observed.update(options);self.pre=[]
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def add_post_processor(self,pp,when):
            if when=='before_dl':self.pre.append(pp)
        def download(self,urls):
            for pp in self.pre:pp.run(info)
            observed['started']=True
            return 0
    monkeypatch.setitem(sys.modules,'yt_dlp',SimpleNamespace(YoutubeDL=YDL))
    monkeypatch.setitem(sys.modules,'yt_dlp.postprocessor.common',SimpleNamespace(PostProcessor=PP))
    monkeypatch.setitem(sys.modules,'yt_dlp.utils',SimpleNamespace(PostProcessingError=Error))
    monkeypatch.setattr(worker,'ffmpeg_stop_bridge',lambda *a,**k:nullcontext())
    monkeypatch.setattr(worker,'fragment_patch',lambda *a,**k:nullcontext())
    monkeypatch.setattr(worker,'find_tool',lambda name:'/fake/'+name)
    def run():
        return worker.record(Settings(
            url='https://youtube.com/watch?v='+VIDEO, save_dir=str(tmp_path),
            live_from_start=live_from_start), Event(), lambda *a,**k:None,
            youtube_video_id=VIDEO, youtube_recovery=youtube_recovery)
    return observed,run,Error


def test_youtube_live_does_not_wait_or_fetch_archive(monkeypatch,tmp_path):
    observed,run,_=contract(monkeypatch,tmp_path,{'extractor_key':'Youtube','id':VIDEO,'is_live':True,'live_status':'is_live'})
    assert run()==0 and observed['started']
    assert observed['live_from_start'] is False and 'wait_for_video' not in observed


def test_youtube_recovery_uses_live_edge_low_parallel_combined_format(monkeypatch,tmp_path):
    observed,run,_=contract(
        monkeypatch,tmp_path,
        {'extractor_key':'Youtube','id':VIDEO,'is_live':True,'live_status':'is_live'},
        live_from_start=True,youtube_recovery=True)
    assert run()==0 and observed['started']
    assert observed['live_from_start'] is False
    assert observed['concurrent_fragment_downloads']==4
    assert observed['fragment_retries']==1
    assert observed['format'].startswith('b[height<=1080]')


def test_youtube_live_can_catch_up_from_available_dvr(monkeypatch,tmp_path):
    observed,run,_=contract(
        monkeypatch,tmp_path,
        {'extractor_key':'Youtube','id':VIDEO,'is_live':True,'live_status':'is_live'},
        live_from_start=True)
    assert run()==0 and observed['started']
    assert observed['live_from_start'] is True and 'wait_for_video' not in observed

@pytest.mark.parametrize('changes',[{'id':'OtherId_123'}, {'is_live':False}, {'live_status':'is_upcoming'}, {'live_status':'post_live'}, {'extractor_key':'Generic'}])
def test_changed_or_ended_youtube_broadcast_rejected(monkeypatch,tmp_path,changes):
    observed,run,error=contract(monkeypatch,tmp_path,dict({'extractor_key':'Youtube','id':VIDEO,'is_live':True,'live_status':'is_live'},**changes))
    with pytest.raises(error):run()
    assert not observed.get('started')


def test_youtube_probe_rejects_video_url_before_network():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'livecatch_worker.py'),'--youtube-probe'],input=json.dumps(Settings(url='https://youtube.com/watch?v='+VIDEO).to_dict())+'\n',text=True,capture_output=True,cwd=root,timeout=15)
    assert result.returncode==1
    assert json.loads(result.stdout.splitlines()[-1])['status']=='failed'


def test_headless_help_does_not_need_a_display():
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,DISPLAY='',PYSTRAY_BACKEND='unavailable')
    result=subprocess.run([sys.executable,'-m','livecatch_core','watch','--help'],text=True,capture_output=True,cwd=root,env=env,timeout=15)
    assert result.returncode==0 and '--watch-config' in result.stdout and '--allow-extreme' in result.stdout


def test_youtube_auth_storm_requests_recovery_once(monkeypatch,tmp_path):
    events=[]; observed={}
    class PP:
        def __init__(self,*args):pass
    class Error(Exception):pass
    class YDL:
        def __init__(self,options): observed.update(options);self.pre=[]
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def add_post_processor(self,pp,when):
            if when=='before_dl':self.pre.append(pp)
        def download(self,urls):
            for pp in self.pre:pp.run(
                {'extractor_key':'Youtube','id':VIDEO,'is_live':True,'live_status':'is_live'})
            logger=observed['logger']
            for _ in range(8):
                logger.debug('[download] Got error: HTTP Error 401: Unauthorized. Giving up after 3 retries')
            raise KeyboardInterrupt()
    monkeypatch.setitem(sys.modules,'yt_dlp',SimpleNamespace(YoutubeDL=YDL))
    monkeypatch.setitem(sys.modules,'yt_dlp.postprocessor.common',SimpleNamespace(PostProcessor=PP))
    monkeypatch.setitem(sys.modules,'yt_dlp.utils',SimpleNamespace(PostProcessingError=Error))
    monkeypatch.setattr(worker,'ffmpeg_stop_bridge',lambda *a,**k:nullcontext())
    monkeypatch.setattr(worker,'fragment_patch',lambda *a,**k:nullcontext())
    monkeypatch.setattr(worker,'find_tool',lambda name:'/fake/'+name)
    with pytest.raises(RuntimeError,match='recovery retry requested'):
        worker.record(
            Settings(url='https://youtube.com/watch?v='+VIDEO,save_dir=str(tmp_path),live_from_start=True),
            Event(),lambda kind,**data:events.append({'event':kind,**data}),youtube_video_id=VIDEO)
    assert len([e for e in events if e['event']=='youtube_recovery'])==1
    warnings=[e for e in events if e['event']=='warning']
    assert any('401/403' in e['message'] for e in warnings)
    assert len([e for e in warnings if '401/403' in e['message']])==1
