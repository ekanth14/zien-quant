"""Exercise real HTTP route dispatch/auth without opening a network socket."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from paper_trading import PaperSession
from email.message import Message
from unittest.mock import patch, MagicMock
import unittest
import tempfile
import json
import io

class PaperHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paper=PaperSession(Path(self.tmp.name)/'paper.json')
        self.user=None
        self.patch=patch.object(server,'get_paper_session',return_value=self.paper)
        self.patch.start();self.addCleanup(self.patch.stop)
        self.rate=patch.object(server,'rate_limiter',server.RateLimiter(1000));self.rate.start();self.addCleanup(self.rate.stop)
    def request(self,path,body=None,headers=None):
        handler=object.__new__(server.MarketScopeHandler)
        handler.path=path;handler.client_address=('127.0.0.1',12345)
        handler.headers=Message();handler.headers['Host']='127.0.0.1:8766'
        raw=json.dumps(body).encode() if body is not None else b''
        handler.headers['Content-Length']=str(len(raw))
        for k,v in (headers or {}).items():handler.headers[k]=v
        handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO()
        handler._get_current_session=lambda:self.user
        output={}
        def send_json(payload,status=200):output.update(status=status,mime='application/json',body=json.dumps(payload).encode())
        def send_text(text,content_type='text/plain',status=200):output.update(status=status,mime=content_type,body=text.encode())
        handler._send_json=send_json;handler._send_text=send_text
        (handler.do_GET if body is None else handler.do_POST)()
        return output['status'],output['mime'],output['body']
    def test_status_is_read_only_and_default_unconfigured(self):
        status,_,data=self.request('/api/paper/status')
        self.assertEqual(status,200);self.assertEqual(json.loads(data)['state'],'No strategy configured')
        self.assertFalse(self.paper.path.exists())
    def test_control_requires_owner_and_csrf(self):
        self.assertEqual(self.request('/api/paper/control',{'action':'start'})[0],403)
        self.user={'email':'test','role':'owner','csrf_token':'test-csrf'}
        self.assertEqual(self.request('/api/paper/control',{'action':'start'})[0],403)
        self.assertEqual(self.request('/api/paper/control',{'action':'start'},{'X-CSRF-Token':'test-csrf'})[0],200)
        self.assertEqual(self.paper.s['state'],'No strategy configured')
        self.user['role']='viewer'
        self.assertEqual(self.request('/api/paper/control',{'action':'end'},{'X-CSRF-Token':'test-csrf'})[0],403)
    def test_cross_origin_controls_rejected(self):
        self.user={'email':'test','role':'owner','csrf_token':'test-csrf'}
        status,_,_=self.request('/api/paper/control',{'action':'start'},{'X-CSRF-Token':'test-csrf','Origin':'https://unrelated.invalid'})
        self.assertEqual(status,403)
    def test_csv_mime_and_records_no_execution_routes(self):
        status,mime,data=self.request('/api/paper/export')
        self.assertEqual(status,200);self.assertTrue(mime.startswith('text/csv'))
        self.assertIn(b'PAPER portfolio',data)
        self.assertEqual(self.request('/api/paper/orders',{})[0],404)

if __name__=='__main__':unittest.main()
