from io import StringIO
import json
from threading import Event

import pytest

from livecatch_core.events import Emitter, EventBuffer
from livecatch_core.fragment_telemetry import FragmentTelemetry
from livecatch_core.progress import (new_progress, apply_event, current_percent,
                                     catchup_percent, progress_text, stream_key)


def state():
    p = new_progress(active=True, now=0)
    apply_event(p, {'event': 'phase', 'name': 'downloading'}, now=1)
    return p


def test_network_and_writer_share_a_single_stream():
    p = state()
    apply_event(p, {'event': 'progress', 'stream': '137', 'percent': 25}, now=2)
    apply_event(p, {'event': 'fragment', 'stream': '0:137', 'current': 5, 'total': 10}, now=3)
    assert list(p['streams']) == ['137']
    assert current_percent(p) == 50


def test_old_throttled_byte_deltas_are_not_summed():
    p = state()
    for i in range(3):
        apply_event(p, {'event': 'fragment', 'stream': 'v', 'current': i, 'bytes': 1024})
    assert 'committed_bytes' not in p['streams']['v']


def test_cumulative_values_survive_dropped_and_delayed_samples():
    p = state()
    for count in (1, 4, 10, 2):
        apply_event(p, dict(event='fragment_state', stream='v', downloaded_fragments=count,
                            committed_fragments=count, committed_bytes=count*1024))
    s = p['streams']['v']
    assert s['committed_fragments'] == 10 and s['committed_bytes'] == 10240


def test_downloaded_while_first_append_blocked_is_not_zero_progress():
    p = state()
    apply_event(p, dict(event='fragment_state', stream='v', downloaded_fragments=15,
                        committed_fragments=0, received_bytes=15000))
    text = progress_text(p)
    assert '取得 15 / 保存 0' in text and '保存待ち 15' in text


def test_mux_does_not_reuse_download_percentage_or_speed():
    p = state()
    apply_event(p, dict(event='progress', stream='v', percent=100, speed=1e9))
    apply_event(p, dict(event='phase', name='postprocessing'))
    assert current_percent(p) is None
    assert '100' not in progress_text(p) and '/s' not in progress_text(p)


def test_missing_audio_prevents_premature_completion():
    p = state()
    apply_event(p, dict(event='streams', count=2))
    apply_event(p, dict(event='progress', stream='v', percent=100))
    assert current_percent(p) is None
    apply_event(p, dict(event='progress', stream='a', percent=10))
    assert current_percent(p) == 10


def test_partial_catchup_does_not_claim_all_streams_are_live():
    p = state()
    apply_event(p, dict(event='streams', count=2))
    apply_event(p, dict(event='catchup', stream='v', percent=100, caught_up=True))
    assert not p['caught_up'] and catchup_percent(p) is None
    apply_event(p, dict(event='catchup', stream='a', percent=20, caught_up=False))
    assert catchup_percent(p) == 20
    apply_event(p, dict(event='catchup', stream='v', percent=10, caught_up=False))
    assert not p['caught_up'] and catchup_percent(p) == 10


def test_late_progress_cannot_resurrect_completed_job():
    p = state()
    apply_event(p, dict(event='done', status='completed'), now=3)
    assert not apply_event(p, dict(event='phase', name='downloading'), now=5)
    assert p['phase'] == 'done' and p['elapsed'] == 3


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, True, '42'])
def test_invalid_numeric_data_is_not_a_percentage(bad):
    p = state()
    apply_event(p, dict(event='progress', stream='v', percent=bad))
    assert current_percent(p) is None


def test_nonfragment_transfer_shows_bytes_before_completion():
    p = state()
    apply_event(p, dict(event='progress', stream='v', downloaded_bytes=4096,
                        total_bytes=8192, fragment_index=None, speed=1024))
    assert current_percent(p) == 50
    assert '4.0 KiB' in progress_text(p)


def test_unknown_live_denominator_is_not_fake_zero_percent():
    p = state()
    apply_event(p, dict(event='download_context', live=True, streams=1, catchup=False))
    apply_event(p, dict(event='progress', stream='v', percent=25))
    assert current_percent(p) is None
    assert '25%' not in progress_text(p)


def test_telemetry_is_latest_and_not_behind_a_log_flood():
    b = EventBuffer(1000)
    for i in range(50000):
        b.put(dict(event='log', message='old log'))
        b.put(dict(event='progress', stream='v', percent=i/500))
    result = b.drain(10)
    assert result[0]['event'] == 'progress'
    assert result[0]['percent'] == 49999/500
    assert len(b.telemetry) == 0 and len(b.items) <= 1000


