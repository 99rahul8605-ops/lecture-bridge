#!/usr/bin/env python3
"""Standalone smoke tests for public/no-key routes without Flask or Railway network."""
from pathlib import Path
from unittest.mock import patch
import importlib.util
import tempfile
import threading
import types
import sys
import unittest

root = Path(__file__).resolve().parent
# Mock only the external interfaces so this runs without Flask or Telegram credentials.
req = types.SimpleNamespace(headers={}, path='', is_json=False, content_length=0, get_json=lambda silent=True: None)
flask = types.ModuleType('flask')
flask.request = req
flask.jsonify = lambda **kw: kw
sys.modules['flask'] = flask
relay = types.ModuleType('relay_api')
relay.ROOT = Path(tempfile.mkdtemp())
relay.MAX_TOTAL = 700 * 1024**2
relay._jobs={}
relay._jobs_lock=threading.Lock()
relay._allowed_origin=lambda: 'https://vidcloud.eu.org'
relay._clean_title=lambda title: str(title or 'Lecture')[:140]
relay._job=lambda jid: None
sys.modules['relay_api']=relay
spec=importlib.util.spec_from_file_location('cloud_queue',root/'cloud_queue.py')
cloud=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)

class FakeApp:
    def __init__(self):self.routes={};self.after=[]
    def after_request(self,func):self.after.append(func);return func
    def route(self,path,methods):return self.decorator(path,methods[0])
    def get(self,path):return self.decorator(path,'GET')
    def post(self,path):return self.decorator(path,'POST')
    def decorator(self,path,method):
        def wrapper(func):self.routes[(method,path)]=func;return func
        return wrapper

class Smoke(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prev_db,self.prev_root=cloud.DB,cloud.ROOT
        cloud.ROOT=Path(self.tmp.name)
        cloud.DB=cloud.ROOT/'queue.sqlite3'
        self.addCleanup(lambda: setattr(cloud,'DB',self.prev_db))
        self.addCleanup(lambda: setattr(cloud,'ROOT',self.prev_root))
        cloud._started=True # Do not run worker; just test route behavior.
        self.app=FakeApp();cloud.install_cloud_routes(self.app)
        dns=patch.object(cloud.socket,'getaddrinfo', return_value=[(2,1,6,'',('104.18.5.6',443))])
        dns.start();self.addCleanup(dns.stop)
        req.headers={};req.path='';req.is_json=False;req.content_length=0
        req.get_json=lambda silent=True: None
    def call(self,method,path,body=None):
        req.path=path
        req.is_json=body is not None
        req.content_length=0
        req.get_json=lambda silent=True:body
        return self.app.routes[(method,path)]()
    def test_public_status_without_key(self):
        response=self.call('GET','/api/cloud/queue')
        self.assertEqual(response[1],200)
        self.assertEqual(response[0]['jobs'],[])
    def test_public_enqueue_and_duplicate_without_key(self):
        data={'manifest':'https://bunny-cdn-qbg-s6.testwave.cc/file.docx?Policy=opaque','videoId':'video_999','title':'Lecture 9'}
        created=self.call('POST','/api/cloud/enqueue',data)
        self.assertEqual(created[1],201)
        self.assertNotIn('manifest',created[0]['job'])
        again=self.call('POST','/api/cloud/enqueue',data)
        self.assertEqual(again[1],200)
        self.assertTrue(again[0]['duplicate'])
        listed=self.call('GET','/api/cloud/queue')
        self.assertEqual(listed[1],200)
        self.assertEqual(len(listed[0]['jobs']),1)
        self.assertNotIn('opaque',str(listed))
    def test_public_endpoint_still_validates_url(self):
        invalid={'manifest':'http://127.0.0.1/private','videoId':'video_999','title':'Lecture 9'}
        result=self.call('POST','/api/cloud/enqueue',invalid)
        self.assertEqual(result[1],400)
    def test_public_queue_cap_preserved(self):
        with patch.object(cloud,'MAX_QUEUE',1):
            for i in range(2):
                data={'manifest':'https://bunny-cdn-qbg-s6.testwave.cc/file.docx?Policy=opaque','videoId':f'video_{i+999}','title':'Lecture'}
                r=self.call('POST','/api/cloud/enqueue',data)
                self.assertEqual(r[1],201 if i==0 else 429)

if __name__=='__main__':unittest.main(verbosity=2)
