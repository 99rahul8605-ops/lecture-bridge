#!/usr/bin/env python3
"""Dependency-free integration tests against the real relay layout, using fake Flask only.
No HTTP, Telegram messages, ffmpeg, web access, or production files touched.
"""
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

BASE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('resume_patcher', BASE/'install_resume_backend.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
FIXTURE = os.environ.get('RESUME_TEST_FIXTURE', '')
if not FIXTURE:
    # Optional local reference fixture. On Railway the live file is used with the
    # same syntax before installation; its content is never overwritten by tests.
    FIXTURE = str(BASE / 'relay_api.py') if (BASE / 'relay_api.py').is_file() else str(BASE / 'fixtures' / 'parallel_relay.py')
SRC = pathlib.Path(FIXTURE)

class FakeFlask:
    def __init__(self): self.routes = {}
    def route(self,path,**kw): return lambda f:self._add(path,f)
    def get(self,path): return lambda f:self._add(path,f)
    def post(self,path): return lambda f:self._add(path,f)
    def after_request(self,f): return f
    def _add(self,path,f): self.routes[path]=f; return f

class FakeRequest:
    def __init__(self):
        self.json = None
        self.stream = io.BytesIO(b'')
        self.content_length=0
        self.headers={}
        self.path = '/api/relay/start'
    def get_json(self,silent=False): return self.json

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{
            'RELAY_DATA_DIR':self.tmp.name,'TELEGRAM_PROGRESS_ENABLED':'0',
            'RELAY_MAX_GB':'0.5','RELAY_MAX_JOBS':'1','TELEGRAM_CHAT_ID':'-100123'})
        self.env.start();self.addCleanup(self.env.stop)
        self.request = FakeRequest()
        self.mod,self.app = self.load()
        self.key='b'*48
        self.fp='a'*64
        self.packet=bytearray(2*188)
        self.packet[0]=self.packet[188]=0x47

    def load(self):
        stub=types.ModuleType('flask')
        stub.request=self.request
        stub.jsonify=lambda **kwargs:kwargs
        stub.send_file=lambda *a,**kw: None
        with patch.dict(sys.modules,{'flask':stub}):
            mod=types.ModuleType('relay_resume_test')
            mod.__file__='test_relay.py'
            patched=installer.patch_text(SRC.read_text())
            exec(compile(patched,'relay_resume_test.py','exec'),mod.__dict__)
            app=FakeFlask()
            mod.install_relay_routes(app)
        mod._telegram_setup_error=lambda: None
        mod._authorized=lambda: True
        return mod, app

    def start(self,mod=None,app=None,**changes):
        mod=mod or self.mod; app=app or self.app
        self.request.json={"segments":3,"title":"Authorized lecture",
            "resume_key":self.key,"source_fingerprint":self.fp,**changes}
        return app.routes['/api/relay/start']()

    def send(self,jid,index):
        self.request.stream=io.BytesIO(self.packet)
        self.request.content_length=len(self.packet)
        return self.app.routes['/api/relay/<job_id>/segment/<int:index>'](jid,index)

    def test_patch_idempotent(self):
        source=SRC.read_text()
        changed=installer.patch_text(source)
        self.assertEqual(installer.patch_text(changed),changed)
        compile(changed,'relay_resume_test.py','exec')
        self.assertIn('def relay_pause(',changed)

    def test_cache_out_of_order_then_resume(self):
        result, status = self.start()
        self.assertEqual(status,201)
        jid=result['job_id']
        self.assertEqual(self.send(jid,2)['received'],1)
        self.assertEqual(self.send(jid,0)['received'],2)
        self.assertEqual(self.send(jid,0)[1],200)  # idempotent lost-ACK retry
        self.assertEqual(self.app.routes['/api/relay/<job_id>/pause'](jid)[0]['received_indices'],[0,2])
        self.assertEqual(self.mod._jobs[jid]['status'],'paused')
        second,status=self.start()
        self.assertEqual(status,200)
        self.assertEqual(second['job_id'],jid)
        self.assertTrue(second['resumed'])
        self.assertEqual(second['received_indices'],[0,2])
        self.assertEqual(self.send(jid,1)['received'],3)
        ts=(self.mod._jobs[jid]['folder']/'input.ts').read_bytes()
        self.assertEqual(len(ts),3*len(self.packet))
        self.mod._convert=lambda x:None
        finish,status=self.app.routes['/api/relay/<job_id>/finish'](jid)
        self.assertEqual(status,202)

    def test_restart_recovers_cache(self):
        result,_=self.start()
        jid=result['job_id']
        self.send(jid,0)
        self.send(jid,2)
        self.app.routes['/api/relay/<job_id>/pause'](jid)
        mod2,app2=self.load()  # Simulated process restart; same temp storage.
        self.assertIn(jid,mod2._jobs)
        res,status=self.start(mod2,app2)
        self.assertEqual(status,200)
        self.assertEqual(res['received_indices'],[0,2])

    def test_reaper_preserves_cache_and_releases_slot(self):
        result,_=self.start()
        jid=result['job_id']
        self.send(jid,0)
        self.mod._jobs[jid]['updated'] -= 7200
        self.mod._reap_abandoned_uploads()
        self.assertEqual(self.mod._jobs[jid]['status'],'paused')
        result2,_=self.start(resume_key='c'*48)
        self.assertNotEqual(result2['job_id'],jid)

    def test_mismatched_playlist_rejected(self):
        result,_=self.start()
        jid=result['job_id']
        self.app.routes['/api/relay/<job_id>/pause'](jid)
        error,status=self.start(source_fingerprint='c'*64)
        self.assertEqual(status,409)
        self.assertIn('Playlist changed',error['error'])

    def test_does_not_resume_processing_and_duplicate_send(self):
        result,_=self.start()
        jid=result['job_id']
        self.send(jid,0);self.send(jid,1);self.send(jid,2)
        self.mod._convert=lambda x:None
        self.app.routes['/api/relay/<job_id>/finish'](jid)
        ans,status=self.start()
        self.assertEqual(status,200)
        self.assertTrue(ans['already_finished'])

    def test_only_own_progress_message_deleted(self):
        calls=[]
        job={'status':'uploading', 'title':'Example', 'expected':5,'received_count':1,'next':1,
             'bytes':1000,'created':self.mod.time.time()}
        def bot(method,params):
            calls.append((method,params.copy()))
            if method=='sendMessage':
                job['status']='paused'
                return {'message_id':13579}
            return True
        self.mod._bot_api=bot
        self.mod.time.sleep=lambda x:None
        self.mod._progress_notifier(job)
        self.assertEqual([a[0] for a in calls],['sendMessage','deleteMessage'])
        self.assertEqual(calls[-1][1]['message_id'],13579)

if __name__=='__main__':
    unittest.main(verbosity=2)
