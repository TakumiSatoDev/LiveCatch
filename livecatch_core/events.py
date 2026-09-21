"""Bounded IPC with latest-value telemetry instead of a stale FIFO backlog."""
from __future__ import annotations

from collections import deque
import json
import re
from threading import Lock
from time import monotonic

from .progress import TELEMETRY, stream_key

_URL = re.compile(r'https?://[^\s]+')


def redact(text):
    return _URL.sub(lambda m: m[0].split('?', 1)[0] + ('?[redacted]' if '?' in m[0] else ''), text)


class Emitter:
    def __init__(self, output, *, interval=0.1, clock=monotonic):
        self.output, self.interval, self.clock = output, interval, clock
        self.lock = Lock()
        self.last, self.pending, self.milestones = {}, {}, {}

    def _write(self, kind, data):
        if 'message' in data:
            data = dict(data, message=redact(str(data['message']))[:8000])
        self.output.write(json.dumps({'event': kind, **data}, ensure_ascii=False, allow_nan=False) + '\n')
        self.output.flush()

    def __call__(self, kind, **data):
        with self.lock:
            if kind in TELEMETRY:
                data['stream'] = stream_key(data.get('stream'))
                key = (kind, data['stream'])
                now = self.clock()
                # First received/committed fragment and edge transitions are
                # immediate; repeated LIVE updates are still rate limited.
                milestone = (bool(data.get('downloaded_fragments')),
                             bool(data.get('committed_fragments')),
                             data.get('caught_up'), data.get('finished'), data.get('status') == 'finished')
                changed = self.milestones.get(key) != milestone
                self.milestones[key] = milestone
                if not changed and now - self.last.get(key, -1e20) < self.interval:
                    self.pending[key] = (kind, data)
                    return
                self.pending.pop(key, None)
                self.last[key] = now
            elif kind not in ('log', 'watch_log'):
                # Logs must not defeat rate limits. Flush final samples only
                # before ordered controls (phase changes, outputs, DONE, etc.).
                for pending_kind, pending_data in self.pending.values():
                    self._write(pending_kind, pending_data)
                self.pending.clear()
            self._write(kind, data)


class EventBuffer:
    """Telemetry is coalesced, controls retain order, logs cannot evict either."""
    def __init__(self, limit=1000):
        self.lock = Lock()
        self.items = deque(maxlen=limit)
        self.controls = deque(maxlen=max(64, min(limit, 1000)))
        self.telemetry = {}
        self.terminal = None
        self.serial = 0

    def put(self, event):
        with self.lock:
            self.serial += 1
            kind = event.get('event')
            if kind == 'done':
                self.terminal = event
            elif kind in TELEMETRY:
                key = (kind, stream_key(event.get('stream')))
                if key not in self.telemetry and len(self.telemetry) >= 128:
                    self.telemetry.pop(next(iter(self.telemetry)))
                self.telemetry[key] = (self.serial, event)
            elif kind in ('log', 'watch_log'):
                self.items.append(event)
            else:
                self.controls.append((self.serial, event))

    def drain(self, limit=100):
        if limit < 1:
            return []
        with self.lock:
            pending = sorted([*self.controls, *self.telemetry.values()], key=lambda item: item[0])
            taken = pending[:limit]
            serials = {serial for serial, _event in taken}
            self.controls = deque((p for p in self.controls if p[0] not in serials), maxlen=self.controls.maxlen)
            self.telemetry = {k: p for k, p in self.telemetry.items() if p[0] not in serials}
            result = [event for _serial, event in taken]
            result.extend(self.items.popleft() for _ in range(min(limit - len(result), len(self.items))))
            if not self.items and not self.controls and not self.telemetry and self.terminal is not None:
                result.append(self.terminal)
                self.terminal = None
            return result
