"""Synthetic fixtures test causal research behavior, not profitability or market data."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from marketscope.paper_watchlist import load_config
from strategy.papertrade_engine import at
from strategy.research_lab import atr14, ema, evaluate, replay, vwap

ROOT=Path(__file__).resolve().parents[1]
DAY='2026-10-30'

def bar(stamp, close=100, high=101, low=99, opening=100, **extra):
    return dict(t=stamp,o=opening,h=high,l=low,c=close,v=100,
                source='SYNTHETIC_TEST_FIXTURE',closed=True,available_at=stamp+300,**extra)

def rows(side='LONG'):
    warm=[bar(at('2026-10-29','09:15')+i*300) for i in range(15)]
    today=[bar(at(DAY,'09:15')+i*300) for i in range(3)]
    today.append(bar(at(DAY,'09:30'),close=103 if side=='LONG' else 97,
                     high=103.5 if side=='LONG' else 100.5,
                     low=99.5 if side=='LONG' else 96.5))
    return warm+today

def config(**changes):
    c=load_config();c.update(changes);return c

class FormulaTests(unittest.TestCase):
    def test_seeded_ema_known_values(self):
        self.assertIsNone(ema([1,2],3))
        self.assertEqual(ema([1,2,3],3),2)
        self.assertEqual(ema([1,2,3,4],3),3)
    def test_atr_is_documented_simple_average(self):
        r=[bar(10000+i*300) for i in range(15)]
        r[-1].update(h=104,l=99,c=103)
        self.assertAlmostEqual(atr14(r),(13*2+5)/14)
        self.assertIsNone(atr14(r[:14]))
    def test_vwap_uses_typical_price_and_volume(self):
        r=[dict(h=12,l=6,c=9,v=10),dict(h=15,l=9,c=12,v=30)]
        self.assertEqual(vwap(r),11.25)
        self.assertIsNone(vwap([dict(h=12,l=6,c=9,v=0)]))

class SignalSafetyTests(unittest.TestCase):
    def test_orb_exact_long_and_short(self):
        for side in ('LONG','SHORT'):
            result=evaluate('orb15_atr',rows(side),at(DAY,'09:35'))
            self.assertEqual(result['signal'],side)
            self.assertFalse(result['approved']);self.assertIsNone(result['probability'])
            self.assertEqual(result['values']['opening_high'],101)
            self.assertEqual(result['values']['opening_low'],99)
            self.assertEqual(result['plan']['entry'],103 if side=='LONG' else 97)
    def test_no_breakout_at_range_boundary(self):
        r=rows();r[-1].update(c=101,h=101)
        self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_previous_close_already_outside_no_repeat_breakout(self):
        r=rows();r.append(bar(at(DAY,'09:35'),close=104,high=105,low=102,opening=103))
        self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:40'))['signal'],'NO TRADE')
    def test_ema_vwap_exact_cross(self):
        result=evaluate('ema9_vwap',rows(),at(DAY,'09:35'))
        self.assertEqual(result['signal'],'LONG')
        self.assertEqual(result['values']['previous_ema9'],100)
        self.assertEqual(result['values']['previous_vwap'],100)
        self.assertAlmostEqual(result['values']['ema9'],100.6)
        self.assertAlmostEqual(result['values']['vwap'],100.5)
    def test_cash_is_explicit_no_exposure(self):
        self.assertEqual(evaluate('cash_baseline',[],at(DAY,'10:00'))['signal'],'NO TRADE')
    def test_future_or_unavailable_bar_cannot_signal(self):
        for mutate in (lambda r:r[-1].update(available_at=at(DAY,'09:36')),
                       lambda r:r[-1].update(t=at(DAY,'09:35'))):
            r=rows();mutate(r)
            self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_unclosed_or_invalid_or_nonfinite_rejected(self):
        for k,v in [('closed',False),('h',90),('c',float('nan')),('v',-1),('v',None),('v',True),('source',{})]:
            r=rows();r[-1][k]=v
            self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:35'))['signal'],'NO TRADE',(k,v))
    def test_nonobject_row_is_blocked(self):
        self.assertEqual(evaluate('orb15_atr',[None],at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_mixed_provider_is_blocked(self):
        r=rows();r[-1]['source']='OTHER_TEST_PROVIDER'
        self.assertIn('Mixed providers',evaluate('orb15_atr',r,at(DAY,'09:35'))['reason'])
    def test_session_gap_or_missing_open_is_blocked(self):
        for index in (15,16):
            r=rows();del r[index]
            self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_duplicate_or_unordered_is_blocked(self):
        r=rows();r.insert(-1,copy.deepcopy(r[-2]))
        self.assertIn('Duplicate',evaluate('orb15_atr',r,at(DAY,'09:35'))['reason'])
        r=rows();r[-1],r[-2]=r[-2],r[-1]
        self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_stale_candle_is_blocked(self):
        self.assertIn('Stale',evaluate('orb15_atr',rows(),at(DAY,'09:41'))['reason'])
    def test_no_session_or_warmup_or_volume_blocks(self):
        self.assertEqual(evaluate('orb15_atr',rows()[-4:],at(DAY,'09:35'))['signal'],'NO TRADE')
        r=rows()
        for b in r:b['v']=0
        self.assertEqual(evaluate('orb15_atr',r,at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_cutoff_no_new_entry(self):
        self.assertEqual(evaluate('orb15_atr',rows(),at(DAY,'15:15'))['signal'],'NO TRADE')
    def test_unsupported_interval_strategy_and_time_raise(self):
        for strategy,interval,stamp in [('missing',5,at(DAY,'09:35')),('orb15_atr',True,at(DAY,'09:35')),('orb15_atr',2,at(DAY,'09:35')),('orb15_atr',5,float('nan'))]:
            with self.assertRaises(ValueError):evaluate(strategy,rows(),stamp,interval)

class ReplayTests(unittest.TestCase):
    def test_replay_does_not_mutate_config_or_input(self):
        r={'TEST':rows()};c=config();r_before=copy.deepcopy(r);c_before=copy.deepcopy(c)
        a=replay('orb15_atr',DAY,r,c);b=replay('orb15_atr',DAY,r,c)
        self.assertEqual(a,b);self.assertEqual(r,r_before);self.assertEqual(c,c_before)
        self.assertEqual(a['config']['leverage'],1);self.assertFalse(a['approved'])
    def test_decision_bar_cannot_fill_itself(self):
        out=replay('orb15_atr',DAY,{'TEST':rows()},config())
        self.assertEqual(len(out['trades']),1)
        t=out['trades'][0];self.assertEqual(t['status'],'EXPIRED');self.assertIsNone(t['entry_price'])
        self.assertIsNone(out['metrics']['win_rate'])
    def test_next_bar_can_fill_and_missing_cutoff_is_unknown(self):
        r=rows();r.append(bar(at(DAY,'09:35'),opening=103,close=103,high=103.3,low=102.8))
        out=replay('orb15_atr',DAY,{'TEST':r},config())
        t=out['trades'][0];self.assertEqual(t['entry_time'],at(DAY,'09:40'))
        self.assertEqual(t['status'],'CLOSED');self.assertIsNone(t['exit_price'])
        self.assertIsNone(out['metrics']['net_pnl']);self.assertIsNone(out['metrics']['return_pct'])
    def test_future_prices_never_change_initial_signal(self):
        r=rows();initial=replay('orb15_atr',DAY,{'TEST':r},config())['trades'][0]
        r.append(bar(at(DAY,'09:35'),opening=103,close=110,high=110,low=100))
        later=replay('orb15_atr',DAY,{'TEST':r},config())['trades'][0]
        for key in ('decision_time','planned_entry','initial_stop','target','atr','signal'):
            self.assertEqual(initial[key],later[key],key)
    def test_late_observation_skips_already_started_bar(self):
        r=rows();r[-1]['available_at']=at(DAY,'09:36')
        r.extend([bar(at(DAY,'09:35'),opening=103,close=103,high=103.3,low=102.8),
                  bar(at(DAY,'09:40'),opening=103,close=103,high=103.3,low=102.8)])
        out=replay('orb15_atr',DAY,{'TEST':r},config());t=out['trades'][0]
        self.assertEqual(t['decision_time'],at(DAY,'09:36'))
        self.assertEqual(t['entry_time'],at(DAY,'09:45'))
    def test_very_late_observation_does_not_signal(self):
        r=rows();r[-1]['available_at']=at(DAY,'09:50')
        out=replay('orb15_atr',DAY,{'TEST':r},config())
        self.assertEqual(out['trades'],[])
    def test_calendar_unknown_holiday_weekend_special_rejected(self):
        for day in ('2026-10-31','2026-11-10','2026-11-08','2027-01-04'):
            with self.assertRaises(ValueError):replay('cash_baseline',day,{},config())
    def test_missing_availability_requires_retrospective_opt_in(self):
        r=rows()
        for b in r:b.pop('available_at')
        with self.assertRaises(ValueError):replay('orb15_atr',DAY,{'TEST':r},config())
        out=replay('orb15_atr',DAY,{'TEST':r},config(),retrospective=True)
        self.assertTrue(out['retrospective_availability_assumed'])
    def test_mixed_provider_and_invalid_volume_never_reach_fills(self):
        for field,value in [('source','SECOND_PROVIDER'),('v',-1),('v',None),('v',float('nan'))]:
            r=rows();r.append(bar(at(DAY,'09:35'),opening=103,close=103,high=103.3,low=102.8));r[-1][field]=value
            with self.assertRaises(ValueError):replay('orb15_atr',DAY,{'TEST':r},config())
    def test_invalid_shape_and_duplicates_rejected(self):
        candidates=[[],{'TEST':None},{'TEST':[None]},{'TEST':rows()+[rows()[-1]]},{'TEST':[{}]}]
        for data in candidates:
            with self.assertRaises(ValueError):replay('orb15_atr',DAY,data,config())
    def test_unsupported_replay_interval_rejected(self):
        with self.assertRaises(ValueError):replay('orb15_atr',DAY,{'TEST':rows()},config(timeframe_minutes=True))
    def test_empty_cash_never_claims_win_rate(self):
        out=replay('cash_baseline',DAY,{},config())
        self.assertEqual(out['trades'],[]);self.assertIsNone(out['metrics']['win_rate']);self.assertEqual(out['metrics']['net_pnl'],0)
    def test_cli_exclusive_output_preserves_input_and_prior_result(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'input.json';out=Path(folder)/'output.json'
            raw=json.dumps({'TEST':rows()});src.write_text(raw)
            cmd=[sys.executable,str(ROOT/'scripts/replay_research_strategy.py'),'--input',str(src),'--output',str(out),'--day',DAY,'--strategy','cash_baseline']
            first=subprocess.run(cmd,capture_output=True,text=True,cwd=ROOT)
            self.assertEqual(first.returncode,0,first.stderr);saved=out.read_bytes()
            second=subprocess.run(cmd,capture_output=True,text=True,cwd=ROOT)
            self.assertNotEqual(second.returncode,0);self.assertEqual(out.read_bytes(),saved);self.assertEqual(src.read_text(),raw)

if __name__=='__main__':unittest.main()

class ExtendedResearchTests(unittest.TestCase):
    def test_channel_excludes_current_bar_and_supports_both_sides(self):
        for side,close in [('LONG',103),('SHORT',97)]:
            r=[bar(at(DAY,'09:15')+i*300) for i in range(20)]
            r.append(bar(at(DAY,'10:55'),close=close,high=max(104,close),low=min(96,close)))
            result=evaluate('donchian20_atr',r,at(DAY,'11:00'))
            self.assertEqual(result['signal'],side)
            self.assertEqual(result['values']['channel_upper'],101)
            self.assertEqual(result['values']['channel_lower'],99)
            self.assertFalse(result['approved'])
    def test_future_append_cannot_turn_into_approved_signal(self):
        r=rows();r.append(bar(at(DAY,'12:00'),close=999,high=1000))
        for strategy in ('orb15_atr','ema9_vwap','donchian20_atr'):
            self.assertEqual(evaluate(strategy,r,at(DAY,'09:35'))['signal'],'NO TRADE')
    def test_walk_forward_embargo_and_test_disjointness(self):
        from strategy.research_validation import walk_forward_folds
        folds=walk_forward_folds(list(range(12)),4,2,1)
        self.assertEqual(len(folds),3)
        seen=set()
        for f in folds:
            self.assertLess(max(f['train']),min(f['embargo']))
            self.assertLess(max(f['embargo']),min(f['test']))
            self.assertFalse(seen.intersection(f['test']));seen.update(f['test'])
        with self.assertRaises(ValueError): walk_forward_folds([2,1])
    def test_training_selection_deterministic_and_rejects_bad_coverage(self):
        from strategy.research_validation import select_on_training
        self.assertEqual(select_on_training({'cash':[0,0],'loss':[-1,0]}),'cash')
        self.assertEqual(select_on_training({'b':[1,2],'a':[1,2]}),'a')
        with self.assertRaises(ValueError): select_on_training({'a':[1],'b':[1,2]})
        with self.assertRaises(ValueError): select_on_training({'a':[float('nan')]})
