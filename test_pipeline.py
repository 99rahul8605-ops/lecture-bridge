"""Offline regression tests; do not contact Telegram or CDN."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parent
SRC_PARALLEL = Path(os.environ.get('TEST_RELAY_PARALLEL', str(ROOT/'tests/fixtures/parallel_relay.py')))
SRC_SEQUENTIAL = Path(os.environ.get('TEST_RELAY_SEQUENTIAL', str(ROOT/'tests/fixtures/sequential_relay.py')))
spec = importlib.util.spec_from_file_location('pipeline_installer', ROOT / 'install_pipeline.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class StubRequest:
    def __init__(self):
        self.body = {'segments': 1, 'title': 'Test lecture'}
    def get_json(self, silent=True):
        return self.body


class StubApp:
    def __init__(self):
        self.routes = {}
    def _register(self, path, fn):
        self.routes[path] = fn
        return fn
    def post(self, path):
        return lambda fn: self._register(path, fn)
    def get(self, path):
        return lambda fn: self._register(path, fn)
    def route(self, path, methods=None):
        return lambda fn: self._register(path, fn)
    def after_request(self, fn):
        return fn


class StubThread:
    threads = []
    def __init__(self, target, args=(), daemon=True):
        self.target = target
        self.args = args
        self.daemon = daemon
        StubThread.threads.append(self)
    def start(self):
        return None


class PipelineTests(unittest.TestCase):
    def test_parallel_patch_idempotent(self):
        orig = SRC_PARALLEL.read_text()
        patched = installer.patch_text(orig)
        self.assertIn('MAX_BACKGROUND_JOBS', patched)
        self.assertIn('status="queued"', patched)
        self.assertIn('with _conversion_lock:', patched)
        self.assertEqual(installer.patch_text(patched), patched)

    def test_sequential_patch_idempotent(self):
        orig = SRC_SEQUENTIAL.read_text()
        patched = installer.patch_text(orig)
        self.assertEqual(installer.patch_text(patched), patched)

    def test_browser_does_not_wait_for_telegram(self):
        src = (ROOT/'Railway_Auto_Telegram.user.js').read_text()
        self.assertNotIn('await poll(job.job_id, key)', src)
        self.assertIn('NEXT lecture now', src)
        self.assertIn('await api(`/api/relay/${encodeURIComponent(job.job_id)}/finish`', src)
        self.assertIn('RETRY_BACKOFF_MS', src)

    def _load(self):
        app_root = tempfile.TemporaryDirectory()
        self.addCleanup(app_root.cleanup)
        os.environ['RELAY_DATA_DIR'] = app_root.name
        os.environ['TELEGRAM_PROGRESS_ENABLED'] = '0'
        os.environ['RELAY_MAX_JOBS'] = '1'
        os.environ['RELAY_BACKGROUND_JOBS'] = '2'
        request = StubRequest()
        fake_flask = types.ModuleType('flask')
        fake_flask.request = request
        fake_flask.jsonify = lambda **d: d
        fake_flask.send_file = lambda *a, **kw: None
        with patch.dict(sys.modules, {'flask': fake_flask}):
            mod = types.ModuleType('relay_test')
            exec(compile(installer.patch_text(SRC_PARALLEL.read_text()), 'relay_test.py', 'exec'), mod.__dict__)
        mod._authorized = lambda: True
        mod._telegram_setup_error = lambda: None
        StubThread.threads = []
        mod.threading.Thread = StubThread
        app = StubApp()
        mod.install_relay_routes(app)
        return mod, app

    @staticmethod
    def _status(response):
        if isinstance(response, tuple):
            return response[1]
        return 200

    def test_first_finishes_then_second_can_start(self):
        mod, app = self._load()
        start = app.routes['/api/relay/start']
        finish = app.routes['/api/relay/<job_id>/finish']
        first, status = start()
        self.assertEqual(status, 201)
        id1 = first['job_id']
        # Do not wait for Telegram processing; stage it ready.
        mod._jobs[id1].update(received_count=1, next=1)
        result, status = finish(id1)
        self.assertEqual(status, 202)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(mod._jobs[id1]['status'], 'queued')
        second, status = start()
        self.assertEqual(status, 201)
        self.assertNotEqual(id1, second['job_id'])
        third, status = start()
        self.assertEqual(status, 429)  # only one concurrent Chrome upload
        self.assertEqual(third['active_status'], 'uploading')

    def test_backpressure_caps_telegram_backlog(self):
        mod, app = self._load()
        start = app.routes['/api/relay/start']
        finish = app.routes['/api/relay/<job_id>/finish']
        ids = []
        for i in range(2):
            job, st = start()
            self.assertEqual(st, 201)
            job_id = job['job_id']
            ids.append(job_id)
            mod._jobs[job_id].update(received_count=1, next=1)
            _, st = finish(job_id)
            self.assertEqual(st, 202)
        blocked, st = start()
        self.assertEqual(st, 429)
        self.assertEqual(blocked['active_status'], 'background_full')
        mod._jobs[ids[0]]['status'] = 'sent'
        accepted, st = start()
        self.assertEqual(st, 201)

    def test_conversion_and_telegram_are_serial(self):
        mod, app = self._load()
        a, _ = app.routes['/api/relay/start']()
        job_a = mod._jobs[a['job_id']]
        job_a.update(status='queued')
        # Directly insert a second queued job; conversion lock must serialize both.
        b_id = 'test-b'
        mod._jobs[b_id] = dict(job_a, status='queued', lock=threading.Lock())
        started = []
        release = threading.Event()
        def fake_serial(job_id):
            started.append(job_id)
            if job_id == a['job_id']:
                release.wait(timeout=2)
            mod._jobs[job_id]['status'] = 'sent'
        mod._convert_serial = fake_serial
        # Use saved original class: the stub captures scheduled background jobs.
        original_thread = thread_original
        t1 = original_thread(target=mod._convert, args=(a['job_id'],))
        t1.start()
        deadline = time.time()+2
        while not started and time.time()<deadline: time.sleep(.01)
        t2 = original_thread(target=mod._convert, args=(b_id,))
        t2.start()
        try:
            time.sleep(.1)
            self.assertEqual(started, [a['job_id']])
            self.assertEqual(mod._jobs[b_id]['status'], 'queued')
        finally:
            release.set()
            t1.join(2)
            t2.join(2)
        self.assertEqual(started, [a['job_id'], b_id])


thread_original = threading.Thread

if __name__ == '__main__':
    unittest.main(verbosity=2)
