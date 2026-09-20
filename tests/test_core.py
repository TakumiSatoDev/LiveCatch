from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import shutil
import zipfile
from types import SimpleNamespace
import pytest

from livecatch_core.config import ConfigStore, Settings, DEFAULT_TEMPLATE, LEGACY_DEFAULT_TEMPLATE_V2, LEGACY_DEFAULT_TEMPLATE_V3, normalize_url
from livecatch_core.events import Emitter, EventBuffer, redact
from livecatch_core.options import ydl_options
from livecatch_core.updates import UpdateCheck, check_for_update, download_update, is_newer
from livecatch_core.ytdlp_patch import require_supported_version
import livecatch_updater
from livecatch_updater import _install, _install_with_retry

@pytest.mark.parametrize('url', ['youtube.com/watch?v=abc','https://www.youtube.com/live/abc','https://youtu.be/abc','https://twitch.tv/example'])
def test_urls(url):
    assert normalize_url(url).startswith('https://')

@pytest.mark.parametrize('url', ['', 'file:///tmp/video','https://youtube.com.attacker.test/a','https://attacker.test/youtube.com','https://u:p@youtube.com/x','https://youtube.com:9999/x','javascript:alert(1)'])
def test_bad_urls(url):
    with pytest.raises(ValueError): normalize_url(url)

@pytest.mark.parametrize('changes', [dict(concurrent_fragments=0),dict(concurrent_fragments=129),dict(concurrent_fragments=True),dict(prefetch=0),dict(prefetch=9),dict(language='xx'),dict(gpu_export='magic'),dict(gpu_jobs=9),dict(gpu_preset='warp'),dict(export_height=17),dict(wait_seconds=1),dict(mode='catchup_stop',engine='stock'),dict(save_dir=''),dict(use_temp_dir=True,temp_dir='')])
def test_validation(changes):
    with pytest.raises(ValueError): replace(Settings(url='https://youtu.be/test'),**changes).validate()

@pytest.mark.parametrize('data', [[], {'wait_seconds':'30'}, {'cookies_from_browser':'true'}, {'concurrent_fragments':True}])
def test_config_types(data):
    with pytest.raises(ValueError): Settings.from_dict(data)

def test_legacy_gpu_export_is_disabled_on_load():
    cfg=Settings.from_dict({'gpu_export':'cuda','gpu_jobs':8,'gpu_preset':'max_speed'})
    assert cfg.gpu_export=='off'


def test_extreme_limits_are_explicitly_allowed():
    replace(Settings(url='https://youtu.be/test'), concurrent_fragments=128, prefetch=8,
            gpu_jobs=8, gpu_preset='max_speed').validate()

def test_migration_atomic_preserve(tmp_path):
    path=tmp_path/'settings.json'; store=ConfigStore(path)
    assert store.load()==Settings()
    path.write_text(json.dumps({'version':'2.1.3','future_setting':{'x':1},'concurrent_fragments':128,'output_template':LEGACY_DEFAULT_TEMPLATE_V2}))
    cfg=store.load(); assert cfg.concurrent_fragments==32 and cfg.output_template==DEFAULT_TEMPLATE
    store.save(cfg); assert json.loads(path.read_text())['future_setting']=={'x':1}
    assert len(list(tmp_path.iterdir()))==1

def test_current_default_template_migrates_to_channel_layout():
    cfg=Settings.from_dict({'output_template':LEGACY_DEFAULT_TEMPLATE_V3})
    assert cfg.output_template==DEFAULT_TEMPLATE
    assert DEFAULT_TEMPLATE.startswith('%(uploader_id)s/')


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
    assert opts['outtmpl'].startswith('YouTube/%(uploader_id)s/')
    twitch=ydl_options(replace(cfg,url='https://twitch.tv/example'),'C:/tools/ffmpeg.exe')
    assert twitch['outtmpl'].startswith('Twitch/%(uploader_id)s/')
    custom=ydl_options(replace(cfg,output_template='custom/%(title)s.%(ext)s'))
    assert custom['outtmpl']=='custom/%(title)s.%(ext)s'
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


