"""Socket-free checks of the real server dispatcher and new export contracts."""
import unittest,tempfile,json,io,sys
from pathlib import Path
from unittest.mock import patch,MagicMock
from email.message import Message
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'marketscope'))
import server
from paper_watchlist import PaperWatchlist

class WatchlistRouteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.watch=PaperWatchlist(Path(self.tmp.name)/'test.db',Path(self.tmp.name)/'missing-candles.db')
  p=patch.object(server,'get_paper_watchlist',return_value=self.watch);p.start();self.addCleanup(p.stop)
  p=patch.object(server,'rate_limiter',server.RateLimiter(1000));p.start();self.addCleanup(p.stop)
 def get(self,path):
  h=object.__new__(server.MarketScopeHandler);h.path=path;h.client_address=('127.0.0.1',123)
  h.headers=Message();h.server=MagicMock();h.server.db_manager.db_path=str(Path(self.tmp.name)/'test.db');h.db=h.server.db_manager
  output={}
  h._send_json=lambda d,status=200:output.update(status=status,mime='application/json',body=json.dumps(d))
  h._send_text=lambda d,content_type='text/plain',status=200:output.update(status=status,mime=content_type,body=d)
  h.do_GET();return output
 def test_readonly_empty_status(self):
  r=self.get('/api/paper-watchlist');self.assertEqual(r['status'],200);self.assertEqual(json.loads(r['body'])['trades'],[])
 def test_csv_and_json_export(self):
  for fmt,mime in [('csv','text/csv'),('json','application/json')]:
   r=self.get('/api/paper-watchlist/export?format='+fmt);self.assertEqual(r['status'],200);self.assertTrue(r['mime'].startswith(mime))
 def test_invalid_report_date_and_format(self):
  self.assertEqual(self.get('/api/paper-watchlist/report?day=invalid')['status'],400)
  self.assertEqual(self.get('/api/paper-watchlist/export?format=html')['status'],400)
 def test_no_saved_report_never_fabricates_results(self):
  r=json.loads(self.get('/api/paper-watchlist/report')['body']);self.assertEqual(r['status'],'EMPTY');self.assertNotIn('metrics',r)
 def test_failure_returns_no_simulated_values(self):
  with patch.object(server,'get_paper_watchlist',side_effect=RuntimeError('test unavailable')):
   r=self.get('/api/paper-watchlist');self.assertEqual(r['status'],503);self.assertNotIn('net_pnl',r['body'])

if __name__=='__main__':unittest.main()
