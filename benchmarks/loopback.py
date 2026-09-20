"""Synthetic scheduling benchmark. NOT a YouTube, yt-dlp, or GPU benchmark.
The old scheduling primitive (eager Executor.map) is compared at equal workers.
All data is served from an owned loopback HTTP fixture; SHA-256 must match.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
from threading import Thread
import time
from urllib.request import urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from livecatch_core.parallel import OrderedPrefetch

COUNT=32;SIZE=32*1024;REQUEST_DELAY=.01;EDGE_PAUSE=.16;REPEATS=3
payloads=[bytes([i])*SIZE for i in range(COUNT)]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        data=payloads[int(self.path.strip('/'))];time.sleep(REQUEST_DELAY)
        self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

class Server(ThreadingHTTPServer):
    request_queue_size=64
    daemon_threads=True
server=Server(('127.0.0.1',0),Handler)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
def fetch(i):
    with urlopen(f'http://127.0.0.1:{server.server_port}/{i}',timeout=3) as response:return response.read()
def source(pause):
    for i in range(COUNT):
        if i==8 and pause:time.sleep(EDGE_PAUSE)
        yield i

def run(mode,pause):
    begin=time.perf_counter();first=None;digest=hashlib.sha256()
    pool=None
    try:
        if mode=='sequential':results=map(fetch,source(pause))
        elif mode=='eager_8':pool=ThreadPoolExecutor(8);results=pool.map(fetch,source(pause))
        else:pool=OrderedPrefetch(8,16);results=pool.map(fetch,source(pause))
        for part in results:
            if first is None:first=time.perf_counter()-begin
            digest.update(part)
    finally:
        if pool:pool.shutdown()
    assert digest.hexdigest()==hashlib.sha256(b''.join(payloads)).hexdigest()
    return {'seconds':time.perf_counter()-begin,'first_commit_seconds':first,'sha256':digest.hexdigest()}
try:
    rows=[]
    for pause in (False,True):
        for mode in ('sequential','eager_8','bounded_8'):
            samples=[run(mode,pause) for _ in range(REPEATS)]
            rows.append({'scenario':'live_edge_pause' if pause else 'finite_backlog','mode':mode,
                         'median_seconds':statistics.median(x['seconds'] for x in samples),
                         'median_first_commit_seconds':statistics.median(x['first_commit_seconds'] for x in samples),
                         'samples':samples})
    print(json.dumps({'kind':'synthetic_loopback_not_real_site','python':sys.version.split()[0],
                      'os':platform.system(),'fragments':COUNT,'fragment_bytes':SIZE,
                      'injected_request_latency_seconds':REQUEST_DELAY,'injected_edge_pause_seconds':EDGE_PAUSE,
                      'repeats':REPEATS,'results':rows},indent=2))
finally:server.shutdown();server.server_close();thread.join()