@pytest.mark.parametrize(('current','latest','expected'), [
    ('3.0.0-dev1', '3.0.0-dev2', True),
    ('3.0.0-dev2', '3.0.0', True),
    ('3.0.0', '3.0.0-dev3', False),
    ('3.0.0', '3.0.0', False),
])
def test_update_version_order(current, latest, expected):
    assert is_newer(current, latest) is expected


def test_check_for_update(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit): return b'__version__ = "3.0.0-dev2"\n'

    monkeypatch.setattr("livecatch_core.updates.urlopen", lambda *args, **kwargs: Response())
    result = check_for_update("3.0.0-dev1")
    assert isinstance(result, UpdateCheck)
    assert result.latest_version == "3.0.0-dev2" and result.update_available
    assert result.url.endswith("/releases/latest")


def test_check_for_update_reads_release_package(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _limit):
            return json.dumps({
                "tag_name": "v3.0.3",
                "html_url": "https://github.com/TakumiSatoDev/LiveCatch/releases/tag/v3.0.3",
                "assets": [{
                    "name": "LiveCatch-Update-3.0.3.zip",
                    "browser_download_url": "https://github.com/TakumiSatoDev/LiveCatch/releases/download/v3.0.3/LiveCatch-Update-3.0.3.zip",
                }],
            }).encode()

    monkeypatch.setattr("livecatch_core.updates.urlopen", lambda *args, **kwargs: Response())
    result = check_for_update("3.0.2")
    assert result.latest_version == "3.0.3" and result.update_available
    assert result.download_url.endswith("LiveCatch-Update-3.0.3.zip")


def test_download_update_validates_and_extracts_payload(monkeypatch, tmp_path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for name in ("LiveCatch.exe", "LiveCatchWorker.exe", "LiveCatchUpdater.exe"):
            package.writestr(name, b"MZ")
        package.writestr("tools/ffmpeg.exe", b"MZ")
    data = archive.read_bytes()

    class Response:
        def __init__(self): self.offset = 0
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, limit):
            chunk = data[self.offset:self.offset + limit]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr("livecatch_core.updates.urlopen", lambda *args, **kwargs: Response())
    payload = download_update(UpdateCheck("3.0.2", "3.0.3", True, download_url="https://example.test/update.zip"))
    try:
        assert (payload / "LiveCatch.exe").is_file()
        assert (payload / "LiveCatchUpdater.exe").is_file()
        assert (payload / "tools" / "ffmpeg.exe").is_file()
    finally:
        shutil.rmtree(payload.parent, ignore_errors=True)


def test_updater_replaces_installed_files(tmp_path):
    target = tmp_path / "installed"
    payload = tmp_path / "payload"
    (target / "tools").mkdir(parents=True)
    (payload / "tools").mkdir(parents=True)
    for name in ("LiveCatch.exe", "LiveCatchWorker.exe", "LiveCatchUpdater.exe"):
        (target / name).write_bytes(b"old")
        (payload / name).write_bytes(b"new")
    (target / "tools" / "ffmpeg.exe").write_bytes(b"old-tool")
    (payload / "tools" / "ffmpeg.exe").write_bytes(b"new-tool")
    _install(payload, target)
    assert (target / "LiveCatch.exe").read_bytes() == b"new"
    assert (target / "tools" / "ffmpeg.exe").read_bytes() == b"new-tool"


def test_updater_retries_windows_sharing_violation(monkeypatch, tmp_path):
    target = tmp_path / "installed"
    payload = tmp_path / "payload"
    target.mkdir()
    payload.mkdir()
    (target / "LiveCatch.exe").write_bytes(b"old")
    (payload / "LiveCatch.exe").write_bytes(b"new")
    (payload / "LiveCatchWorker.exe").write_bytes(b"worker")

    attempts = []
    original_install = livecatch_updater._install

    def flaky_install(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            error = PermissionError(32, "sharing violation")
            error.winerror = 32
            raise error
        original_install(source, destination)

    real_os = livecatch_updater.os
    fake_os = SimpleNamespace(name="nt", replace=real_os.replace)
    monkeypatch.setattr(livecatch_updater, "os", fake_os)
    monkeypatch.setattr(livecatch_updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(livecatch_updater, "_install", flaky_install)
    _install_with_retry(payload, target, timeout=1)
    assert len(attempts) == 3
    assert (target / "LiveCatch.exe").read_bytes() == b"new"
