import importlib
import os
import tempfile
import unittest
from pathlib import Path

class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        os.environ['AUTO_CATALOG_DB']=str(Path(self.temp.name)/'catalog.sqlite3')
        import auto_catalog
        self.mod=importlib.reload(auto_catalog)
        self.mod.init_db()
        self.B='a'*24;self.S='b'*24;self.C='c'*24

    def tearDown(self):
        self.temp.cleanup()

    def test_full_selection_and_status(self):
        m=self.mod
        m.ingest(dict(kind='batches',items=[{'id':self.B,'name':'CA batch'}]))
        m.ingest(dict(kind='subjects',batchId=self.B,items=[{'id':self.S,'name':'Accounts','chapters':1,'lectures':3}]))
        m.ingest(dict(kind='chapters',batchId=self.B,subjectId=self.S,items=[{'id':self.C,'name':'AS','lectures':3}]))
        m.ingest(dict(kind='lectures',batchId=self.B,subjectId=self.S,chapterId=self.C,items=[
          {'videoId':'1'*24,'contentId':'7'*24,'title':'Lecture 01','status':'COMPLETED','videoStatus':'Ready','date':'2026-10-01'},
          {'videoId':'2'*24,'contentId':'8'*24,'title':'Lecture 02','status':'SCHEDULED','videoStatus':'','date':'2026-10-02'},
          {'videoId':'3'*24,'title':'Lecture 03','status':'COMPLETED','videoStatus':'Ready','date':'2026-10-03'}]))
        assert 'CA batch' in m.command(123,'batches')
        assert 'selected' in m.command(123,'batch','1')
        assert 'Accounts' in m.command(123,'subjects')
        m.command(123,'subject','1')
        assert 'AS' in m.command(123,'autochapters')
        m.command(123,'autochapter','1')
        assert 'Lecture 01' in m.command(123,'lectures')
        assert 'Added 2' in m.command(123,'add','all')
        assert 'already queued/delivered 2' in m.command(123,'add','all')
        assert m.command(123,'autopending')=='yes'
        assert '2 newly prepared' in m.command(123,'done')
        assert m.command(123,'autopending')=='no'
        assert m.mark_delivered('1'*24)==1
        assert 'delivered 1' in m.command(123,'queueauto')

    def test_no_media_url_persisted(self):
        m=self.mod
        m.ingest(dict(kind='lectures',batchId=self.B,subjectId=self.S,chapterId=self.C,items=[
          dict(videoId='1'*24,title='Safe lesson',videoUrl='https://secret.invalid/drm/master.mpd',
               token='secret',status='COMPLETED',videoStatus='Ready')]))
        data=Path(os.environ['AUTO_CATALOG_DB']).read_bytes()
        self.assertNotIn(b'secret.invalid',data)
        self.assertNotIn(b'master.mpd',data)
        self.assertIn(b'Safe lesson',data)

    def test_bad_ids(self):
        with self.assertRaises(ValueError):
            self.mod.ingest({'kind':'lectures','batchId':'invalid','items':[]})

if __name__=='__main__':unittest.main()
