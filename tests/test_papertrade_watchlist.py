"""Pytest-collectable unittest cases; all fixtures explicitly synthetic except archive replay."""
import unittest,copy,json,tempfile,sqlite3,math
from contextlib import closing
from pathlib import Path
from datetime import datetime,timedelta
from strategy.papertrade_math import *
from strategy.papertrade_engine import PaperEngine,at,replay_signal,build_report,IST
from marketscope.paper_watchlist import load_config,PaperWatchlist,evaluate_closed_prefix
ROOT=Path(__file__).resolve().parents[1]
DAY='2026-10-30'

def config(**updates):
 c=load_config();c['slippage_bps']=0;c['fees']={k:0 for k in c['fees']};c.update(updates);return c

def decision(side='LONG',**extra):
 return {'signal_type':'BUY' if side=='LONG' else 'SELL','action':'TEST LONG' if side=='LONG' else 'TEST SHORT','grade':'A','conviction_pct':90,'risk_reward_ratio':2,'entry_low':100.,'entry_high':100.,**extra}

def bar(clock='09:20',o=100,h=100.3,l=99.7,c=100,day=DAY):
 return {'t':at(day,clock),'o':o,'h':h,'l':l,'c':c,'v':100,'source':'TEST FIXTURE','closed':True}

def emit(e,side='LONG',clock='09:20',**extra):
 return e.signal('TEST',at(DAY,clock),decision(side,**extra),1,5,'TEST FIXTURE',100)

def consume(e,c):e.candle('TEST',c,5,c['t']+300)

class MathTests(unittest.TestCase):
 def test_atr_long_short(self):
  self.assertEqual(atr_levels(100,2,'LONG'),{'entry':100,'stop':98,'target':104,'rr':2})
  self.assertEqual(atr_levels(100,2,'SHORT')['target'],96)
  with self.assertRaises(ValueError):atr_levels(100,2,'LONG',2,2,1.5)
 def test_long_short_pnl_no_cost(self):
  self.assertEqual(gross_pnl(100,110,10,'LONG'),100)
  self.assertEqual(gross_pnl(100,90,10,'SHORT'),100)
  self.assertEqual(trade_pnl(100,90,10,'SHORT',5,200,10)['r_multiple'],9.5)
 def test_hand_calculated_order_charges(self):
  fees=load_config()['fees'];buy=order_cost(100,10,True,fees);sell=order_cost(100,10,False,fees)
  self.assertAlmostEqual(buy['brokerage'],.3);self.assertAlmostEqual(buy['gst'],.059706)
  self.assertAlmostEqual(buy['total'],.421406);self.assertAlmostEqual(sell['total'],.641406)
  self.assertEqual(buy['stt'],0);self.assertEqual(sell['stamp'],0)
  self.assertEqual(order_cost(100,100000,False,fees)['brokerage'],20)
 def test_hand_calculated_roundtrip_charges(self):
  fees=load_config()['fees'];cost=order_cost(100,10,True,fees)['total']+order_cost(110,10,False,fees)['total']
  self.assertAlmostEqual(cost,1.1269526)
  self.assertAlmostEqual(trade_pnl(100,110,10,'LONG',cost,200,10)['net_pnl'],98.8730474)
 def test_slippage_is_adverse_and_not_double_counted(self):
  entry=slipped(100,True,5);exit=slipped(110,False,5)
  self.assertAlmostEqual(entry,100.05);self.assertAlmostEqual(exit,109.945)
  self.assertAlmostEqual(trade_pnl(entry,exit,10,'LONG',0,200,10)['net_pnl'],98.95)
  self.assertGreater(slipped(90,True,5),90);self.assertLess(slipped(100,False,5),100)
 def test_excursions_both_sides(self):
  c=[bar(h=110,l=90)];self.assertEqual(excursions(100,'LONG',c)['mfe_pct'],10)
  self.assertEqual(excursions(100,'SHORT',c)['mae_pct'],-10)
 def test_position_size_risk_margin_properties(self):
  fees=load_config()['fees']
  for side in ('LONG','SHORT'):
   for capital in (1000,10000,300000):
    for atr in (.1,1,10):
     levels=atr_levels(100,atr,side)
     s=position_size(capital,.01,100,levels['stop'],side,5,capital/3,capital,fees,5)
     self.assertIsInstance(s['qty'],int);self.assertGreaterEqual(s['qty'],0)
     self.assertLessEqual(s['planned_risk'],capital*.01+1e-8)
     self.assertLessEqual(s['margin'],capital/3+1e-8)
 def test_finite_inputs_only(self):
  for bad in (float('nan'),float('inf'),True,-1):
   with self.assertRaises(ValueError):atr_levels(100,bad,'LONG')
 def test_portfolio_metrics_hand_values(self):
  rows=[{'id':'1','status':'CLOSED','net_pnl':100,'r_multiple':1,'exit_time':1},
        {'id':'2','status':'CLOSED','net_pnl':-50,'r_multiple':-.5,'exit_time':2}]
  m=portfolio_metrics(rows,1000,[.1,-.05]);self.assertEqual(m['win_rate'],50)
  self.assertEqual(m['profit_factor'],2);self.assertEqual(m['expectancy_r'],.25)
  self.assertEqual(m['return_pct'],5);self.assertAlmostEqual(m['max_drawdown_pct'],50/1100*100)
  self.assertIsNotNone(m['sharpe_like']);self.assertIsNone(portfolio_metrics(rows,1000,[.05])['sharpe_like'])

