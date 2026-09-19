"""A shared YouTube sequence cutoff, not a regex/timeout-based catch-up guess."""
from __future__ import annotations

from threading import Condition, Event
from time import monotonic
from urllib.parse import parse_qs, urlsplit


class SnapshotBarrier:
    def __init__(self, streams: int, cancel: Event, timeout: float = 30):
        self.streams, self.cancel, self.timeout = streams, cancel, timeout
        self.condition = Condition()
        self.heads: dict[str, int] = {}
        self.cutoff: int | None = None

    def register(self, stream: str, head: int) -> int | None:
        with self.condition:
            self.heads[stream] = head
            if len(self.heads) == self.streams:
                self.cutoff = min(self.heads.values())
                self.condition.notify_all()
            deadline = monotonic() + self.timeout
            while self.cutoff is None:
                if self.cancel.is_set():
                    return None
                if monotonic() >= deadline:
                    self.cancel.set()
                    self.condition.notify_all()
                    raise RuntimeError("Snapshot: audio/video cutoff negotiation timed out")
                self.condition.wait(timeout=0.05)
            return self.cutoff


def snapshot_fragments(source, barrier: SnapshotBarrier, stream: str, emit):
    """yt-dlp 2026.08.19: fragment_count is the exclusive YouTube sq head.

    The absolute sq is essential for long streams whose DVR starts after sq=0.
    End naturally after the common cutoff; let ALL streams merge normally.
    """
    cutoff = None
    previous = None
    saw_fragment = False
    for fragment in source:
        if barrier.cancel.is_set():
            return
        query = parse_qs(urlsplit(fragment["url"]).query)
        try:
            seq = int(query["sq"][0])
            head = int(fragment["fragment_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Snapshot requires YouTube DVR sq/count metadata; refusing to guess") from exc
        if seq < 0 or head <= seq or (previous is not None and seq <= previous):
            raise RuntimeError("Invalid/nonmonotonic YouTube snapshot sequence")
        if cutoff is None:
            cutoff = barrier.register(stream, head)
            if cutoff is None:
                return
            emit("snapshot", stream=stream, exclusive_sequence=cutoff)
        if seq >= cutoff:
            if not saw_fragment:
                raise RuntimeError("No common accessible DVR range for audio/video")
            return
        saw_fragment = True
        previous = seq
        yield fragment
        if seq + 1 >= cutoff:
            return  # Do NOT request the next (possibly blocking) live fragment.
    if not saw_fragment and not barrier.cancel.is_set():
        raise RuntimeError("Snapshot contains no accessible DVR fragments")
