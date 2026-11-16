import csv,gzip,hashlib,json,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from collect_history import *

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.con=open_db(self.root/'research.sqlite')
        self.start=int(datetime.fromisoformat('2023-05-08T09:15:00+05:30').timestamp())
        self.con.execute('INSERT INTO requests VALUES(?,?,?,?)',('TEST','2023-05-08',self.start,self.start+6300));self.con.commit()
    def tearDown(self): self.con.close();self.temp.cleanup()
    def csv(self,name='bars.csv',count=105,volume=True):
        path=self.root/name
        opener=gzip.open if name.endswith('.gz') else open
        with opener(path,'wt',newline='') as stream:
            out=csv.DictWriter(stream,fieldnames=['symbol','timestamp','open','high','low','close','volume']);out.writeheader()
            for i in range(count):
                out.writerow(dict(symbol='TEST',timestamp=datetime.fromtimestamp(self.start+i*60,IST).replace(tzinfo=None).isoformat(),open=100,high=102,low=99,close=101,volume=10 if volume else ''))
        return path
    def test_naive_requires_explicit_timezone(self):
        with self.assertRaises(ValueError):parse_timestamp('2023-05-08T09:15:00')
        self.assertEqual(parse_timestamp('2023-05-08T09:15:00','IST'),self.start)
        self.assertEqual(parse_timestamp('2023-05-08T03:45:00Z'),self.start)
    def test_end_stamp_shift_and_aliases(self):
        bar=normalize_row({'Symbol':'TEST','Date':'20230508','Time':'09:20','Open':'100','High':'102','Low':'99','Close':'101','Volume':'10'},5,'IST','end')
        self.assertEqual(bar[1],self.start)
    def test_off_grid_five_minute_rejected(self):
        with self.assertRaises(ValueError):validate_bar('TEST',self.start+60,5,100,102,99,101,1)
    def test_invalid_ohlc_nonfinite_negative_volume(self):
        for values in [(100,90,99,100,1),(100,101,99,float('nan'),1),(100,102,99,101,-1)]:
            with self.assertRaises(ValueError):validate_bar('TEST',self.start,1,*values)
    def test_missing_volume_preserved_without_inventing_index_volume(self):
        path=self.csv(volume=False);result=import_file(self.con,path,'synthetic_fixture',1,'IST','start')
        self.assertEqual(result['derived_5m'],21)
        self.assertTrue(all(r[0] is None for r in self.con.execute('SELECT v FROM bars')))
    def test_complete_csv_resamples_exact_21_and_idempotent(self):
        path=self.csv();result=import_file(self.con,path,'synthetic_fixture',1,'IST','start')
        self.assertEqual(result['accepted'],105);self.assertEqual(result['derived_5m'],21)
        self.assertEqual(coverage(self.con)[0]['status'],'COMPLETE_PRICE_WINDOW')
        self.assertEqual(import_file(self.con,path,'synthetic_fixture',1,'IST','start')['status'],'ALREADY_IMPORTED')
    def test_gap_never_filled(self):
        path=self.csv(count=104);import_file(self.con,path,'synthetic_fixture',1,'IST','start')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM bars WHERE minutes=5').fetchone()[0],20)
        self.assertEqual(coverage(self.con)[0]['status'],'MISSING_OR_CONFLICTED')
    def test_gzip_csv_supported(self):
        result=import_file(self.con,self.csv('bars.csv.gz'),'synthetic_fixture',1,'IST','start')
        self.assertEqual(result['derived_5m'],21)
    def test_overlapping_requests_deduplicate(self):
        path=self.root/'events.csv'
        with path.open('w',newline='') as stream:
            out=csv.DictWriter(stream,fieldnames=['symbol','announced_at','next_trading_date','continuous_target_window']);out.writeheader()
            for _ in range(2):out.writerow(dict(symbol='TEST',announced_at='2023-05-06T18:00:00+05:30',next_trading_date='2023-05-08',continuous_target_window='True'))
        self.assertEqual(init_requests(self.con,path),0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM requests').fetchone()[0],1)
    def test_positive_only_scopes_financial_result_not_chart_direction(self):
        path=self.root/'scoped.csv'
        with path.open('w',newline='') as stream:
            out=csv.DictWriter(stream,fieldnames=['symbol','announced_at','next_trading_date','continuous_target_window','financial_class','return_0915_1100_pct']);out.writeheader()
            for classification,day,ret in [('POSITIVE','2023-05-09',-3),('NEGATIVE','2023-05-10',3)]:
                out.writerow(dict(symbol='TEST',announced_at='2023-05-08T17:00:00+05:30',next_trading_date=day,continuous_target_window='True',financial_class=classification,return_0915_1100_pct=ret))
        self.assertEqual(init_requests(self.con,path,positive_only=True),1)
        self.assertIsNotNone(self.con.execute("SELECT 1 FROM requests WHERE day='2023-05-09'").fetchone())
        self.assertIsNone(self.con.execute("SELECT 1 FROM requests WHERE day='2023-05-10'").fetchone())
    def test_resume_progresses_after_row_budget(self):
        path=self.csv();a=import_file(self.con,path,'synthetic_fixture',1,'IST','start',max_rows=50)
        self.assertEqual(a['rows_read'],50);self.assertEqual(a['status'],'PAUSED_ROW_BUDGET')
        b=import_file(self.con,path,'synthetic_fixture',1,'IST','start',max_rows=100)
        self.assertEqual(b['rows_read'],105);self.assertEqual(b['status'],'COMPLETE')
        self.assertEqual(coverage(self.con)[0]['status'],'COMPLETE_PRICE_WINDOW')
    def test_conflicting_duplicate_quarantines_window(self):
        path=self.csv();import_file(self.con,path,'synthetic_fixture',1,'IST','start')
        sid=self.con.execute('SELECT id FROM sources').fetchone()[0]
        store_bar(self.con,validate_bar('TEST',self.start,1,100,103,99,102,10),sid)
        self.assertEqual(coverage(self.con)[0]['status'],'MISSING_OR_CONFLICTED')
    def test_other_source_disagreement_is_retained_and_flagged(self):
        path=self.csv();import_file(self.con,path,'source_a',1,'IST','start')
        store_bar(self.con,validate_bar('TEST',self.start,5,100,103,99,102,50),'source_b')
        self.assertEqual(coverage(self.con)[0]['status'],'MISSING_OR_CONFLICTED')
    def test_minute_limit_enforced_not_only_second_limit(self):
        for i in range(50):self.con.execute('INSERT INTO api_attempts VALUES(?,?)',(100+i,'data'))
        limiter=RateLimiter(self.con)
        self.assertGreater(limiter.delay(150),9)
    def test_daily_budget_stops_without_sleeping_hours(self):
        self.con.execute('INSERT INTO api_attempts VALUES(?,?)',(99,'data'))
        limiter=RateLimiter(self.con,per_day=1)
        with self.assertRaises(RuntimeError):limiter.acquire('data',clock=lambda:100,sleep=lambda _:None)
    def test_private_fyers_credentials_never_assumed(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):fyers_collect(self.con,self.root/'not-a-master.csv')
    def test_parquet_missing_dependency_fails_explicitly(self):
        path=self.root/'data.parquet';path.write_bytes(b'notparquet')
        with patch.dict('sys.modules',{'pyarrow':None,'pyarrow.dataset':None}):
            with self.assertRaises(RuntimeError):list(iter_rows(path))
    def test_complete_export_preserves_provenance_and_unknown_verification(self):
        import_file(self.con,self.csv(),'synthetic_fixture',1,'IST','start')
        output=self.root/'export.csv';result=export_complete(self.con,output)
        self.assertEqual(result['exported_5m_bars'],21)
        with output.open() as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['provider_arrival_verified'],'False')
        self.assertTrue(rows[0]['source_raw_sha256'])
    def test_downloader_rejects_credential_url_and_existing_unknown_file(self):
        with self.assertRaises(ValueError):public_download('https://user:pass@example.com/data.csv',self.root)
        (self.root/'data.csv').write_text('unverified')
        with self.assertRaises(ValueError):public_download('https://example.com/data.csv',self.root)
    def test_public_downloader_byte_budget_and_checksum(self):
        class Response:
            status=200
            headers={'Content-Length':'3','ETag':'"fixture"'}
            def __init__(self):self.content=b'abc'
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,_):data,self.content=self.content,b'';return data
        with patch('urllib.request.urlopen',return_value=Response()):
            r=public_download('https://example.com/archive.csv',self.root,hashlib.sha256(b'abc').hexdigest(),4)
        self.assertTrue(r['complete']);self.assertEqual((self.root/'archive.csv').read_bytes(),b'abc')
        with patch('urllib.request.urlopen',return_value=Response()):
            with self.assertRaises(ValueError):public_download('https://example.com/too-big.csv',self.root,max_bytes=2)
    def test_public_download_resumes_only_matching_etag_range(self):
        part=self.root/'archive.csv.part';part.write_bytes(b'a')
        (self.root/'archive.csv.download.json').write_text(json.dumps({'url':'https://example.com/archive.csv','etag':'"fixture"'}))
        class Response:
            status=206
            headers={'Content-Length':'2','Content-Range':'bytes 1-2/3','ETag':'"fixture"'}
            def __init__(self):self.data=b'bc'
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,_):data,self.data=self.data,b'';return data
        with patch('urllib.request.urlopen',return_value=Response()) as mock:
            result=public_download('https://example.com/archive.csv',self.root,hashlib.sha256(b'abc').hexdigest())
        self.assertEqual(mock.call_args[0][0].get_header('Range'),'bytes=1-')
        self.assertTrue(result['complete']);self.assertEqual((self.root/'archive.csv').read_bytes(),b'abc')

if __name__=='__main__':unittest.main()