class LifecycleTests(unittest.TestCase):
 def test_pending_then_fill_not_signal_bar(self):
  e=PaperEngine(config());t=emit(e)
  consume(e,bar('09:15',h=120,l=80));self.assertEqual(t['status'],'PENDING')
  consume(e,bar());self.assertEqual(t['status'],'OPEN');self.assertEqual(t['entry_time'],at(DAY,'09:25'))
 def test_expiry_unfilled(self):
  e=PaperEngine(config());t=emit(e)
  for label in ('09:20','09:25','09:30'):consume(e,bar(label,o=101,h=102,l=100.5,c=101))
  self.assertEqual(t['status'],'EXPIRED');self.assertIsNone(t['net_pnl'])
 def test_target_on_subsequent_candle(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar());consume(e,bar('09:25',o=101,h=103,l=100,c=102))
  self.assertEqual(t['close_reason'],'TARGET_HIT');self.assertGreater(t['net_pnl'],0)
 def test_sl_both_touch_stop_wins(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar());consume(e,bar('09:25',h=103,l=98,c=100))
  self.assertEqual(t['close_reason'],'STOPLOSS_HIT');self.assertLess(t['net_pnl'],0)
 def test_entry_candle_target_not_assumed(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar(h=104,l=99.5,c=103))
  self.assertEqual(t['status'],'OPEN')
 def test_entry_candle_stop_conservative(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar(h=103,l=98,c=101))
  self.assertEqual(t['close_reason'],'STOPLOSS_HIT')
 def test_short_from_zero_sell_buy_profit(self):
  e=PaperEngine(config());t=emit(e,'SHORT');consume(e,bar());consume(e,bar('09:25',o=99,h=100,l=97,c=98))
  self.assertEqual(t['side'],'SHORT');self.assertGreater(t['net_pnl'],0)
  self.assertEqual(t['entry_price'],100);self.assertEqual(t['exit_price'],98)
  self.assertLessEqual(e.account()['reserved'],300000)
 def test_short_margin_never_spends_proceeds(self):
  e=PaperEngine(config());t=emit(e,'SHORT');consume(e,bar())
  self.assertLess(e.account()['available'],300000)
 def test_gap_stop_fills_at_worse_open(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar());consume(e,bar('09:25',o=95,h=96,l=94,c=95))
  self.assertEqual(t['exit_price'],95)
 def test_entry_gap_past_stop_unfilled(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar(o=95,h=97,l=94,c=96))
  self.assertEqual(t['status'],'EXPIRED');self.assertEqual(t['close_reason'],'INVALID_ENTRY_GAP')
 def test_dedupe_one_active_and_repeat_event(self):
  e=PaperEngine(config());emit(e);emit(e);emit(e,clock='09:21')
  self.assertEqual(len(e.trades),1)
 def test_trailing_only_uses_prior_bars(self):
  e=PaperEngine(config(trailing_enabled=True,k_tp=10));t=emit(e);consume(e,bar())
  consume(e,bar('09:25',o=100.5,h=101,l=100,c=100.8))
  self.assertEqual(t['status'],'OPEN');consume(e,bar('09:30',o=100.5,h=100.7,l=99.8,c=100))
  self.assertEqual(t['close_reason'],'TRAILING_STOP')
 def test_reversal_flag_off_and_on_no_flip(self):
  for enabled in (False,True):
   e=PaperEngine(config(reversal_enabled=enabled));t=emit(e);consume(e,bar());emit(e,'SHORT','09:25')
   self.assertEqual(len(e.trades),1)
   self.assertEqual(t['status'],'CLOSED' if enabled else 'OPEN')
   if enabled:self.assertEqual(t['close_reason'],'SIGNAL_REVERSAL')
 def test_reversal_flip_creates_only_pending(self):
  e=PaperEngine(config(reversal_enabled=True,flip_enabled=True));emit(e);consume(e,bar());emit(e,'SHORT','09:25')
  self.assertEqual([t['status'] for t in e.trades],['CLOSED','PENDING'])
 def test_time_stop(self):
  e=PaperEngine(config(time_stop_minutes=5));t=emit(e);consume(e,bar());consume(e,bar('09:25'))
  self.assertEqual(t['close_reason'],'TIME_STOP')
 def test_square_off_boundary_candle_close(self):
  e=PaperEngine(config(time_stop_minutes=1000));t=emit(e);consume(e,bar());consume(e,bar('15:10',o=100,h=100.4,l=99.8,c=100.2))
  self.assertEqual(t['close_reason'],'AUTO_SQUARE_OFF');self.assertEqual(t['exit_time'],at(DAY,'15:15'));self.assertEqual(t['exit_price'],100.2)
 def test_missing_squareoff_no_fabricated_pnl(self):
  e=PaperEngine(config());t=emit(e);consume(e,bar());e.finish(DAY,at(DAY,'15:15'))
  self.assertEqual(t['status'],'CLOSED');self.assertIsNone(t['net_pnl']);self.assertIsNone(e.account()['available'])
 def test_future_unclosed_candle_is_not_used(self):
  e=PaperEngine(config());t=emit(e);c=bar();e.candle('TEST',c,5,c['t']+299)
  self.assertEqual(t['status'],'PENDING')
 def test_fees_identity_and_determinism(self):
  cfg=load_config();signal={'symbol':'TEST','stamp':at(DAY,'09:20'),'decision':decision(),'atr':1,'timeframe':5,'source':'TEST FIXTURE','price':100}
  candles=[bar(),bar('09:25',o=101,h=103,l=100,c=102)]
  a=replay_signal(signal,candles,cfg);b=replay_signal(signal,candles,cfg)
  self.assertEqual(a,b);self.assertAlmostEqual(a['net_pnl'],a['gross_pnl']-a['fees']);self.assertGreater(a['slippage_cost'],0)

class StorageReportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();p=Path(self.tmp.name);self.db=p/'app.db';self.candles=p/'candles.db';self.now=datetime.fromisoformat(DAY+'T16:00:00+05:30')
  with closing(sqlite3.connect(self.candles)) as c:c.execute('CREATE TABLE candles(symbol TEXT,timeframe INTEGER,ts INTEGER,o REAL,h REAL,l REAL,c REAL,v REAL,source TEXT)')
  self.w=PaperWatchlist(self.db,self.candles,now_fn=lambda:self.now);self.w.engine=PaperEngine(config())
 def tearDown(self):
  try:self.tmp.cleanup()
  except Exception:pass
 def archive(self,c):
  with closing(sqlite3.connect(self.candles)) as con:
   with con:con.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?)',('TEST',5,c['t'],c['o'],c['h'],c['l'],c['c'],c['v'],c['source']))
 def test_reports_reconcile_and_persist(self):
  t=emit(self.w.engine);a=bar();b=bar('09:25',o=101,h=103,l=100,c=102)
  for c in (a,b):self.archive(c);consume(self.w.engine,c)
  report=self.w.report(DAY,generate=True);self.assertEqual(report['metrics']['net_pnl'],sum(t['net_pnl'] or 0 for t in report['trades']))
  self.assertEqual(self.w.report(DAY),report)
  restored=PaperWatchlist(self.db,self.candles,now_fn=lambda:self.now)
  self.assertEqual(restored.report(DAY),report);self.assertIn('SIMULATED TRADE',restored.export(DAY,'csv'))
 def test_early_report_rejected(self):
  self.now=self.now.replace(hour=15,minute=34)
  with self.assertRaises(ValueError):self.w.report(DAY,generate=True)
 def test_no_demo_when_empty(self):
  self.assertEqual(self.w.report()['status'],'EMPTY');self.assertEqual(self.w.status()['trades'],[])
 def test_no_lookahead_archive_prefix(self):
  for c in (bar(),bar('09:25',o=1000,h=1100,l=900,c=1050)):self.archive(c)
  rows=self.w.archive('TEST',5,before=at(DAY,'09:25'))
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['c'],100)
 def test_closed_morning_returns_last_saved_report(self):
  self.w.report(DAY,generate=True);self.now=self.now.replace(hour=8)
  self.assertFalse(self.w.status()['market_open']);self.assertIsNotNone(self.w.status()['last_report'])
 def test_empty_boundary_never_uses_next_candle(self):
  t=emit(self.w.engine);self.archive(bar());self.archive(bar('15:15',o=100,h=110,l=99,c=109))
  self.w.advance(at(DAY,'15:35'));self.assertIsNone(t['net_pnl'])
 def test_gap_coverage_suppresses_mfe(self):
  e=self.w.engine;t=emit(e);a=bar();b=bar('09:30',o=101,h=103,l=100,c=102)
  for c in (a,b):consume(e,c)
  report=build_report(e,DAY,{('TEST',5):[a,b]},at(DAY,'15:35'))
  self.assertIsNone(report['trades'][0]['hindsight']['mfe_price'])
 def test_migration_does_not_change_signal_journal(self):
  with self.w.connection() as con:
   tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  self.assertEqual(tables,{'paper_watch_trades','paper_watch_events','paper_watch_reports'})


