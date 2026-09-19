"""Structured worker IPC and bounded GUI buffering."""
from __future__ import annotations

from collections import deque
import json
import re
from threading import Lock
from time import monotonic

_URL = re.compile(r"https?://[^\s]+")


def redact(text: str) -> str:
    # Signed media URLs / cookie-bearing query strings must not enter the GUI log.
    return _URL.sub(lambda m: m[0].split("?", 1)[0] + ("?[redacted]" if "?" in m[0] else ""), text)


class Emitter:
    def __init__(self, output):
        self.output = output
        self.lock = Lock()
        self.last: dict[tuple, float] = {}

    def __call__(self, kind: str, **data) -> None:
        with self.lock:
            if kind in ("progress", "fragment"):
                key = (kind, data.get("stream"))
                now = monotonic()
                if now - self.last.get(key, -1) < 0.25:
                    return
                self.last[key] = now
            if "message" in data:
                data["message"] = redact(str(data["message"]))[:8000]
            self.output.write(json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n")
            self.output.flush()


class EventBuffer:
    """Lossy telemetry + a separate completion slot; a log flood cannot lose DONE."""
    def __init__(self, limit: int = 1000):
        self.lock = Lock()
        self.items = deque(maxlen=limit)
        self.terminal = None

    def put(self, event: dict) -> None:
        with self.lock:
            if event.get("event") == "done":
                self.terminal = event
            else:
                self.items.append(event)

    def drain(self, limit: int = 100) -> list[dict]:
        with self.lock:
            result = [self.items.popleft() for _ in range(min(limit, len(self.items)))]
            if not self.items and self.terminal is not None:
                result.append(self.terminal)
                self.terminal = None
            return result
