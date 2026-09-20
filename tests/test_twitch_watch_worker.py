from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest
from livecatch_core.config import Settings
from livecatch_core import worker


def run_contract(monkeypatch, tmp_path, info, expected):
    observed = {}
    class PP:
        def __init__(self, *args): pass
    class PPError(Exception): pass
    class YDL:
        def __init__(self, options): observed.update(options); self.pre = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def add_post_processor(self, pp, when):
            if when == 'before_dl': self.pre.append(pp)
        def download(self, urls):
            for pp in self.pre: pp.run(info)
            observed['download_started'] = True
            return 0
    monkeypatch.setitem(sys.modules, 'yt_dlp', SimpleNamespace(YoutubeDL=YDL))
    monkeypatch.setitem(sys.modules, 'yt_dlp.postprocessor.common', SimpleNamespace(PostProcessor=PP))
    monkeypatch.setitem(sys.modules, 'yt_dlp.utils', SimpleNamespace(PostProcessingError=PPError))
    monkeypatch.setattr(worker, 'ffmpeg_stop_bridge', lambda *a,**k:nullcontext())
    monkeypatch.setattr(worker, 'fragment_patch', lambda *a,**k:nullcontext())
    monkeypatch.setattr(worker, 'find_tool', lambda x:'/fake/'+x)
    settings = Settings(url='https://twitch.tv/example', save_dir=str(tmp_path))
    def run(): return worker.record(settings, Event(), lambda *a,**k:None, twitch_stream_id=expected)
    return observed, run, PPError


def test_live_watch_removes_reservation_wait_and_vod_mode(monkeypatch, tmp_path):
    observed, run, _ = run_contract(monkeypatch, tmp_path,
        {'extractor_key':'TwitchStream', 'is_live':True, 'id':'1234'}, '1234')
    assert run() == 0 and observed['download_started']
    assert observed['live_from_start'] is False and 'wait_for_video' not in observed


@pytest.mark.parametrize('info', [
    {'extractor_key':'TwitchStream', 'is_live':True, 'id':'9999'},
    {'extractor_key':'TwitchStream', 'is_live':False, 'id':'1234'},
    {'extractor_key':'TwitchVod', 'is_live':True, 'id':'1234'},
])
def test_changed_broadcast_refused_before_download(monkeypatch, tmp_path, info):
    observed, run, error = run_contract(monkeypatch, tmp_path, info, '1234')
    with pytest.raises(error): run()
    assert not observed.get('download_started')


def test_legacy_recording_options_unchanged(monkeypatch, tmp_path):
    observed, run, _ = run_contract(monkeypatch, tmp_path, {'is_live':True}, None)
    assert run() == 0
    assert observed['live_from_start'] is True and observed['wait_for_video'] == (30,30)


def test_probe_worker_cli_dispatch_reports_invalid_input():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'livecatch_worker.py'), '--twitch-probe'],
        input=json.dumps(Settings(url='https://twitch.tv/videos/1234').to_dict())+'\n',
        text=True, capture_output=True, timeout=15, cwd=root)
    assert result.returncode == 1
    events=[json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1] == {'event':'done','status':'failed','code':1}
    assert any(e['event']=='error' for e in events)


def test_unknown_worker_arguments_are_rejected():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'livecatch_worker.py'),'--unknown'],
                          input='{}\n',text=True,capture_output=True,timeout=15,cwd=root)
    assert result.returncode==1 and 'Unknown worker arguments' in result.stdout
