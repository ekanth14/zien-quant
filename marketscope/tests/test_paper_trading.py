"""Deterministic virtual-ledger tests. Prices are explicit test fixtures, never live fallbacks."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paper_trading import PaperSession, SYMBOLS, VERSION, IST, calendar_session, next_session, FyersPaperData
from datetime import datetime, timedelta, date


class FixtureData:
    def __init__(self, owner):
        self.owner=owner
        self.calls=0
        self.price=100.0
        self.direction=1
        self.stale=False
        self.fail=False
        self.no_signal=False
    def quotes(self):
        self.calls+=1
        if self.fail:
            raise ValueError('test provider outage')
        stamp=self.owner.now.timestamp()-(100 if self.stale else 0)
        return {s:{'price':self.price,'timestamp':stamp,'source':'FYERS Quotes API'} for s in SYMBOLS}
    def analysis(self,symbol,now):
        return {'reaction_session':{'date':now.date().isoformat()}, 'sample_size':10,
                'setup':{'action':'WAIT' if self.no_signal else 'BUY_WATCH' if self.direction==1 else 'SELL_WATCH',
                         'reasons':['TEST ONLY: deterministic fixture'],
                         'levels':{'entry':100, 'stop':99 if self.direction==1 else 101,
                                   'target':101.5 if self.direction==1 else 98.5}}}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,10,30,8,0,tzinfo=IST)
        self.data=FixtureData(self)
        self.p=PaperSession(Path(self.tmp.name)/'state.json', self.data, lambda:self.now)
    def tearDown(self): self.tmp.cleanup()
    def review(self,**settings):
        return self.p.command('review',{'version':VERSION,'acknowledged':True,'settings':settings},'test-owner')
    def start(self):
        self.review();self.p.command('start',{},'test-owner')
        self.now=self.now.replace(hour=9,minute=45)
        self.p.tick()
    def test_default_review_gate_no_data_or_fills(self):
        s=self.p.command('start',{},'test-owner');self.p.tick()
        self.assertEqual(s['state'],'No strategy configured')
        self.assertEqual(s['portfolio']['capital'],300000)
        self.assertFalse(s['trades']);self.assertEqual(self.data.calls,0)
    def test_calendar_weekend_unknown_and_expiry(self):
        self.assertIsNone(calendar_session(date(2026,10,31)))
        self.assertIsNone(calendar_session(date(2026,11,8)))
        self.assertIsNone(calendar_session(date(2027,1,4)))
        self.assertEqual(next_session(self.now)['date'],'2026-10-30')
        self.assertEqual(next_session(self.now.replace(day=31))['date'],'2026-11-02')
    def test_outside_session_does_not_fetch(self):
        self.review();self.p.command('start',{},'test-owner');self.p.tick()
        self.assertEqual(self.data.calls,0)
        self.now=self.now.replace(day=31,hour=10)
        self.p.tick();self.assertEqual(self.data.calls,0)
        self.assertEqual(self.p.s['state'],'Ended')
    def test_late_start_targets_next_session(self):
        self.now=self.now.replace(hour=12)
        self.review();s=self.p.command('start',{},'test-owner')
        self.assertEqual(s['session']['date'],'2026-11-02')
    def test_long_entries_budget_and_whole_shares(self):
        self.start();s=self.p.snapshot()
        self.assertEqual(len(s['trades']),3)
        self.assertLessEqual(s['portfolio']['reserved'],300000)
        self.assertGreaterEqual(s['portfolio']['available'],0)
        for t in s['trades']:
            self.assertIsInstance(t['quantity'],int)
            self.assertGreater(t['entry_price'],100)
            self.assertLessEqual(t['allocation']+t['entry_fee'],100000.01)
            self.assertLess(t['net_pnl'],0) # adverse cost mark, no fabricated profit
    def test_long_quote_gap_exit_accounts_costs(self):
        self.start();self.now+=timedelta(seconds=5);self.data.price=98
        self.p.tick();s=self.p.snapshot()
        for t in s['trades']:
            self.assertEqual(t['status'],'CLOSED')
            self.assertLess(t['exit_price'],98)
            self.assertLess(t['net_pnl'],t['gross_pnl'])
            self.assertEqual(t['closed_by'],'simulator')
        self.assertEqual(len(s['trades']),3)
    def test_short_proceeds_never_add_spendable_capital(self):
        self.data.direction=-1;self.start();s=self.p.snapshot()
        self.assertLess(s['portfolio']['available'],300000)
        self.assertTrue(all(t['direction']==-1 for t in s['trades']))
        self.now+=timedelta(seconds=5);self.data.price=97;self.p.tick()
        self.assertTrue(all(t['net_pnl']>0 for t in self.p.s['trades']))
    def test_pause_preserves_stop_management(self):
        self.start();self.p.command('pause',{},'test-owner')
        self.data.price=98;self.now+=timedelta(seconds=5);self.p.tick()
        self.assertTrue(all(t['status']=='CLOSED' for t in self.p.s['trades']))
        self.assertEqual(self.p.s['state'],'Paused')
    def test_stale_quotes_no_entries(self):
        self.data.stale=True;self.start()
        self.assertFalse(self.p.s['trades'])
        self.assertTrue(all(r['signal']=='NO TRADE' for r in self.p.s['rows'].values()))
    def test_missing_quote_time_and_wrong_source_rejected(self):
        self.review();self.p.command('start',{},'test-owner');self.now=self.now.replace(hour=9,minute=45)
        for q in ({'price':100},{'price':100,'timestamp':self.now.timestamp(),'source':'Yahoo'},
                  {'price':100,'timestamp':self.now.timestamp()+1,'source':'FYERS Quotes API'}):
            self.assertFalse(self.p.fresh(q,self.now))
    def test_cutoff_fresh_closes_all(self):
        self.start();self.now=self.now.replace(hour=11,minute=0);self.p.tick()
        self.assertEqual(self.p.s['state'],'Ended')
        self.assertTrue(all(t['status']=='CLOSED' for t in self.p.s['trades']))
    def test_cutoff_outage_unpriced_no_fabricated_profit(self):
        self.start();self.data.fail=True;self.now=self.now.replace(hour=11,minute=0);self.p.tick()
        s=self.p.snapshot();self.assertEqual(s['state'],'Ended')
        self.assertTrue(all(t['status']=='CLOSED_UNPRICED' and t['exit_price'] is None for t in s['trades']))
        self.assertIsNone(s['portfolio']['net_pnl']);self.assertIsNone(s['portfolio']['available'])
    def test_no_signal_no_profit(self):
        self.data.no_signal=True;self.start();s=self.p.snapshot()
        self.assertEqual(s['portfolio']['net_pnl'],0)
        self.assertEqual(s['portfolio']['reserved'],0)
        self.assertFalse(s['trades'])
    def test_no_duplicate_trades_or_signal_logs_every_poll(self):
        self.start();self.now+=timedelta(seconds=5);self.p.tick()
        count=len(self.p.s['log']);self.now+=timedelta(seconds=5);self.p.tick()
        self.assertEqual(len(self.p.s['trades']),3)
        self.assertEqual(len(self.p.s['log']),count)
    def test_restart_preserves_ledger_and_no_backfill_entry(self):
        self.start();self.now=self.now.replace(hour=11,minute=1)
        restored=PaperSession(self.p.path,self.data,lambda:self.now);restored.tick()
        self.assertEqual(len(restored.s['trades']),3)
        self.assertEqual(restored.s['state'],'Ended')
    def test_corrupt_storage_blocks_reset(self):
        self.p.path.write_text('{broken')
        p=PaperSession(self.p.path,self.data,lambda:self.now)
        with self.assertRaises(ValueError):p.command('start',{},'test')
        self.assertEqual(p.s['state'],'Storage error')
    def test_settings_validation_nan_overspend_cutoff(self):
        for settings in ({'capital':float('nan')},{'allocation_pct':40},{'cutoff':'18:00'},{'max_age_seconds':100}):
            with self.assertRaises(ValueError): self.review(**settings)
    def test_end_closes_simulated_positions(self):
        self.start();s=self.p.command('end',{},'test-owner')
        self.assertEqual(s['state'],'Ended')
        self.assertTrue(all(t['status']=='CLOSED' for t in s['trades']))
    def test_csv_contains_portfolio_stocks_full_event_log(self):
        self.start();self.p.command('end',{},'test-owner')
        rows=list(csv.DictReader(io.StringIO(self.p.export_csv())))
        self.assertEqual(rows[0]['record_type'],'PAPER portfolio')
        self.assertEqual(sum(r['record_type']=='PAPER stock' for r in rows),3)
        self.assertEqual(sum(r['record_type']=='PAPER entry' for r in rows),3)
        self.assertEqual(sum(r['record_type']=='PAPER exit' for r in rows),3)
    def test_read_only_quote_adapter_allowlisted_get(self):
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'s':'ok','d':[{'n':'NSE:TRENT-EQ','s':'ok','v':{'lp':100,'tt':123}}]}).encode()
        with patch('paper_trading.get_fyers_history_provider') as provider, patch('urllib.request.urlopen',return_value=Reply()) as url:
            provider.return_value._credentials.return_value=('test-app','test-token')
            result=FyersPaperData().quotes()
            req=url.call_args.args[0]
            self.assertEqual(req.get_method(),'GET')
            self.assertTrue(req.full_url.startswith('https://api-t1.fyers.in/data/quotes?'))
            self.assertEqual(result['TRENT']['price'],100)
            self.assertNotIn('test-token',json.dumps(result))


class CurrentDataProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,30,9,45,tzinfo=IST)
        opening=self.now.replace(hour=9,minute=15)
        self.live=[{'t':int((opening+timedelta(minutes=5*i)).timestamp()),'o':100.,'h':101.,'l':99.,'c':100.,'v':10.} for i in range(6)]
        self.service=type('Service',(),{})()
        self.service.list_events=lambda **kw:{'events':[{'id':'test','symbol':'TRENT'}]}
        self.service.get_candles=lambda s:[{**r,'timeframe':5,'source':'Yahoo'} for r in self.live]
        self.service.history=lambda s:[]
        self.service._meta=lambda *a:{}
    def analyze(self,live):
        with patch('results_service.get_results_service',return_value=self.service), patch('earnings_research.analyze_event',return_value={'setup':{'action':'WAIT'}}) as engine, patch('paper_trading.get_fyers_history_provider') as provider:
            provider.return_value._request_chunk.return_value=live
            result=FyersPaperData().analysis('TRENT',self.now)
            return result,engine.call_args
    def test_empty_fyers_cannot_fall_back_to_public_current_candles(self):
        with self.assertRaises(ValueError):self.analyze([])
    def test_partial_fyers_cannot_fill_gaps_from_public_current_candles(self):
        with self.assertRaises(ValueError):self.analyze(self.live[:-1])
    def test_conflicting_fyers_candles_rejected(self):
        with self.assertRaises(ValueError):self.analyze(self.live+[{**self.live[-1],'c':100.5}])
    def test_complete_current_prefix_uses_only_fyers(self):
        result,args=self.analyze(self.live)
        self.assertEqual(result['signal_closed_at'],self.now.timestamp())
        self.assertTrue(all(c['source']=='FYERS Historical Data API' for c in args.args[2]['TRENT']))
        self.assertFalse(result['market_data_evidence']['public_current_session_fallback'])
    def test_invalid_ohlcv_rejected(self):
        bad=[*self.live[:-1],{**self.live[-1],'h':1}]
        with self.assertRaises(ValueError):self.analyze(bad)

class DeadlineAndControlTests(unittest.TestCase):
    setUp = PaperTests.setUp
    tearDown = PaperTests.tearDown
    review = PaperTests.review
    def one_position(self):
        self.review(cutoff='10:00');self.p.command('start',{},'test')
        self.now=self.now.replace(hour=9,minute=45)
        original=self.data.analysis
        def initial(symbol,now):
            value=original(symbol,now)
            if symbol!='ASIANPAINT':value['setup']['action']='WAIT'
            return value
        self.data.analysis=initial;self.p.tick()
        self.assertEqual(len(self.p.s['trades']),1)
        return original
    def test_slow_analysis_closes_at_deadline_before_tick_returns(self):
        original=self.one_position();self.now=self.now.replace(hour=9,minute=59,second=59)
        def slow(symbol,now):
            self.now+=timedelta(seconds=18)
            return original(symbol,now)
        self.data.analysis=slow;self.p.tick()
        self.assertEqual(self.p.s['state'],'Ended')
        self.assertTrue(all(t['status']!='OPEN' for t in self.p.s['trades']))
        self.assertEqual(len(self.p.s['trades']),1)
    def test_failed_quote_request_refreshes_clock_and_enforces_cutoff(self):
        self.one_position();self.now=self.now.replace(hour=9,minute=59,second=59)
        def fail():
            self.now+=timedelta(seconds=18)
            raise ValueError('fixture timeout')
        self.data.quotes=fail;self.p.tick()
        self.assertEqual(self.p.s['state'],'Ended')
        self.assertEqual(self.p.s['trades'][0]['status'],'CLOSED_UNPRICED')
    def test_watchdog_and_pause_can_access_state_during_blocked_history(self):
        import threading
        original=self.one_position();self.now=self.now.replace(hour=9,minute=59,second=59)
        entered,released=threading.Event(),threading.Event()
        def blocked(symbol,now):
            entered.set()
            if not released.wait(2):raise ValueError('test synchronization timeout')
            return original(symbol,now)
        self.data.analysis=blocked
        thread=threading.Thread(target=self.p.tick,daemon=True);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            # Lock is free despite the provider request being blocked.
            self.p.command('pause',{},'test')
            self.now+=timedelta(seconds=2)
            self.assertTrue(self.p.deadline_check())
            self.assertEqual(self.p.snapshot()['state'],'Ended')
        finally:
            released.set();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.p.s['trades']),1)
        self.assertTrue(all(t['status']!='OPEN' for t in self.p.s['trades']))

if __name__=='__main__':unittest.main()
