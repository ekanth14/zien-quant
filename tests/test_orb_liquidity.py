import unittest
from copy import deepcopy
from datetime import date,timedelta
from strategy.research_lab import evaluate,cost_stress
from strategy.papertrade_engine import at
from test_research_lab import rows,bar,DAY,config


class LiquidityTests(unittest.TestCase):
    def fixture(self):
        prior=[]; d=date(2026,10,29)
        while len(prior)<10:
            if d.weekday()<5: prior.append(d.isoformat())
            d-=timedelta(days=1)
        history=[bar(at(day,'09:15')+i*300) for day in sorted(prior) for i in range(4)]
        for b in history:b['v']=100000
        current=rows()[-4:]
        for b in current:b['v']=200000
        return history+current

    def test_volume_and_liquidity_gate_and_sizing_evidence(self):
        r=evaluate('orb15_rvol_liquid',self.fixture(),at(DAY,'09:35'))
        self.assertEqual(r['signal'],'LONG')
        self.assertEqual(r['values']['liquidity']['opening_rvol'],2)
        self.assertEqual(r['values']['liquidity']['max_quantity'],1000)

    def test_missing_reference_and_low_relative_volume_block(self):
        self.assertEqual(evaluate('orb15_rvol_liquid',rows(),at(DAY,'09:35'))['signal'],'NO TRADE')
        bars=self.fixture()
        for b in bars[-4:]:b['v']=100000
        self.assertIn('below 1.5',evaluate('orb15_rvol_liquid',bars,at(DAY,'09:35'))['reason'])

    def test_stress_runs_are_separate_and_inputs_unchanged(self):
        cfg=config();original=deepcopy(cfg)
        report=cost_stress('cash_baseline',DAY,{},cfg)
        self.assertEqual(cfg,original)
        self.assertEqual([r['cost_multiplier'] for r in report['runs']],[1,1.5,2])
        self.assertEqual(report['runs'][-1]['report']['config']['slippage_bps'],10)