class SnapshotAdapterTests(unittest.TestCase):
 setUp=StorageReportTests.setUp
 tearDown=StorageReportTests.tearDown
 archive=StorageReportTests.archive
 def test_live_signal_uses_actual_observation_time_not_backdated_close(self):
  from unittest.mock import patch
  self.now=self.now.replace(hour=9,minute=46,second=7)
  self.archive(bar('09:40'))
  snapshot={'data_mode':'LIVE','session_state':'LIVE','stocks':{'TEST':{'status':'ok','ai_decision':decision()}}}
  with patch('marketscope.paper_watchlist.evaluate_closed_prefix',return_value=(decision(),1)):
   self.w.observe_snapshot(snapshot)
  t=self.w.engine.trades[0]
  self.assertEqual(t['decision_time'],self.now.timestamp())
  self.assertEqual(t['first_eligible_bar'],at(DAY,'09:50'))
  self.assertEqual(t['signal']['decision']['observed_candle_end'],at(DAY,'09:45'))
 def test_provisional_unmatched_closed_signal_ignored(self):
  from unittest.mock import patch
  self.now=self.now.replace(hour=9,minute=46);self.archive(bar('09:40'))
  with patch('marketscope.paper_watchlist.evaluate_closed_prefix',return_value=(decision('SHORT'),1)):
   self.w.observe_snapshot({'data_mode':'LIVE','session_state':'LIVE','stocks':{'TEST':{'status':'ok','ai_decision':decision()}}})
  self.assertFalse(self.w.engine.trades)
 def test_stale_and_historical_snapshots_never_create_positions(self):
  from unittest.mock import patch
  self.archive(bar('09:40'));self.now=self.now.replace(hour=12)
  with patch('marketscope.paper_watchlist.evaluate_closed_prefix',return_value=(decision(),1)):
   for mode in ('LIVE','PREVIOUS'):
    self.w.observe_snapshot({'data_mode':mode,'session_state':mode,'stocks':{'TEST':{'status':'ok','ai_decision':decision()}}})
  self.assertFalse(self.w.engine.trades)

class ActualArchiveReplayTests(unittest.TestCase):
 @unittest.skipUnless((ROOT/'work/runtime/candles.db').exists(),'Local real candle archive is not distributed with source')
 def test_real_archive_replay_totals_and_determinism(self):
  from strategy.papertrade_replay import replay_archive
  with tempfile.TemporaryDirectory() as scratch:
   reader=PaperWatchlist(Path(scratch)/'scratch.db',ROOT/'work/runtime/candles.db')
   symbols=['AEGISVOPAK','APLAPOLLO','APOLLO','ATHERENERG','AUBANK','AVANTEL','BANKINDIA','BPCL']
   a=replay_archive(reader,'2026-10-26',symbols);b=replay_archive(reader,'2026-10-26',symbols)
   self.assertEqual(a,b)
   total=sum(t['net_pnl'] for t in a['trades'] if t['status']=='CLOSED' and t['net_pnl'] is not None)
   self.assertAlmostEqual(a['metrics']['net_pnl'],total)
   self.assertTrue(all(a['replay_audit']['complete'].values()))

if __name__=='__main__':unittest.main()
