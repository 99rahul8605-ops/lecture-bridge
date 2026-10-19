"""Safe, metadata-only tests; no website or Telegram needed."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old={k:os.environ.get(k) for k in ('OWNER_ID','AUTO_CATALOG_DB')}
        os.environ['OWNER_ID']='1234567'
        os.environ['AUTO_CATALOG_DB']=str(Path(self.temp.name)/'db.sqlite')
        import auto_catalog
        self.m=importlib.reload(auto_catalog)
        self.m.init_db()
        self.batch='a'*24;self.subject='b'*24;self.chapter='c'*24
        self.ids=['1'*24,'2'*24,'3'*24]
        self.browserA='browser_A1'
        self.browserB='browser_B2'
        self.m.ingest({'kind':'lectures','batchId':self.batch,'subjectId':self.subject,'chapterId':self.chapter,
                       'items':[{'videoId':x,'title':'Lesson '+str(i),'status':'COMPLETED',
                                 'videoStatus':'Ready'} for i,x in enumerate(self.ids)]})
        m=self.m
        m.ingest({'kind':'batches','items':[{'id':self.batch,'name':'Batch'}]})
        m.ingest({'kind':'subjects','batchId':self.batch,'items':[{'id':self.subject,'name':'Subject'}]})
        m.ingest({'kind':'chapters','batchId':self.batch,'subjectId':self.subject,
                  'items':[{'id':self.chapter,'name':'Chapter'}]})
        m.command(1234567,'batch','1');m.command(1234567,'subject','1')
        m.command(1234567,'autochapter','1')
        m.command(1234567,'add','all')

    def tearDown(self):
        for k,v in self.old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        self.temp.cleanup()

    def test_next_lease_and_order(self):
        m=self.m
        self.assertEqual(m.worker_next(self.browserA)['state'],'paused')
        self.assertIn('newly prepared',m.command(1234567,'done'))
        r=m.worker_next(self.browserA)
        self.assertEqual(r['state'],'assigned')
        first=r['lecture']['video_id']
        self.assertEqual(first,self.ids[0])
        self.assertEqual(r['lecture']['batch_id'],self.batch)
        self.assertEqual(m.worker_next(self.browserB)['state'],'busy')
        self.assertEqual(m.worker_next(self.browserA)['lecture']['video_id'],first)
        self.assertTrue(m.worker_heartbeat(self.browserA,first)['active'])
        self.assertFalse(m.worker_heartbeat(self.browserB,first)['active'])
        self.assertEqual(m.worker_finish(self.browserB,first,'delivered')['updated'],0)
        self.assertEqual(m.worker_finish(self.browserA,first,'delivered')['updated'],1)
        self.assertEqual(m.worker_next(self.browserA)['lecture']['video_id'],self.ids[1])
        self.assertIn('delivered 1',m.command(1234567,'queueauto'))

    def test_failed_and_stop_and_retry(self):
        m=self.m
        m.command(1234567,'done')
        first=m.worker_next(self.browserA)['lecture']['video_id']
        self.assertEqual(m.worker_finish(self.browserA,first,'failed','Authorization denied')['state'],'failed')
        self.assertIn('failed 1',m.command(1234567,'queueauto'))
        m.command(1234567,'autostop')
        self.assertEqual(m.worker_next(self.browserA)['state'],'paused')
        m.command(1234567,'autoretry')
        m.command(1234567,'autostart')
        result=m.worker_next(self.browserA)
        self.assertEqual(result['state'],'assigned')
        self.assertEqual(result['lecture']['video_id'],first)

    def test_only_completed_ready_picks_and_id_validation(self):
        m=self.m
        with self.assertRaises(ValueError):m.worker_next('bad')
        with self.assertRaises(ValueError):m.worker_finish(self.browserA,self.ids[0],'hacked')
        m.command(1234567,'done')
        data=Path(os.environ['AUTO_CATALOG_DB']).read_bytes()
        self.assertNotIn(b'master.mpd',data)
        self.assertNotIn(b'Signature',data)

if __name__=='__main__':unittest.main()
