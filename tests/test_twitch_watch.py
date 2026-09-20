from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from livecatch_core.config import Settings
from livecatch_core.events import EventBuffer
from livecatch_core.supervisor import Supervisor
from livecatch_core.twitch_watch import (
    WatchChannel, WatchConfig, WatchStore, WatchManager, channel_settings,
    normalize_channel, parse_channels, MAX_PROBES, PROBE_TIMEOUT,
)
from livecatch_core.twitch_watch_probe import _is_offline, probe_channel


@pytest.mark.parametrize(('value', 'expected'), [
    ('Some_Name', 'some_name'), ('@Example', 'example'), (' twitch.tv/Foo/ ', 'foo'),
    ('https://www.twitch.tv/Name?ref=front', 'name'), ('https://m.twitch.tv/name', 'name'),
])
def test_channels(value, expected):
    assert normalize_channel(value) == expected


@pytest.mark.parametrize('value', ['', None, 'https://twitch.tv/videos/1234',
    'https://evil.test/name', 'https://twitch.tv.evil.test/name', 'https://u:p@twitch.tv/name',
    'https://twitch.tv:123/name', 'https://clips.twitch.tv/a', 'https://twitch.tv/foo/clip/abc',
    '../hello', 'directory', 'https://twitch.tv/directory', 'a'*26, 'foo-bar', 'file:///foo',
])
def test_bad_channels(value):
    with pytest.raises(ValueError): normalize_channel(value)


def test_bulk_normalizes_and_deduplicates():
    assert parse_channels('A b, https://twitch.tv/a\n@C;d、e') == ('a', 'b', 'c', 'd', 'e')


@pytest.mark.parametrize('changes', [
    {'interval': 29}, {'interval': True}, {'interval': 3601}, {'max_recordings': 9},
    {'max_recordings': False}, {'autostart': 'yes'}, {'monitor_preset': 'warp'},
    {'catchup_mode': 'archive'}, {'schema_version': 2},
    {'channels': [WatchChannel('a')]}, {'channels': (WatchChannel('A'),)},
    {'channels': (WatchChannel('a'), WatchChannel('a'))},
    {'channels': (WatchChannel('a', 'yes'),)},
    {'channels': tuple(WatchChannel(f'a{i}') for i in range(51))},
])
def test_bad_config(changes):
    with pytest.raises(ValueError): replace(WatchConfig(), **changes).validate()


def test_store_roundtrip_preserves_unknown(tmp_path):
    path = tmp_path/'watch.json'
    store = WatchStore(path)
    assert store.load() == WatchConfig()
    path.write_text(json.dumps({'channels': [], 'future': {'foo': 1}}))
    cfg = WatchConfig((WatchChannel('aaa'), WatchChannel('bbb', False)), 90, 4, True)
    store.save(cfg)
    assert store.load() == cfg
    assert json.loads(path.read_text())['future'] == {'foo': 1}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('text', ['{bad', '[]', '{"schema_version": 2}'])
def test_corrupt_store_is_never_overwritten(tmp_path, text):
    path = tmp_path/'watch.json'
    path.write_text(text)
    with pytest.raises(ValueError): WatchStore(path).save(WatchConfig())
    assert path.read_text() == text


def test_store_replace_failure_keeps_original(tmp_path, monkeypatch):
    path = tmp_path/'watch.json'; path.write_text('{}')
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr('livecatch_core.twitch_watch.os.replace', fail)
    with pytest.raises(OSError): WatchStore(path).save(WatchConfig())
    assert path.read_text() == '{}' and list(tmp_path.iterdir()) == [path]


def test_recording_settings_unique_and_not_vod():
    base = Settings(mode='catchup_stop', gpu_export='cuda', gpu_preset='max_speed', use_temp_dir=True)
    a = channel_settings(base, 'foo', '12345')
    b = channel_settings(base, 'foo', '12345')
    assert a.mode == 'reservation' and a.live_from_start is False
    assert a.gpu_export == 'off' and a.gpu_preset == 'max_speed'
    assert a.save_dir == base.save_dir and a.use_temp_dir
    assert a.output_template.startswith('Twitch/foo/12345_')
    assert a.output_template != b.output_template
    assert '%(ext)s' in a.output_template and base.mode == 'catchup_stop'
    with pytest.raises(ValueError): channel_settings(base, 'foo', '../bad')


class FakeWorker:
    def __init__(self, kind, stream_id):
        self.kind, self.stream_id = kind, stream_id
        self.events = EventBuffer()
        self.active = False
        self.stopped = False
        self.forced = False
    def start(self, settings):
        self.settings = settings
        self.active = True
    def stop(self):
        self.stopped = True
    def force_stop(self):
        self.forced = True
        self.finish('forced')
    def finish(self, status='completed', result=None):
        if result is not None:
            self.events.put({'event': 'probe_result', **result})
        self.active = False
        self.events.put({'event': 'done', 'status': status, 'code': 0 if status == 'completed' else 1})


