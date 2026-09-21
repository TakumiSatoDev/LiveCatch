import json
import sys
import time
import pytest
from livecatch_core.config import Settings
from livecatch_core.supervisor import Supervisor

CHILD='''import json,sys,time
config=json.loads(sys.stdin.readline())
print(json.dumps({'event':'ready'}),flush=True)
print(json.dumps({'event':'unicode','message':'日本語 ⧸ UTF-8'},ensure_ascii=False),flush=True)
if config['url'].endswith('/crash'):
 print(json.dumps({'event':'done','status':'completed','code':0}),flush=True)
 sys.exit(7)
for line in sys.stdin:
 if json.loads(line).get('command')=='stop':
  print(json.dumps({'event':'done','status':'cancelled','code':130}),flush=True)
  sys.exit(130)
'''

def wait_done(s,seconds=3):
    end=time.monotonic()+seconds;events=[]
    while time.monotonic()<end:
        events+=s.events.drain()
        if any(e['event']=='done' for e in events):return next(e for e in events if e['event']=='done')
        time.sleep(.01)
    pytest.fail('child did not finish')

@pytest.fixture
def supervisor(tmp_path):
    child=tmp_path/'child.py';child.write_text(CHILD)
    s=Supervisor([sys.executable,'-u',str(child)])
    yield s
    if s.active:s.force_stop();wait_done(s)

def test_stop_then_restart_has_no_stale_kill(supervisor):
    s=supervisor;cfg=Settings(url='https://youtu.be/first')
    s.start(cfg);first=s.proc.pid
    with pytest.raises(RuntimeError):s.start(cfg)
    s.stop();assert wait_done(s)['status']=='cancelled'
    assert not s.active
    s.start(cfg);assert s.proc.pid!=first
    time.sleep(.1);assert s.active
    s.stop();assert wait_done(s)['status']=='cancelled'

def test_worker_environment_transports_unicode(supervisor):
    supervisor.start(Settings(url='https://youtu.be/first'))
    end=time.monotonic()+3
    found=None
    while time.monotonic()<end and found is None:
        for event in supervisor.events.drain():
            if event.get('event')=='unicode':
                found=event.get('message')
        time.sleep(.01)
    assert found=='日本語 ⧸ UTF-8'
    supervisor.stop()
    assert wait_done(supervisor)['status']=='cancelled'


def test_child_error_overrides_completed_event(supervisor):
    supervisor.start(Settings(url='https://youtu.be/crash'))
    assert wait_done(supervisor)['status']=='failed'

def test_force_stop_only_current_child(supervisor):
    supervisor.start(Settings(url='https://youtu.be/first'))
    supervisor.force_stop()
    assert wait_done(supervisor)['status']=='forced'
    assert not supervisor.active

def test_spawn_error_resets_state():
    s=Supervisor(['/no/such/program'])
    with pytest.raises(OSError):s.start(Settings(url='https://youtu.be/test'))
    assert not s.active
