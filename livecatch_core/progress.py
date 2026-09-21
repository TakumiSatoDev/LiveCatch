"""One progress reducer for manual recording and channel monitoring.

Network completion, ordered append and live-edge catch-up are different facts.
All counters received over IPC are cumulative: dropping an intermediate update
must never change the final total. No Tk, filesystem or network calls live here.
"""
from __future__ import annotations

import math
import re
from time import monotonic

TELEMETRY = frozenset({'progress', 'fragment', 'fragment_state', 'catchup', 'phase_progress'})
PHASES = ('starting', 'extracting', 'downloading', 'postprocessing', 'done')


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def stream_key(value):
    # v3.4 used `0:137` for the writer and `137` for network callbacks.
    return re.sub(r'^\d+:', '', str(value or 'media'))[:128]


def new_progress(*, active=False, catchup=False, now=None):
    return dict(phase='starting', last_phase='starting', active=active,
                streams={}, catchup={}, caught_up=False, catchup_active=catchup,
                stream_count=0, outputs=0, detail='', started_at=now,
                elapsed=None, phase_started=now, phase_detail='', terminal=False)


def apply_event(state, event, *, now=None):
    """Update an owner-thread dictionary. Return whether it affected progress."""
    now = monotonic() if now is None else now
    kind = event.get('event')
    if state.get('terminal'):
        return False
    if kind == 'phase':
        phase = event.get('name', 'starting')
        phase = 'postprocessing' if phase == 'exporting' else phase
        if phase not in PHASES:
            return False
        if phase != state['phase']:
            state['phase_started'] = now
            state['phase_detail'] = ''
            state.pop('phase_percent', None)
        state['phase'] = state['last_phase'] = phase
    elif kind == 'phase_progress':
        if event.get('phase') != state['phase']:
            return False
        state['phase_detail'] = str(event.get('detail') or '')[:160]
        state['phase_percent'] = number(event.get('percent'))
    elif kind == 'download_context':
        state['catchup_active'] = event.get('catchup') is True
        state['is_live'] = event.get('live') is True
        state['catchup_supported'] = event.get('catchup_supported') is True
        state['stream_count'] = max(1, int(number(event.get('streams')) or 1))
    elif kind == 'streams':
        count = number(event.get('count'))
        if count:
            state['stream_count'] = int(count)
    elif kind in ('progress', 'fragment', 'fragment_state'):
        key = stream_key(event.get('stream'))
        s = state['streams'].setdefault(key, {})
        # Delayed/out-of-order snapshots may not move absolute counters back.
        for field in ('downloaded_bytes', 'committed_bytes', 'received_bytes',
                      'downloaded_fragments', 'committed_fragments', 'fragment_index'):
            value = number(event.get(field))
            if value is not None:
                s[field] = max(s.get(field, 0), value)
        for field in ('total_bytes', 'fragment_count', 'speed'):
            if field in event:
                s[field] = number(event[field])
        if kind == 'fragment':
            current, total = number(event.get('current')), number(event.get('total'))
            if current is not None:
                s['fragment_index'] = max(s.get('fragment_index', 0), current)
            if total is not None:
                s['fragment_count'] = total
            # `bytes` is a dropped/throttled delta in old workers. NEVER add it.
        value = number(event.get('percent'))
        if value is not None:
            s['percent'] = min(100.0, value)
        elif s.get('fragment_count') and number(s.get('fragment_index')) is not None:
            s['percent'] = min(100.0, 100 * s['fragment_index'] / s['fragment_count'])
        elif s.get('total_bytes') and number(s.get('downloaded_bytes')) is not None:
            s['percent'] = min(100.0, 100 * s['downloaded_bytes'] / s['total_bytes'])
        if event.get('status') == 'finished' or event.get('finished') is True:
            s['finished'] = True
            s['percent'] = 100.0
        s['updated_at'] = now
    elif kind == 'catchup':
        key = stream_key(event.get('stream'))
        s = state['catchup'].setdefault(key, {})
        for field in ('percent', 'current', 'total', 'gap_fragments'):
            s[field] = number(event.get(field))
        s['caught_up'] = event.get('caught_up') is True
        s['updated_at'] = now
        state['catchup_active'] = state['catchup_supported'] = True
        expected = state.get('stream_count', 0)
        values = list(state['catchup'].values())
        state['caught_up'] = bool(values and len(values) >= max(1, expected)
                                  and all(p['caught_up'] for p in values))
    elif kind == 'output':
        state['outputs'] += 1
        state['detail'] = str(event.get('path') or '')[:600]
    elif kind == 'snapshot':
        state['detail'] = f"Snapshot sequence < {event.get('exclusive_sequence', '?')}"
    elif kind == 'done':
        start = state.get('started_at')
        state['elapsed'] = max(0, now - start) if number(start) is not None else None
        state['active'] = False
        state['phase'] = 'done' if event.get('status') == 'completed' else event.get('status', 'failed')
        state['terminal'] = True
    else:
        return False
    return True