class Harness:
    def __init__(self, count=1, limit=3, manual=()):
        self.now = 0.0
        self.workers = []
        self.manual = set(manual)
        self.manager = WatchManager(factory=self.factory, clock=lambda: self.now, manual_channels=lambda: self.manual)
        self.config = WatchConfig(tuple(WatchChannel(f'channel{i}') for i in range(count)), max_recordings=limit)
        self.manager.configure(self.config, Settings())
        self.manager.start()
        self.manager.tick()
    def factory(self, kind, stream_id=None):
        w = FakeWorker(kind, stream_id)
        self.workers.append(w)
        return w
    def tick(self, delta=0):
        self.now += delta
        self.manager.tick()
    def live(self, index=0, stream_id='1234'):
        s = self.manager.states[f'channel{index}']
        assert s.probe is not None
        s.probe.worker.finish(result={'status': 'live', 'stream_id': stream_id, 'title': 'Example'})
        self.tick()
        return s
    def join_killers(self):
        for s in self.manager.states.values():
            for job in (s.probe, s.recording):
                if job and job.killer: job.killer.join(timeout=1)
        self.tick()


def test_offline_then_live_then_same_broadcast_no_duplicate():
    h = Harness(); s = h.manager.states['channel0']
    s.probe.worker.finish(result={'status': 'offline'}); h.tick()
    assert s.status == 'offline' and not h.manager.recording_channels
    h.tick(60); h.live()
    assert h.manager.recording_channels == {'channel0'}
    for _ in range(100): h.tick(1)
    assert len([w for w in h.workers if w.kind == 'record']) == 1
    s.recording.worker.finish(); h.tick()
    h.tick(60); h.live()
    assert not s.recording and s.status == 'skipped'
    h.tick(60); h.live(stream_id='1235')
    assert s.recording and s.recording.worker.stream_id == '1235'


def test_capacity_and_queue():
    h = Harness(count=4, limit=2)
    h.tick(1)
    assert sum(s.probe is not None for s in h.manager.states.values()) == MAX_PROBES
    s0 = h.live(0, '100'); h.tick(); s1 = h.live(1, '101')
    h.tick(); h.live(2, '102'); h.tick(); s3 = h.live(3, '103')
    assert len(h.manager.recording_channels) == 2 and s3.status == 'queued'
    s0.recording.worker.finish(); h.tick()
    assert len(h.manager.recording_channels) == 2
    assert 'channel2' in h.manager.recording_channels


def test_stale_live_result_is_discarded_after_pause():
    h = Harness(); s = h.manager.states['channel0']; w = s.probe.worker
    h.manager.stop(); h.join_killers()
    w.finish(result={'status':'live', 'stream_id':'321', 'title':'late'})
    h.tick(100)
    assert not h.manager.recording_channels and not h.manager.running
    h.manager.start(); h.tick()
    assert s.probe is not None and not s.online


def test_pause_keeps_recording_and_shutdown_stops_it():
    h = Harness(); s = h.live(); w = s.recording.worker
    h.manager.stop()
    assert w.active and not w.stopped
    h.manager.shutdown()
    assert w.stopped and s.suppressed_id == '1234'
    w.finish('cancelled'); h.tick()
    assert not h.manager.has_children


def test_manual_stop_suppresses_same_id_until_explicit_retry():
    h = Harness(); s = h.live(); w = s.recording.worker
    h.manager.stop_recording('channel0'); w.finish('cancelled'); h.tick()
    h.tick(60); h.live()
    assert s.status == 'skipped' and not s.recording
    h.manager.retry_channel('channel0'); h.tick(); h.live()
    assert s.recording and s.attempts == 1
    with pytest.raises(ValueError): h.manager.retry_channel('channel0')


def test_manual_recording_exclusion():
    h = Harness(manual=('channel0',)); s = h.live()
    assert s.status == 'manual' and not s.recording
    h.manual.clear(); h.tick()
    assert s.recording


def test_retry_limit_persists_across_transient_offline():
    h = Harness(); s = h.live()
    for i in range(3):
        assert s.attempts == i+1
        s.recording.worker.finish('failed'); h.tick()
        h.tick(60)
        s.probe.worker.finish(result={'status': 'offline'}); h.tick()
        h.tick(60); h.live()
        if s.recording is None and i < 2:
            h.tick(120)
            if s.probe: h.live()
    assert s.attempts == 3 and s.recording is None
    assert s.status == 'retry_limit'
    h.tick(60); h.live(stream_id='9999')
    assert s.attempts == 1 and s.recording


