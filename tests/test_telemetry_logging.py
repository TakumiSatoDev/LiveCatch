"""A noisy logger must not turn coalesced progress back into a flood."""
from io import StringIO
import json

from livecatch_core.events import Emitter


def test_logs_do_not_bypass_telemetry_interval():
    output = StringIO()
    emit = Emitter(output, clock=lambda: 1)
    for count in range(1, 1001):
        emit('fragment_state', stream='v', downloaded_fragments=count,
             committed_fragments=count)
        emit('log', message='fragment log')
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert sum(e['event'] == 'fragment_state' for e in events) == 1
    emit('done', status='completed')
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events[-2]['committed_fragments'] == 1000
    assert events[-1]['event'] == 'done'
