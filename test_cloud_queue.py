#!/usr/bin/env python3
"""Purely offline, no CDN or Telegram calls. Unit + Flask route tests."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

# Fixture fallback lets package tests run even before it is put beside relay_api.py.
if 'relay_api' not in sys.modules and not Path(__file__).with_name('relay_api.py').exists():
    root=Path(tempfile.mkdtemp(prefix='cloud-unit-relay-'))
    relay=types.ModuleType('relay_api')
    relay.ROOT=root
    relay.MAX_TOTAL=700*1024**2
    relay._jobs={}
    relay._jobs_lock=threading.Lock()
    relay._allowed_origin=lambda:'https://vidcloud.eu.org'
    relay._clean_title=lambda x: (str(x or 'Lecture')[:140])
    relay._convert=lambda jobid: relay._jobs[jobid].update(status='sent')
    sys.modules['relay_api']=relay

# On a standalone ZIP check, Flask may not be installed. Non-Flask parsing,
# SSRF and end-to-end mock tests still run. On Railway all tests run.
HAVE_FLASK = importlib.util.find_spec('flask') is not None
if not HAVE_FLASK:
    fake_flask=types.ModuleType('flask')
    fake_flask.jsonify=lambda **kw: kw
    fake_flask.request=None
    sys.modules['flask']=fake_flask
import cloud_queue as cloud
if HAVE_FLASK:
    from flask import Flask

VALID='https://bunny-cdn-qbg-s6.testwave.cc/media/file.docx?Policy=opaque'

def public_dns(*a,**k):
    return [(2,1,6,'',('104.18.5.6',443))]


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.orig_db,self.orig_root=cloud.DB,cloud.ROOT
        cloud.ROOT=Path(self.tmp.name)
        cloud.DB=cloud.ROOT/'queue.sqlite3'
        self.addCleanup(lambda:setattr(cloud,'DB',self.orig_db))
        self.addCleanup(lambda:setattr(cloud,'ROOT',self.orig_root))
        self.dns=patch.object(cloud.socket,'getaddrinfo',side_effect=public_dns)
        self.dns.start();self.addCleanup(self.dns.stop)
    def test_url_allowlist(self):
        self.assertEqual(cloud._validate_url(VALID),VALID)
        for bad in ('http://'+cloud.HOST+'/m.m3u8','https://localhost/video',
                    'https://127.0.0.1/foo','file:///etc/passwd',
                    'https://'+cloud.HOST+'.evil.org/a',
                    'https://foo@'+cloud.HOST+'/x',
                    'https://'+cloud.HOST+':8443/x'):
            with self.subTest(url=bad),self.assertRaises(cloud.CloudError):cloud._validate_url(bad)
    def test_reject_private_dns(self):
        with patch.object(cloud.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(cloud.CloudError):cloud._validate_url(VALID)
    def test_vod_and_segments(self):
        data=b'#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:5,\n0001.pdf\n#EXTINF:5,\n0002.pdf\n#EXT-X-ENDLIST\n'
        with patch.object(cloud,'_fetch_bytes',return_value=data):
            self.assertEqual(len(cloud._get_playlist(VALID)),2)
    def test_master_playlist_variant(self):
        master=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nmedia.docx\n'
        media=b'#EXTM3U\n#EXTINF:5,\n0001.pdf\n#EXT-X-ENDLIST\n'
        with patch.object(cloud,'_fetch_bytes',side_effect=[master,media]):
            result=cloud._get_playlist(VALID)
            self.assertEqual(result[0].split('?')[0], 'https://'+cloud.HOST+'/media/0001.pdf')
    def test_drm_and_live_not_downloaded(self):
        for data in (b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n0001.pdf\n#EXT-X-ENDLIST',
                     b'#EXTM3U\n#EXTINF:5,\n0001.pdf',
                     b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n0001.pdf\n#EXT-X-ENDLIST',
                     b'#EXTM3U\n#EXTINF:5,\nhttps://127.0.0.1/private\n#EXT-X-ENDLIST'):
            with patch.object(cloud,'_fetch_bytes',return_value=data):
                with self.assertRaises(cloud.CloudError):cloud._get_playlist(VALID)
    @unittest.skipUnless(HAVE_FLASK, "Flask not installed in offline build container")
    def test_auth_and_enqueue_duplicate_and_status_no_url_leak(self):
        cloud._started=True
        app=Flask(__name__)
        cloud.install_cloud_routes(app)
        client=app.test_client()
        data={'manifest':VALID,'videoId':'lecture_987','title':'Lecture 4'}
        self.assertEqual(client.post('/api/cloud/enqueue',json=data).status_code,401)
        with patch.object(cloud,'_secret',return_value='A'*36):
            headers={'X-Cloud-Queue-Key':'A'*36, 'Origin':'https://vidcloud.eu.org'}
            first=client.post('/api/cloud/enqueue',json=data,headers=headers)
            self.assertEqual(first.status_code,201)
            second=client.post('/api/cloud/enqueue',json=data,headers=headers)
            self.assertEqual(second.status_code,200)
            self.assertTrue(second.json['duplicate'])
            self.assertNotIn('Policy',str(first.json))
            stats=client.get('/api/cloud/queue',headers=headers)
            self.assertEqual(stats.json['jobs'][0]['state'],'waiting')
            self.assertEqual(stats.json['jobs'][0]['title'],'Lecture 4')
            self.assertNotIn('manifest',str(stats.json))
            denied=client.post('/api/cloud/enqueue',json={**data,'videoId':'lecture_988','manifest':'http://127.0.0.1/'},headers=headers)
            self.assertEqual(denied.status_code,400)
    def test_segment_streaming_and_bound(self):
        payload=b'G'*376
        class Response(io.BytesIO):
            status=200
            headers={'Content-Length':str(len(payload))}
        class Opener:
            def open(self, *a, **kw): return Response(payload)
        temp=Path(self.tmp.name)/'incoming.part'
        with patch.object(cloud,'_OPENER',Opener()):
            self.assertEqual(cloud._download_part(VALID,temp,1000),len(payload))
            self.assertEqual(temp.read_bytes(),payload)
            with self.assertRaises(cloud.CloudError):cloud._download_part(VALID,temp,20)

    def test_video_reaches_original_telegram_pipeline(self):
        relay=cloud.relay
        origroot=relay.ROOT
        relay.ROOT=Path(self.tmp.name)
        self.addCleanup(lambda:setattr(relay,'ROOT',origroot))
        data=b'G'+b'0'*187+b'G'+b'1'*187
        with cloud._open_db() as db:
            db.execute('INSERT INTO jobs (id,video_id,title,manifest,state,created,updated) VALUES (?,?,?,?,?,?,?)',
                       ('123','video_123','Test Lecture',VALID,'waiting',1000,1000))
            db.commit()
            row=db.execute('SELECT * FROM jobs WHERE id=?',('123',)).fetchone()
        old_convert=relay._convert
        def fake_convert(jobid):
            job=relay._jobs[jobid]
            self.assertEqual((job['folder']/'input.ts').read_bytes(), data)
            job['status']='sent'
        relay._convert=fake_convert
        self.addCleanup(lambda:setattr(relay,'_convert',old_convert))
        try:
            def fake_download_part(url, part, limit):
                part.write_bytes(data)
                return len(data)
            with patch.object(cloud,'_get_playlist',return_value=[VALID]),patch.object(cloud,'_download_part',side_effect=fake_download_part):
                cloud._process(row)
        finally:
            relay._jobs.clear()
        with cloud._open_db() as db:
            updated=db.execute('SELECT * FROM jobs WHERE id=?',('123',)).fetchone()
        self.assertEqual(updated['state'],'sent')
        self.assertEqual(updated['received'],1)
        self.assertFalse((relay.ROOT/'cloud-123').exists())


if __name__=='__main__':unittest.main(verbosity=2)