def test_probe_failure_not_offline_and_backoff():
    h = Harness(); s = h.manager.states['channel0']
    s.probe.worker.events.put({'event': 'error', 'message': 'HTTP 429'})
    s.probe.worker.finish('failed'); h.tick()
    assert s.status == 'check_error' and s.next_check == 60
    h.tick(60); s.probe.worker.finish('failed'); h.tick()
    assert s.next_check == 180 and s.check_errors == 2
    h.tick(60); assert s.probe is None


def test_timeout_kills_only_probe():
    h = Harness(count=2); s0 = h.live(0); h.tick(1)
    probe = h.manager.states['channel1'].probe.worker
    h.tick(PROBE_TIMEOUT+1); h.join_killers()
    assert probe.forced and s0.recording.worker.active
    assert h.manager.states['channel1'].status == 'check_error'


def test_no_new_work_when_not_monitoring():
    h = Harness(); h.manager.stop(); h.join_killers()
    before = len(h.workers); h.tick(10000)
    assert len(h.workers) == before


def test_disabled_channels_and_config_edit_rules():
    h = Harness(); s = h.live()
    with pytest.raises(ValueError): h.manager.configure(h.config, Settings())
    h.manager.stop()
    with pytest.raises(ValueError): h.manager.configure(WatchConfig(), Settings())
    h.manager.configure(replace(h.config, channels=(WatchChannel('channel0', False),)), Settings())
    assert s.recording and not h.manager.config.channels[0].enabled
    with pytest.raises(ValueError): h.manager.start()


def test_missing_terminal_does_not_release_a_job():
    h = Harness(); s = h.live(); w = s.recording.worker
    w.active = False; h.tick()
    assert s.recording is not None
    w.finish(); h.tick()
    assert s.recording is None


def test_probe_result_not_used_until_successful_exit():
    h = Harness(); s = h.manager.states['channel0']; w=s.probe.worker
    w.events.put({'event':'probe_result','status':'live','stream_id':'123'})
    h.tick(); assert s.recording is None
    w.finish('failed'); h.tick()
    assert s.recording is None and s.status == 'check_error'


def fake_ytdlp(monkeypatch, result=None, error=None):
    class UserNotLive(Exception): pass
    observed = {}
    class YDL:
        def __init__(self, options): observed.update(options)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, **kwargs):
            observed['url'], observed['kwargs'] = url, kwargs
            if error: raise error(UserNotLive)
            return result
    monkeypatch.setitem(sys.modules, 'yt_dlp', SimpleNamespace(YoutubeDL=YDL))
    monkeypatch.setitem(sys.modules, 'yt_dlp.utils', SimpleNamespace(UserNotLive=UserNotLive))
    return observed


def test_probe_only_metadata_and_allowlisted_output(monkeypatch):
    observed = fake_ytdlp(monkeypatch, {'id':'123','is_live':True, 'extractor_key':'TwitchStream',
        'description':'My title', 'url':'https://secret.test?token=secret', 'http_headers':{'Cookie':'secret'}})
    result = probe_channel(Settings(url='https://twitch.tv/foo', cookies_from_browser=True))
    assert result == {'status':'live','stream_id':'123','title':'My title'}
    assert observed['kwargs'] == {'download': False, 'process': False}
    assert observed['live_from_start'] is False and 'wait_for_video' not in observed
    assert observed['cookiesfrombrowser'] == ('chrome',)


def test_typed_offline_but_not_generic_errors(monkeypatch):
    fake_ytdlp(monkeypatch, error=lambda E: E('offline'))
    assert probe_channel(Settings(url='https://twitch.tv/foo')) == {'status':'offline'}
    fake_ytdlp(monkeypatch, error=lambda E: RuntimeError('offline server error'))
    with pytest.raises(RuntimeError): probe_channel(Settings(url='https://twitch.tv/foo'))


def test_wrapped_offline_type():
    class Offline(Exception): pass
    e = RuntimeError(); e.exc_info = (Offline, Offline(), None)
    assert _is_offline(e, Offline)
    other = RuntimeError(); other.__cause__=other
    assert not _is_offline(other, Offline)


@pytest.mark.parametrize('result', [None, {'is_live':False}, {'is_live':True,'id':'foo'},
    {'is_live':True,'id':'123','extractor_key':'TwitchVod'}])
def test_probe_rejects_unconfirmed_or_wrong_identity(monkeypatch, result):
    fake_ytdlp(monkeypatch, result)
    with pytest.raises(RuntimeError): probe_channel(Settings(url='https://twitch.tv/foo'))


