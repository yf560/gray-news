import tempfile, unittest
from pathlib import Path
import job_store as store
class JobStoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.old=store.DB_PATH; store.DB_PATH=Path(self.temp.name)/'test.sqlite'; store.init_db()
 def tearDown(self): store.DB_PATH=self.old; self.temp.cleanup()
 def test_submit_resume(self):
  j,_=store.create_job({'text':'article'},'same'); d,c=store.create_job({'text':'article'},'same'); self.assertFalse(c); self.assertEqual(j['id'],d['id'])
  store.claim_next(); store.checkpoint(j['id'],'plan',{'issues':[]}); self.assertEqual(store.saved_stage(j['id'],'plan'),{'issues':[]})
  store.init_db(); self.assertEqual(store.get_job(j['id'])['status'],'interrupted'); self.assertEqual(store.resume(j['id']),'queued')
 def test_cancel(self):
  j,_=store.create_job({'text':'article'},'hash'); store.claim_next(); self.assertEqual(store.delete_job(j['id']),'conflict')
  store.request_cancel(j['id'])
  with self.assertRaises(InterruptedError): store.checkpoint(j['id'],'plan',{})
  store.finish_cancel(j['id']); self.assertEqual(store.delete_job(j['id']),'deleted')
if __name__=='__main__': unittest.main()
