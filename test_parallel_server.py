"""Offline tests of VPS segmented HTTP backend. No CDN/Telegram/network calls."""
import io
import os
import sys
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

class TestRequest(threading.local):
    headers = {}
    content_length = None
    stream = None
    def get_json(self, silent=True):
        return {'segments': 6, 'title': 'test video'}

with tempfile.TemporaryDirectory() as d:
    os.environ.update(RELAY_DATA_DIR=d, RELAY_REQUIRE_UPLOAD_KEY='0',
                      TELEGRAM_PROGRESS_ENABLED='0', BOT_TOKEN='TEST',
                      TELEGRAM_API_ID='123', TELEGRAM_API_HASH='TEST',
                      TELEGRAM_CHAT_ID='123', RELAY_MAX_GB='.7', RELAY_PARALLEL_UPLOADS='3')
    fake = types.ModuleType('flask')
    fake.request = TestRequest()
    fake.jsonify = lambda **kwargs: kwargs
    fake.send_file = lambda *args, **kwargs: None
    sys.modules['flask'] = fake
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import relay_api as bridge
    class App:
        def __init__(self): self.routes = {}
        def after_request(self, f): return f
        def route(self, path, methods): return self.post(path)
        def get(self, path): return self.post(path)
        def post(self, path):
            def deco(f): self.routes[f.__name__] = f; return f
            return deco
    app = App()
    bridge.install_relay_routes(app)
    req = fake.request

    def start(n):
        req.headers = {}
        req.get_json = lambda silent=True: {'segments': n, 'title': 'Test'}
        payload, status = app.routes['relay_start']()
        assert status == 201, (payload, status)
        return payload['job_id']

    def segment(i):
        data = bytearray(188*2)
        data[0] = data[188] = 0x47
        data[1:6] = f'{i:05d}'.encode()
        return bytes(data)

    def post(job_id, i, data=None):
        if data is None: data = segment(i)
        req.headers = {}
        req.content_length = len(data)
        req.stream = io.BytesIO(data)
        return app.routes['relay_segment'](job_id, i)

    # Each launch is a separate test case, because the DB registry has 1 job limit.
    for count in (1,2,3,6):
        job_id = start(count)
        job = bridge._job(job_id)
        # Send in REVERSE order, including truly simultaneous HTTP request handlers.
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda i: post(job_id, i), reversed(range(count))))
        assert all(isinstance(r, dict) and r.get('received') for r in results), results
        assert (job['folder'] / 'input.ts').read_bytes() == b''.join(segment(i) for i in range(count)), 'Wrong segment order'
        assert job['next'] == count and job['received_count'] == count
        assert not job['inflight'] and job['inflight_bytes'] == 0
        assert not list((job['folder'] / 'segments').glob('*'))
        duplicate = post(job_id, 0)
        assert duplicate[1] == 409, duplicate
        convert_event = threading.Event()
        original = bridge._convert
        bridge._convert = lambda job: convert_event.set()
        out, code = app.routes['relay_finish'](job_id)
        assert (out['status'], code) == ('processing', 202)
        assert convert_event.wait(3), 'No conversion scheduled'
        bridge._convert = original
        job['status'] = 'sent'
        print(f'PASS: {count} segments reverse-order, exact assembly; duplicate rejected; finish gated')

    # Missing segment must NOT trigger conversion.
    job_id = start(3)
    assert isinstance(post(job_id, 2), dict)
    missing = app.routes['relay_finish'](job_id)
    assert missing[1] == 409, missing
    assert isinstance(post(job_id, 1), dict)
    assert isinstance(post(job_id, 0), dict)
    assert (bridge._job(job_id)['folder']/'input.ts').read_bytes() == segment(0)+segment(1)+segment(2)
    assert post(job_id, 9)[1] == 400
    assert post(job_id, 0)[1] == 409
    print('PASS: missing segment blocked finish; late segment accepted in order; invalid index blocked')
