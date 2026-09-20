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


def contract(monkeypatch,tmp_path,info):
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
        return worker.record(Settings(url='https://youtube.com/watch?v='+VIDEO,save_dir=str(tmp_path)),Event(),lambda *a,**k:None,youtube_video_id=VIDEO)
    return observed,run,Error


def test_youtube_live_does_not_wait_or_fetch_archive(monkeypatch,tmp_path):
    observed,run,_=contract(monkeypatch,tmp_path,{'extractor_key':'Youtube','id':VIDEO,'is_live':True,'live_status':'is_live'})
    assert run()==0 and observed['started']
    assert observed['live_from_start'] is False and 'wait_for_video' not in observed

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