def test_real_supervisor_monitor_roundtrip(tmp_path):
    helper = tmp_path/'fake_watch_worker.py'
    helper.write_text('''import json,sys
s=json.loads(sys.stdin.readline())
if sys.argv[1]=='probe':
 print(json.dumps({'event':'probe_result','status':'live','stream_id':'123','title':'synthetic'}),flush=True)
 print(json.dumps({'event':'done','status':'completed','code':0}),flush=True)
else:
 for line in sys.stdin:
  if json.loads(line).get('command')=='stop': break
 print(json.dumps({'event':'done','status':'cancelled','code':130}),flush=True)
''')
    def factory(kind, _stream=None): return Supervisor([sys.executable, '-u', str(helper), kind])
    m = WatchManager(factory=factory)
    m.configure(WatchConfig((WatchChannel('example'),)), Settings())
    try:
        m.start()
        deadline=time.monotonic()+5
        while not m.recording_channels and time.monotonic()<deadline:
            m.tick(); time.sleep(.01)
        assert m.recording_channels == {'example'}
        m.shutdown()
        deadline=time.monotonic()+5
        while m.has_children and time.monotonic()<deadline:
            m.tick(); time.sleep(.01)
        assert not m.has_children and m.states['example'].suppressed_id == '123'
    finally:
        m.shutdown(force=True)


def test_real_ytdlp_offline_wrapper(monkeypatch):
    pytest.importorskip('yt_dlp')
    from yt_dlp.extractor.twitch import TwitchStreamIE
    from yt_dlp.utils import UserNotLive
    def offline(self, url): raise UserNotLive(video_id='example')
    monkeypatch.setattr(TwitchStreamIE, '_real_extract', offline)
    assert probe_channel(Settings(url='https://twitch.tv/example')) == {'status':'offline'}


def test_real_ytdlp_live_identity(monkeypatch):
    pytest.importorskip('yt_dlp')
    from yt_dlp.extractor.twitch import TwitchStreamIE
    monkeypatch.setattr(TwitchStreamIE, '_real_extract', lambda self,url: {
        'id':'1234','title':'synthetic','is_live':True,'formats':[]})
    assert probe_channel(Settings(url='https://twitch.tv/example'))['stream_id']=='1234'


def test_oldest_due_checks_are_not_starved():
    h = Harness(count=12)
    for _ in range(12):
        for s in h.manager.states.values():
            if s.probe:
                s.probe.worker.finish(result={'status':'offline'})
        h.tick(40)
    checked = {normalize_channel(w.settings.url) for w in h.workers if w.kind == 'probe'}
    assert checked == set(h.manager.states)
    assert sum(s.probe is not None for s in h.manager.states.values()) <= MAX_PROBES


def test_factory_failure_counts_towards_recording_attempt_limit():
    h = Harness()
    original = h.factory
    def failing(kind, stream_id=None):
        if kind == 'record': raise OSError('Cannot start process')
        return original(kind, stream_id)
    h.manager.factory = failing
    s = h.live()
    for _ in range(5):
        h.tick(60)
        if s.probe: h.live()
    assert s.attempts == 3 and s.recording is None and s.status == 'retry_limit'


def test_recording_progress_tracks_phase_fragments_and_speed():
    h=Harness();s=h.live();w=s.recording.worker
    w.events.put({'event':'phase','name':'downloading'})
    w.events.put({'event':'progress','stream':'video','percent':42.5,
                  'fragment_index':85,'fragment_count':200,'speed':8*1024*1024})
    w.events.put({'event':'fragment','stream':'audio','current':40,'total':100,'bytes':4096})
    h.tick()
    assert s.status=='recording' and s.phase=='downloading'
    assert s.progress['video']['percent']==42.5
    assert s.progress['video']['fragment_index']==85
    assert s.progress['audio']['percent']==40.0
    w.events.put({'event':'phase','name':'postprocessing'});h.tick()
    assert s.status=='postprocessing'


def test_live_addition_is_checked_immediately_and_elapsed_is_kept():
    now=[10.0]; workers=[]
    def factory(kind, identity=None):
        worker=FakeWorker(kind, identity);workers.append(worker);return worker
    manager=WatchManager(factory=factory, clock=lambda:now[0])
    config=WatchConfig((WatchChannel('channel0'),), catchup_mode='from_start')
    manager.configure(config, Settings())
    manager.start();manager.tick()
    manager.states['channel0'].probe.worker.finish(result={'status':'offline'});manager.tick()

    manager.add_channels((WatchChannel('channel1'),), Settings())
    manager.tick()
    added=manager.states['channel1']
    assert added.probe is not None
    added.probe.worker.finish(result={'status':'live','stream_id':'4321','title':'late add'})
    manager.tick()
    assert added.recording is not None
    assert added.recording.worker.settings.live_from_start is True
    now[0]+=65
    assert manager.elapsed_seconds('channel1')==65
    added.recording.worker.finish();manager.tick()
    assert added.last_elapsed==65
    assert manager.elapsed_seconds('channel1')==65
