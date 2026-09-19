from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import pytest

from livecatch_core.config import ConfigStore, Settings, DEFAULT_TEMPLATE, normalize_url
from livecatch_core.events import Emitter, EventBuffer, redact
from livecatch_core.options import ydl_options
from livecatch_core.ytdlp_patch import require_supported_version

@pytest.mark.parametrize('url', ['youtube.com/watch?v=abc','https://www.youtube.com/live/abc','https://youtu.be/abc','https://twitch.tv/example'])
def test_urls(url):
    assert normalize_url(url).startswith('https://')

@pytest.mark.parametrize('url', ['', 'file:///tmp/video','https://youtube.com.attacker.test/a','https://attacker.test/youtube.com','https://u:p@youtube.com/x','https://youtube.com:9999/x','javascript:alert(1)'])
def test_bad_urls(url):
    with pytest.raises(ValueError): normalize_url(url)

@pytest.mark.parametrize('changes', [dict(concurrent_fragments=0),dict(concurrent_fragments=128),dict(concurrent_fragments=True),dict(prefetch=0),dict(language='xx'),dict(gpu_export='magic'),dict(gpu_jobs=5),dict(export_height=17),dict(wait_seconds=1),dict(mode='catchup_stop',engine='stock'),dict(save_dir=''),dict(use_temp_dir=True,temp_dir='')])
def test_validation(changes):
    with pytest.raises(ValueError): replace(Settings(url='https://youtu.be/test'),**changes).validate()

@pytest.mark.parametrize('data', [[], {'wait_seconds':'30'}, {'cookies_from_browser':'true'}, {'concurrent_fragments':True}])
def test_config_types(data):
    with pytest.raises(ValueError): Settings.from_dict(data)

def test_migration_atomic_preserve(tmp_path):
    path=tmp_path/'settings.json'; store=ConfigStore(path)
    assert store.load()==Settings()
    path.write_text(json.dumps({'version':'2.1.3','future_setting':{'x':1},'concurrent_fragments':128,'output_template':DEFAULT_TEMPLATE.removeprefix('%(extractor_key)s/')}))
    cfg=store.load(); assert cfg.concurrent_fragments==32 and cfg.output_template==DEFAULT_TEMPLATE
    store.save(cfg); assert json.loads(path.read_text())['future_setting']=={'x':1}
    assert len(list(tmp_path.iterdir()))==1

def test_corrupt_config_not_overwritten(tmp_path):
    path=tmp_path/'settings.json';path.write_text('{bad')
    with pytest.raises(ValueError): ConfigStore(path).save(Settings())
    assert path.read_text()=='{bad'

def test_atomic_replace_failure(tmp_path,monkeypatch):
    path=tmp_path/'settings.json';path.write_text('{}')
    def fail(*a): raise OSError('disk error')
    monkeypatch.setattr('livecatch_core.config.os.replace',fail)
    with pytest.raises(OSError): ConfigStore(path).save(Settings())
    assert path.read_text()=='{}' and list(tmp_path.iterdir())==[path]

def test_api_options(tmp_path):
    cfg=Settings(url='https://youtu.be/test',save_dir=str(tmp_path),cookies_from_browser=True,use_temp_dir=True,temp_dir=str(tmp_path/'cache'))
    opts=ydl_options(cfg,'/tools/ffmpeg')
    assert opts['wait_for_video']==(30,30) and opts['cookiesfrombrowser']==('chrome',)
    assert opts['paths']['temp']==str(tmp_path/'cache')
    assert opts['skip_unavailable_fragments'] is False and opts['overwrites'] is False
    assert opts['postprocessors'][0]['preferedformat']=='mp4'
    assert opts['retry_sleep_functions']['fragment'](100)==30
    opts=ydl_options(replace(cfg,mode='catchup_stop',lightweight_catchup_postprocess=True))
    assert 'wait_for_video' not in opts and len(opts['postprocessors'])==1
    assert opts['live_from_start']

def test_event_buffer_retains_completion():
    buf=EventBuffer(3)
    buf.put({'event':'done','code':0})
    for i in range(100):buf.put({'event':'log','i':i})
    assert [e.get('i') for e in buf.drain(2)]==[97,98]
    assert [e['event'] for e in buf.drain(2)]==['log','done']
    assert not buf.drain()

def test_emitter_redacts_and_throttles():
    out=StringIO();emit=Emitter(out)
    emit('warning',message='https://host/file?token=secret')
    for _ in range(100):emit('fragment',stream='video',current=2)
    events=[json.loads(x) for x in out.getvalue().splitlines()]
    assert len(events)==2 and 'secret' not in out.getvalue()
    assert redact('no URL')=='no URL'

@pytest.mark.parametrize('version', ['2026.08.19','2026.8.19'])
def test_reviewed_versions(version): require_supported_version(version)

@pytest.mark.parametrize('version', ['2026.08.20','2026.08.19.dev0','junk','2025.1.1'])
def test_unreviewed_versions(version):
    with pytest.raises(RuntimeError):require_supported_version(version)
