"""Real pinned yt-dlp, real HTTP, no external sites/credentials/GPU required.
Skipped explicitly when yt-dlp is not installed; CI installs requirements.txt.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Event,Thread
import time
import pytest

yt_dlp=pytest.importorskip('yt_dlp',reason='real yt-dlp dependency not installed')
from yt_dlp.downloader.fragment import FragmentFD
from yt_dlp.downloader.dash import DashSegmentsFD
from livecatch_core.ytdlp_patch import fragment_patch

PARTS=[bytes([i])*1024 for i in range(12)]

@contextmanager
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            index=int(self.path.split('?')[0].strip('/'))
            if index>=len(PARTS):self.send_error(404);return
            data=PARTS[index];time.sleep(.002*(index%3))
            self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    class Server(ThreadingHTTPServer):
        request_queue_size=64
        daemon_threads=True
    http=Server(('127.0.0.1',0),Handler)
    thread=Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}'
    finally:http.shutdown();http.server_close();thread.join()

@pytest.mark.parametrize('workers',[1,4,8])
def test_real_fragmentfd_ordered_bytes(tmp_path,workers):
    params={'quiet':True,'noprogress':True,'concurrent_fragment_downloads':workers,'retries':0,'fragment_retries':0,'skip_unavailable_fragments':False}
    info={'id':'owned-fixture','title':'owned','ext':'bin','format_id':'v','protocol':'http_dash_segments'}
    filename=tmp_path/'ordered.bin'
    with server() as base,yt_dlp.YoutubeDL(params) as ydl,fragment_patch(Event()):
        fd=DashSegmentsFD(ydl,params)
        ctx={'filename':str(filename),'total_frags':len(PARTS)}
        fd._prepare_and_start_frag_download(ctx,info)
        fragments=[{'frag_index':i+1,'index':i,'fragment_count':len(PARTS),'url':f'{base}/{i}'} for i in range(len(PARTS))]
        assert fd.download_and_append_fragments(ctx,fragments,info)
    assert filename.read_bytes()==b''.join(PARTS)
    assert not list(tmp_path.glob('*Frag*')) and not list(tmp_path.glob('*.ytdl'))

def test_real_two_stream_live_snapshot(tmp_path):
    params={'quiet':True,'noprogress':True,'concurrent_fragment_downloads':8,'retries':0,'fragment_retries':0,'skip_unavailable_fragments':False}
    info={'id':'owned','title':'owned','ext':'bin','extractor_key':'Youtube','is_live':True,'protocol':'http_dash_segments_generator'}
    with server() as base,yt_dlp.YoutubeDL(params) as ydl,fragment_patch(Event(),snapshot=True):
        fd=DashSegmentsFD(ydl,params);args=[]
        def generate(head):
            for i in range(head):yield {'frag_index':i+1,'index':i,'fragment_count':head,'url':f'{base}/{i}?sq={i}'}
            raise AssertionError('live generator read past cutoff')
        for name,head in [('video',12),('audio',10)]:
            ctx={'filename':str(tmp_path/f'{name}.bin'),'total_frags':None,'live':'is_from_start'}
            fmt={**info,'format_id':name};fd._prepare_and_start_frag_download(ctx,fmt)
            args.append((ctx,generate(head),fmt))
        assert fd.download_and_append_fragments_multiple(*args)
    assert (tmp_path/'video.bin').read_bytes()==(tmp_path/'audio.bin').read_bytes()==b''.join(PARTS[:10])
