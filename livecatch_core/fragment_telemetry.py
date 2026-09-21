"""Thread-safe, bounded bookkeeping; never owns media bytes or writes files."""
from __future__ import annotations

from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit


class FragmentTelemetry:
    def __init__(self, stream, emit, *, total=None, catchup=False, clock=monotonic):
        self.stream, self.emit, self.clock = stream, emit, clock
        self.lock = Lock()
        self.total = total if type(total) is int and total > 0 else None
        self.catchup = catchup
        self.first_sequence = self.head = None
        self.head_at = None
        self.pending = {}
        self.ready = set()
        self.received = self.committed = 0
        self.received_bytes = self.committed_bytes = 0
        self.last_index = 0
        self.first_index = None

    def observe(self, fragment):
        index = fragment.get('frag_index')
        if type(index) is not int:
            return
        # YouTube's count is EXCLUSIVE (last available sq + 1), not inclusive.
        sequence = head = None
        if self.catchup:
            try:
                sequence = int(parse_qs(urlsplit(fragment.get('url', '')).query)['sq'][0])
                head = fragment.get('fragment_count')
                if type(head) is not int or sequence < 0 or head <= sequence:
                    sequence = head = None
            except (ValueError, KeyError, TypeError):
                pass
        with self.lock:
            if self.first_index is None:
                self.first_index = index
            if sequence is not None:
                if self.first_sequence is None:
                    self.first_sequence = sequence
                if self.head is None or head > self.head:
                    self.head, self.head_at = head, self.clock()
                self.pending[index] = sequence

    def _snapshot(self, finished=False):
        return dict(stream=self.stream, downloaded_fragments=self.received,
                    committed_fragments=self.committed, received_bytes=self.received_bytes,
                    committed_bytes=self.committed_bytes, fragment_index=self.last_index,
                    fragment_count=self.total, finished=finished)

    def downloaded(self, index, size):
        with self.lock:
            if index in self.ready or index <= self.last_index:
                return
            self.ready.add(index)
            self.received += 1
            self.received_bytes += max(0, size or 0)
            self.emit('fragment_state', **self._snapshot())

    def appended(self, index, size):
        with self.lock:
            self.ready.discard(index)
            self.last_index = index
            self.committed += 1
            self.received = max(self.received, self.committed)
            self.committed_bytes += size
            self.emit('fragment_state', **self._snapshot())
            seq = self.pending.pop(index, None)
            if seq is None or self.first_sequence is None or self.head is None:
                return
            total = self.head - self.first_sequence
            current = seq - self.first_sequence + 1
            gap = max(0, self.head - seq - 1)
            if total <= 0 or current < 0:
                return
            fresh = self.head_at is not None and self.clock() - self.head_at <= 15
            self.emit('catchup', stream=self.stream, current=current, total=total,
                      gap_fragments=gap, percent=min(100., current * 100 / total),
                      caught_up=bool(gap <= 2 and fresh))

    def finish(self, success):
        with self.lock:
            self.emit('fragment_state', **self._snapshot(finished=bool(success)))
            self.pending.clear()
            self.ready.clear()
