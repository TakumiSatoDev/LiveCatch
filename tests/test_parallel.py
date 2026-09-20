from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time
import pytest
from livecatch_core.parallel import OrderedPrefetch
from livecatch_core.snapshot import SnapshotBarrier, snapshot_fragments

noop=lambda *a,**k:None

def test_order_and_worker_bound():
    active=peak=0;lock=Lock()
    def task(i):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep((4-i%4)*.004)
        with lock:active-=1
        return i
    with OrderedPrefetch(4,8) as pool:
        assert list(pool.map(task,range(30)))==list(range(30))
    assert 1<peak<=4

def test_blocking_source_cannot_block_first_commit():
    gate=Event()
    def source():
        yield 1
        gate.wait(2)
        yield 2
    try:
        with OrderedPrefetch(2,4) as pool:
            results=pool.map(lambda x:x,source())
            with ThreadPoolExecutor(1) as observer:
                future=observer.submit(next,results)
                try:assert future.result(timeout=.5)==1
                finally:gate.set()
            assert list(results)==[2]
    finally:gate.set()

def test_prefetch_counts_completed_not_yet_appended():
    produced=[]
    def source():
        for i in range(100):produced.append(i);yield i
    with OrderedPrefetch(2,4) as pool:
        results=pool.map(lambda x:x,source());assert next(results)==0
        time.sleep(.04)
        assert len(produced)<=4
        assert list(results)==list(range(1,100))

@pytest.mark.parametrize('where',['task','source'])
def test_failure_cancels_siblings(where):
    cancel=Event()
    def source():
        yield 0
        if where=='source':raise ValueError('expected')
        yield 1
    def task(i):
        if i==1:raise ValueError('expected')
        return i
    with pytest.raises(ValueError,match='expected'):
        with OrderedPrefetch(2,4,cancel) as pool:list(pool.map(task,source()))
    assert cancel.is_set()

def test_cancellation_of_waiting_source():
    gate=Event();cancel=Event()
    def source():gate.wait(2);yield 1
    try:
        with OrderedPrefetch(1,1,cancel) as pool:
            results=pool.map(lambda x:x,source())
            with ThreadPoolExecutor(1) as observer:
                future=observer.submit(list,results);time.sleep(.03);cancel.set()
                assert future.result(timeout=.5)==[]
    finally:gate.set()

@pytest.mark.parametrize('workers,window',[(0,1),(2,1)])
def test_bad_pool(workers,window):
    with pytest.raises(ValueError):OrderedPrefetch(workers,window)

def test_single_use():
    with OrderedPrefetch(1,1) as pool:
        assert list(pool.map(str,[1]))==['1']
        with pytest.raises(RuntimeError):list(pool.map(str,[2]))

def fragments(start,head):
    for i in range(start,head):
        yield {'url':f'https://media.invalid/part?sq={i}','fragment_count':head,'frag_index':i-start+1}
    raise AssertionError('snapshot advanced past target')

def test_snapshot_absolute_dvr_offset():
    barrier=SnapshotBarrier(1,Event())
    result=list(snapshot_fragments(fragments(500,505),barrier,'v',noop))
    assert len(result)==5 and result[-1]['url'].endswith('sq=504')

def test_common_audio_video_cutoff():
    barrier=SnapshotBarrier(2,Event(),timeout=1)
    with ThreadPoolExecutor(2) as pool:
        jobs=[pool.submit(list,snapshot_fragments(fragments(100,head),barrier,name,noop)) for name,head in [('v',106),('a',104)]]
        results=[j.result(timeout=2) for j in jobs]
    assert [len(r) for r in results]==[4,4] and barrier.cutoff==104

def test_snapshot_timeout():
    barrier=SnapshotBarrier(2,Event(),timeout=.02)
    with pytest.raises(RuntimeError,match='timed out'):list(snapshot_fragments(fragments(0,2),barrier,'v',noop))
    assert barrier.cancel.is_set()

@pytest.mark.parametrize('fragment', [{'url':'https://media/x','fragment_count':2},{'url':'https://media/x?sq=x','fragment_count':2},{'url':'https://media/x?sq=2','fragment_count':2}])
def test_snapshot_refuses_missing_metadata(fragment):
    with pytest.raises(RuntimeError):list(snapshot_fragments([fragment],SnapshotBarrier(1,Event()),'v',noop))

def test_snapshot_refuses_sequence_reordering():
    source=[{'url':f'https://media/x?sq={i}','fragment_count':4} for i in [0,1,1,3]]
    with pytest.raises(RuntimeError,match='nonmonotonic'):list(snapshot_fragments(source,SnapshotBarrier(1,Event()),'v',noop))