def test_control_events_survive_flood_and_retain_order_with_telemetry():
    b = EventBuffer(3)
    b.put(dict(event='phase', name='downloading'))
    for i in range(50): b.put(dict(event='progress', stream='v', percent=i))
    b.put(dict(event='phase', name='postprocessing'))
    for i in range(50): b.put(dict(event='log', message=str(i)))
    events = b.drain()
    assert [e.get('name') for e in events if e['event'] == 'phase'] == ['downloading', 'postprocessing']
    assert events[1]['event'] == 'progress'


def test_pending_final_counter_is_flushed_before_done():
    output = StringIO()
    emit = Emitter(output, clock=lambda: 1)
    emit('fragment_state', stream='v', downloaded_fragments=1, committed_fragments=1)
    emit('fragment_state', stream='v', downloaded_fragments=200, committed_fragments=200)
    emit('done', status='completed')
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events[-2]['committed_fragments'] == 200
    assert events[-1]['event'] == 'done'


def test_repeated_live_true_does_not_bypass_rate_limit():
    output = StringIO()
    emit = Emitter(output, clock=lambda: 1)
    for i in range(10000): emit('catchup', stream='v', percent=100, caught_up=True)
    assert len(output.getvalue().splitlines()) == 1
    emit('done', status='completed')
    assert len(output.getvalue().splitlines()) == 3


def test_first_fragment_not_delayed_by_zero_counter_sample():
    output = StringIO()
    emit = Emitter(output, clock=lambda: 1)
    emit('fragment_state', stream='v', downloaded_fragments=0, committed_fragments=0)
    emit('fragment_state', stream='v', downloaded_fragments=1, committed_fragments=0)
    assert len(output.getvalue().splitlines()) == 2


def test_exclusive_edge_and_new_head_are_used_at_append():
    events = []
    tracker = FragmentTelemetry('v', lambda kind, **kw: events.append((kind, kw)), catchup=True)
    tracker.observe(dict(frag_index=1, url='https://media/part?sq=100', fragment_count=105))
    tracker.observe(dict(frag_index=2, url='https://media/part?sq=101', fragment_count=110))
    tracker.appended(1, 10)
    update = events[-1][1]
    assert update['total'] == 10 and update['current'] == 1
    assert update['gap_fragments'] == 9 and update['percent'] == 10


def test_stale_edge_cannot_claim_live():
    now = [0]
    events = []
    tracker = FragmentTelemetry('v', lambda kind, **kw: events.append((kind, kw)),
                                catchup=True, clock=lambda: now[0])
    tracker.observe(dict(frag_index=1, url='https://media/part?sq=100', fragment_count=101))
    now[0] = 20
    tracker.appended(1, 1)
    assert events[-1][1]['caught_up'] is False


def test_invalid_and_non_youtube_sequence_is_not_guessed():
    events = []
    tracker = FragmentTelemetry('v', lambda kind, **kw: events.append((kind, kw)), catchup=True)
    tracker.observe(dict(frag_index=1, url='https://media/part.ts', fragment_count=100))
    tracker.appended(1, 1)
    assert not any(kind == 'catchup' for kind, _ in events)


def test_failed_download_does_not_become_finished():
    events = []
    tracker = FragmentTelemetry('v', lambda kind, **kw: events.append((kind, kw)))
    tracker.finish(False)
    assert events[-1][1]['finished'] is False


def test_quality_cap_does_not_silently_fall_back_to_4k():
    import yt_dlp
    from livecatch_core.config import QUALITY
    formats = [dict(format_id='4k', ext='mp4', url='https://example.invalid/video',
                    height=2160, vcodec='h264', acodec='aac')]
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        def select(name):
            selector = ydl.build_format_selector(QUALITY[name])
            return list(selector(dict(formats=formats, has_merged_format=True,
                                      incomplete_formats=False)))
        assert select('recommended_1080p') == []
        assert select('best')[0]['height'] == 2160


@pytest.mark.parametrize('values', [(True, 2), (1, False), (1.5, 2), (1, 2.5)])
def test_prefetch_rejects_non_integer_limits(values):
    from livecatch_core.parallel import OrderedPrefetch
    with pytest.raises(ValueError): OrderedPrefetch(*values)
