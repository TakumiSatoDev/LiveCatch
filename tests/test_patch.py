"""Contract-level tests. This fake is NOT a claim of testing installed yt-dlp.
Real dependency tests are separately collected in test_ytdlp_integration.py.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from livecatch_core.ytdlp_patch import _patch_class

class FragmentContract:
    def __init__(self):self.params={'concurrent_fragment_downloads':4};self.outputs={};self.pool=None
    def _append_fragment(self,ctx,data):self.outputs.setdefault(ctx['filename'],[]).append(data)
    def download_and_append_fragments(self,ctx,fragments,info_dict,*,tpe=None,interrupt_trigger=(True,),**kwargs):
        self.pool=tpe
        with tpe as pool:
            for fragment in pool.map(lambda x:x,fragments):
                ctx['fragment_index']=fragment['frag_index']
                self._append_fragment(ctx,str(fragment['frag_index']).encode())
        return True
    def download_and_append_fragments_multiple(self,*args,**kwargs):
        if len(args)==1:return self.download_and_append_fragments(*args[0],**kwargs)
        with ThreadPoolExecutor(len(args)) as pool:
            for ctx,_,_ in args:ctx['max_progress']=len(args)
            jobs=[pool.submit(self.download_and_append_fragments,*item,**kwargs) for item in args]
            return all(job.result(timeout=2) for job in jobs)

def make(head):
    for i in range(head):
        yield {'frag_index':i+1,'fragment_count':head,'url':f'https://media.invalid/x?sq={i}'}
    raise AssertionError('unbounded live read')

def test_patch_calls_real_contract_path_and_restores():
    original=FragmentContract.download_and_append_fragments;append=FragmentContract._append_fragment
    fd=FragmentContract();events=[]
    with _patch_class(FragmentContract,Event(),prefetch=2,snapshot=False,emit=lambda kind,**data:events.append((kind,data))):
        assert fd.download_and_append_fragments({'filename':'a'},[{'frag_index':i} for i in range(10)],{},tpe=object())
    assert fd.outputs['a']==[str(i).encode() for i in range(10)]
    assert [data['fragment_index'] for kind,data in events if kind=='fragment_state' and not data['finished']]==list(range(10))
    assert FragmentContract.download_and_append_fragments is original and FragmentContract._append_fragment is append

def test_catchup_progress_normalizes_truncated_live_sequence():
    fd=FragmentContract();events=[]
    fragments=[
        {'frag_index':i+1,'fragment_count':106,'url':f'https://media.invalid/x?sq={100+i}'}
        for i in range(6)
    ]
    info={'is_live':True,'is_from_start':True}
    with _patch_class(
            FragmentContract,Event(),prefetch=2,snapshot=False,catchup=True,
            emit=lambda kind,**data:events.append((kind,data))):
        assert fd.download_and_append_fragments({'filename':'a'},fragments,info,tpe=object())
    catchup=[data for kind,data in events if kind=='catchup']
    assert round(catchup[0]['percent'],1)==16.7
    assert catchup[-1]['percent']==100.0
    assert catchup[-1]['gap_fragments']==0 and catchup[-1]['caught_up'] is True


def test_patch_snapshot_both_streams():
    fd=FragmentContract();info={'is_live':True,'extractor_key':'Youtube','protocol':'http_dash_segments_generator'}
    with _patch_class(FragmentContract,Event(),prefetch=2,snapshot=True,emit=lambda *a,**k:None):
        assert fd.download_and_append_fragments_multiple(({'filename':'v'},make(6),info),({'filename':'a'},make(4),info))
    assert fd.outputs['v']==fd.outputs['a']==[b'1',b'2',b'3',b'4']

def test_patch_rejects_unsupported_protocol_and_restores():
    fd=FragmentContract();original=FragmentContract.download_and_append_fragments;cancel=Event()
    with pytest.raises(RuntimeError,match='YouTube'):
        with _patch_class(FragmentContract,cancel,prefetch=2,snapshot=True,emit=lambda *a,**k:None):
            fd.download_and_append_fragments({'filename':'a'},make(3),{'is_live':True,'extractor_key':'Twitch','protocol':'m3u8'})
    assert cancel.is_set() and FragmentContract.download_and_append_fragments is original

def test_patch_rejects_changed_signature():
    class Changed(FragmentContract):
        def download_and_append_fragments(self,x):pass
    with pytest.raises(RuntimeError,match='signature'):
        with _patch_class(Changed,Event(),prefetch=2,snapshot=False,emit=lambda *a,**k:None):pass

def test_checkpoint_uses_ordered_filename_not_racing_progress_count():
    class Racing(FragmentContract):
        def _append_fragment(self,ctx,data):
            self.checkpoint=ctx['fragment_index']
            del ctx['fragment_filename_sanitized']
    fd=Racing();ctx={'filename':'a','fragment_filename_sanitized':'a.part-Frag7','fragment_index':12}
    with _patch_class(Racing,Event(),prefetch=2,snapshot=False,emit=lambda *a,**k:None):
        fd._append_fragment(ctx,b'bytes')
    assert fd.checkpoint==7
    assert 'fragment_filename_sanitized' not in ctx
