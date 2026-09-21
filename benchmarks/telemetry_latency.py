"""Offline queue-latency comparison. NOT a network/SSD throughput benchmark."""
from collections import deque
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livecatch_core.events import EventBuffer


def main():
    old = deque(maxlen=1000)
    new = EventBuffer(1000)
    for queue in (old, new):
        put = queue.append if isinstance(queue, deque) else queue.put
        put(dict(event='progress', stream='video', percent=1))
        for _ in range(50000): put(dict(event='log', message='simulated log line'))
        put(dict(event='fragment_state', stream='video', downloaded_fragments=120, committed_fragments=96))
    start = perf_counter()
    old_polls = 0
    found = False
    while old and not found:
        old_polls += 1
        batch = [old.popleft() for _ in range(min(100, len(old)))]
        found = any(e['event'] == 'fragment_state' for e in batch)
    new_first = new.drain(100)
    assert any(e['event'] == 'fragment_state' for e in new_first)
    print(json.dumps(dict(fixture='50,000 log lines before a latest fragment sample',
                          old_fifo_polls=old_polls, coalesced_polls=1, gui_poll_ms=100,
                          old_delivery_bound_ms=old_polls*100,
                          coalesced_delivery_bound_ms=100,
                          measurement_seconds=perf_counter()-start), indent=2))


if __name__ == '__main__': main()