def catchup_percent(state):
    values = list(state.get('catchup', {}).values())
    expected = max(1, state.get('stream_count', 0))
    if len(values) < expected:
        return None  # A slow/missing audio track is not implicitly complete.
    percentages = [number(s.get('percent')) for s in values]
    return min(percentages) if percentages and all(p is not None for p in percentages) else None


def current_percent(state):
    phase = state.get('phase')
    if phase == 'done':
        return 100.0
    if phase != 'downloading':
        return number(state.get('phase_percent')) if phase in PHASES else None
    if state.get('catchup_active'):
        return None if state.get('caught_up') else catchup_percent(state)
    if state.get('is_live'):
        return None
    streams = list(state.get('streams', {}).values())
    if len(streams) < max(1, state.get('stream_count', 0)):
        return None
    values = [number(s.get('percent')) for s in streams]
    return min(values) if values and all(p is not None for p in values) else None


def format_percent(value):
    return f"{math.floor(value * 10) / 10:g}%"


def format_bytes(value):
    value = number(value)
    if value is None:
        return '?'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{value:.0f} B'
        value /= 1024


def transfer_text(state, language='ja'):
    """Show reception even while the first ordered append is still blocked."""
    ja = language == 'ja'
    streams = list(state.get('streams', {}).values())
    pieces = []
    received = sum(s.get('downloaded_fragments', 0) for s in streams)
    committed = sum(s.get('committed_fragments', 0) for s in streams)
    if any('downloaded_fragments' in s or 'committed_fragments' in s for s in streams):
        pieces.append((f'取得 {received:.0f} / 保存 {committed:.0f} frag' if ja else
                       f'Received {received:.0f} / appended {committed:.0f} frag'))
        if received > committed:
            pieces.append(f'保存待ち {received-committed:.0f}' if ja else f'Awaiting append {received-committed:.0f}')
    else:
        for s in streams:
            current = number(s.get('fragment_index'))
            total = number(s.get('fragment_count'))
            if current is not None:
                pieces.append(f'{current:.0f}/{total:.0f} frag' if total else f'{current:.0f} frag')
    received_bytes = sum(max(s.get('downloaded_bytes', 0), s.get('received_bytes', 0)) for s in streams)
    if received_bytes:
        pieces.append(format_bytes(received_bytes))
    speed = sum(s.get('speed') or 0 for s in streams)
    if speed:
        pieces.append(f'{format_bytes(speed)}/s')
    return ' · '.join(pieces)


def progress_text(state, language='ja'):
    ja = language == 'ja'
    phase = state.get('phase')
    if phase == 'done':
        return '100%'
    if phase in ('failed', 'cancelled', 'forced'):
        return {'failed': ('失敗', 'Failed'), 'cancelled': ('停止', 'Stopped'),
                'forced': ('強制停止', 'Force stopped')}[phase][not ja]
    if phase != 'downloading':
        detail = state.get('phase_detail') or ('処理中…' if ja else 'Working…')
        value = current_percent(state)
        return f'{detail} {value:.0f}%' if value is not None else detail
    if state.get('catchup_active'):
        value = catchup_percent(state)
        if state.get('caught_up'):
            prefix = 'LIVE'
        elif value is not None:
            # Do not round 99.9 to 100 while there is still a measurable gap.
            prefix = f'追いつき {format_percent(value)}' if ja else f'Catch-up {format_percent(value)}'
            gaps = [p.get('gap_fragments') for p in state['catchup'].values()]
            if gaps and all(number(g) is not None for g in gaps):
                prefix += f' / 残り約{max(gaps):.0f} frag' if ja else f' / ~{max(gaps):.0f} frag left'
        else:
            prefix = '追いつき率を確認中' if ja else 'Waiting for catch-up measurement'
    else:
        value = current_percent(state)
        prefix = format_percent(value) if value is not None else ('録画中' if ja else 'Recording')
    metrics = transfer_text(state, language)
    return ' · '.join(p for p in (prefix, metrics) if p)
