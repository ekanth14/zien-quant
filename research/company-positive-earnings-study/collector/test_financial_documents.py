import csv,json,sqlite3,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from collect_financial_documents import collect,validated_url,load_parser

PROJECT=str(Path(__file__).resolve().parents[3])
XML=b'''<xbrli:xbrl xmlns:xbrli="urn:xbrli" xmlns:in="urn:indas" xmlns:iso="urn:currency">
<xbrli:context id="q"><xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-03-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="money"><xbrli:measure>iso:INR</xbrli:measure></xbrli:unit>
<in:RevenueFromOperations contextRef="q" unitRef="money">10000000</in:RevenueFromOperations><in:ProfitLossForPeriod contextRef="q" unitRef="money">1000000</in:ProfitLossForPeriod></xbrli:xbrl>'''
class Response:
    headers={'Content-Type':'application/xml','Last-Modified':'a provider claim'}
    def __init__(self,content):self.content=content
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def read(self,size):return self.content[:size]
class Opener:
    def __init__(self,content):self.content=content;self.calls=0
    def open(self,*_,**__):self.calls+=1;return Response(self.content)
class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.request=self.root/'requests.csv'
        with self.request.open('w',newline='') as stream:
            out=csv.DictWriter(stream,fieldnames=['url','symbol','period_end','basis','announcement_at']);out.writeheader()
            for i in range(3):out.writerow(dict(url=f'https://nsearchives.nseindia.com/corporate/{i}.xml',symbol='TEST',period_end='2023-03-31',basis='consolidated',announcement_at='2023-05-06T17:00:00+05:30'))
    def tearDown(self):self.temp.cleanup()
    def kwargs(self):return dict(requests_path=self.request,db_path=self.root/'docs.sqlite',output_dir=self.root/'raw')
    def test_allowlist_exact_host_https(self):
        for url in ['http://nsearchives.nseindia.com/a','https://nsearchives.nseindia.com.evil.test/a','https://user:secret@nsearchives.nseindia.com/a']:
            with self.assertRaises(ValueError):validated_url(url)
    def test_network_failure_breaks_batch_after_one_request(self):
        opener=Opener(XML)
        with patch.object(opener,'open',side_effect=urllib.error.URLError('fixture DNS failure')):
            result=collect(**self.kwargs(),limit=3,opener=opener)
        self.assertEqual(result['attempted_unique_documents'],1);self.assertEqual(result['circuit_break_reason'],'ERROR_NETWORK')
        self.assertEqual(result['new_documents_downloaded'],0)
    def test_pdf_saved_and_explicitly_unparsed(self):
        result=collect(**self.kwargs(),limit=1,opener=Opener(b'%PDF-1.7 fixture'))
        self.assertEqual(result['new_documents_downloaded'],1);self.assertEqual(result['documents_with_parsed_facts'],0)
        self.assertEqual(len(list((self.root/'raw').glob('*.pdf'))),1)
    def test_exact_project_parser_and_source_clocks_remain_claims(self):
        if not Path(PROJECT).exists():self.skipTest('Original project parser unavailable')
        result=collect(**self.kwargs(),project=PROJECT,limit=1,opener=Opener(XML))
        self.assertEqual(result['documents_with_parsed_facts'],1)
        payload=json.loads(next((self.root/'raw').glob('*.facts.json')).read_text())
        self.assertEqual(payload['facts'][0]['revenue'],100)
        self.assertFalse(payload['original_public_vintage_verified'])
        self.assertFalse(payload['publication_time_inferred_from_download'])
        self.assertTrue(payload['raw_sha256']);self.assertTrue(payload['acquired_at'])
    def test_cached_document_not_downloaded_twice(self):
        opener=Opener(b'%PDF-1.7 fixture')
        collect(**self.kwargs(),limit=1,opener=opener)
        result=collect(**self.kwargs(),limit=1,opener=opener)
        self.assertEqual(opener.calls,2)
        # The second invocation advances to the next URL, rather than fetching
        # the first already-downloaded PDF again.
        self.assertEqual(result['new_documents_downloaded'],1)
    def test_block_page_stops_after_first_document(self):
        result=collect(**self.kwargs(),limit=3,opener=Opener(b'<html>Access Denied</html>'))
        self.assertEqual(result['circuit_break_reason'],'ERROR_BLOCK_PAGE')
        self.assertEqual(result['attempted_unique_documents'],1)
    def test_conservative_minimum_delay_required(self):
        with self.assertRaises(ValueError):collect(**self.kwargs(),delay=.1)

if __name__=='__main__':unittest.main()
