"""Read-only research HTTP dispatch, no live server or account access."""
import unittest,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_papertrade_routes import WatchlistRouteTests

class ResearchRoutes(WatchlistRouteTests):
 def test_registry_disabled_and_no_accuracy(self):
  import json
  r=self.get('/api/research/strategies');self.assertEqual(r['status'],200)
  data=json.loads(r['body']);self.assertEqual(len(data['strategies']),10)
  self.assertTrue(all(s['enabled'] is False and s['approved'] is False for s in data['strategies']))
 def test_documents_fixed_allowlist(self):
  r=self.get('/api/research/document?name=formulas');self.assertEqual(r['status'],200);self.assertIn('Quantocracy',r['body'])
  self.assertEqual(self.get('/api/research/document?name=../../.env')['status'],400)
 def test_registry_error_has_no_demo_data(self):
  with patch('strategy.research_lab.catalogue',side_effect=ValueError('broken fixture')):
   r=self.get('/api/research/strategies');self.assertEqual(r['status'],503);self.assertNotIn('strategies',r['body'])

if __name__=='__main__':unittest.main()
