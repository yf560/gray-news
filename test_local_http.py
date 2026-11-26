import json, threading, unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import server
class LocalChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.temp=tempfile.TemporaryDirectory(); cls.old_db=server.job_store.DB_PATH; server.job_store.DB_PATH=Path(cls.temp.name)/'jobs.sqlite'; server.job_store.init_db()
        cls.httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=cls.httpd.serve_forever,daemon=True).start()
        cls.base='http://127.0.0.1:'+str(cls.httpd.server_port)
    @classmethod
    def tearDownClass(cls): cls.httpd.shutdown(); cls.httpd.server_close(); server.job_store.DB_PATH=cls.old_db; cls.temp.cleanup()
    def request(self,path,body=None,origin=None):
        headers={'Host':'localhost:8765'}
        if body is not None: headers['Content-Type']='application/json'
        if origin: headers['Origin']=origin
        req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req) as r: return r.status,r.read()
        except HTTPError as e:
            with e: return e.code,e.read()
    def test_private_files(self):
        for p in ('/.env','/%2eenv','/.git/config','/server.py','/README.md','/assets/../.env'):
            self.assertEqual(self.request(p)[0],404,p)
    def test_public_files(self):
        for p in ('/','/index.html','/assets/app.css','/assets/gray-mark-dark.svg'):
            self.assertEqual(self.request(p)[0],200,p)
    def test_invalid_inputs(self):
        for body in ({},[],{'url':'javascript:alert(1)'},{'text':4},{'text':'x'*50001}):
            self.assertEqual(self.request('/api/analyze',body)[0],400)
    def test_cross_origin(self):
        self.assertEqual(self.request('/api/analyze',{'text':'abc'},'https://example.org')[0],403)
    def test_api_contract(self):
        with patch.dict(server.os.environ, {'EXAONE_API_KEY':'fake','TAVILY_API_KEY':'fake'}), patch('server.analyze', return_value={'version':1,'title':'t','summary':'s','issues':[],'sources':[]}):
            status,body=self.request('/api/analyze',{'url':'','text':'기사'})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['version'],1)
    def test_rate_limit_response(self):
        with patch.dict(server.os.environ, {'EXAONE_API_KEY':'fake','TAVILY_API_KEY':'fake'}), patch('server.analyze',side_effect=server.RateLimited('잠시 후 다시 시도')):
            status,body=self.request('/api/analyze',{'text':'기사'})
            self.assertEqual(status,429)
            self.assertEqual(json.loads(body)['code'],'RATE_LIMITED')
    def test_job_submit_and_list(self):
        with patch.dict(server.os.environ, {'EXAONE_API_KEY':'fake','TAVILY_API_KEY':'fake'}):
            status,body=self.request('/api/jobs',{'text':'뉴스 본문'})
        self.assertEqual(status,202); job=json.loads(body); self.assertEqual(job['status'],'queued')
        status,body=self.request('/api/jobs'); self.assertEqual(status,200); self.assertEqual(json.loads(body)['items'][0]['id'],job['id'])
        status,body=self.request('/api/jobs/'+job['id']); self.assertEqual(status,200); self.assertIsNone(json.loads(body)['result'])
    def test_job_rejects_remote_origin(self):
        status,_=self.request('/api/jobs',{'text':'뉴스'},'https://evil.test'); self.assertEqual(status,403)
    def test_failure(self):
        with patch.dict(server.os.environ, {'EXAONE_API_KEY':'fake','TAVILY_API_KEY':'fake'}), patch('server.analyze',side_effect=server.AnalysisFailure('upstream')):
            self.assertEqual(self.request('/api/analyze',{'text':'기사'})[0],502)
if __name__=='__main__': unittest.main()
