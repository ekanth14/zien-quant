"""Temporal feature/target separation checks on exported local market records."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from build_local_model_dataset import make_dataset
from feature_snapshot import build_feature_snapshot

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'local_data'/'positive_events.jsonl'
BENCHMARK=HERE.parent/'public_data'/'benchmark_mornings.json'


class DatasetBuilderTests(unittest.TestCase):
    def test_live_prefix_features_equal_offline_full_history_features(self):
        full=next(json.loads(line) for line in SOURCE.read_text().splitlines() if json.loads(line)['event']['complete_price_window'])
        day=full['event']['next_trading_date'];decision=day+'T09:40:00+05:30'
        current,previous=full['current']['fact'],full['previous']['fact']
        history=build_feature_snapshot(current,previous,full['candles'],decision)
        live=build_feature_snapshot(current,previous,full['candles'][:5],decision)
        self.assertEqual(history,live)
        changed=copy.deepcopy(full['candles']);changed[-1]['h']*=100
        changed[-1]['available_at']=day+'T09:00:00+05:30'
        self.assertEqual(history,build_feature_snapshot(current,previous,changed,decision))

    def test_inference_builder_rejects_missing_or_late_available_feature_bar(self):
        full=next(json.loads(line) for line in SOURCE.read_text().splitlines() if json.loads(line)['event']['complete_price_window'])
        decision=full['event']['next_trading_date']+'T09:40:00+05:30'
        current,previous=full['current']['fact'],full['previous']['fact']
        changed=copy.deepcopy(full['candles'][:5]);changed.pop(2)
        with self.assertRaisesRegex(ValueError,'Missing, duplicate, nonconsecutive or stale'):
            build_feature_snapshot(current,previous,changed,decision)
        changed=copy.deepcopy(full['candles'][:5]);changed[0]['ts']+=.5
        with self.assertRaisesRegex(ValueError,'exact five-minute start'):
            build_feature_snapshot(current,previous,changed,decision)
        changed=copy.deepcopy(full['candles'][:5]);changed[-1]['available_at']=full['event']['next_trading_date']+'T09:41:00+05:30'
        with self.assertRaisesRegex(ValueError,'Missing, duplicate, nonconsecutive or stale'):
            build_feature_snapshot(current,previous,changed,decision)
    def test_actual_company_paths_retained_even_when_their_prices_fall(self):
        dataset=make_dataset(SOURCE)
        returns=[r['targets']['return_1100_bps'] for r in dataset['rows'] if r['decision_at'][11:16]=='09:40']
        self.assertTrue(any(v<0 for v in returns));self.assertTrue(any(v>0 for v in returns))
        self.assertEqual(len(dataset['rows']),68)

    def test_only_future_bars_can_change_future_excursions(self):
        full=next(json.loads(line) for line in SOURCE.read_text().splitlines() if json.loads(line)['event']['complete_price_window'])
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'events.jsonl';source.write_text(json.dumps(full)+'\n')
            a=next(r for r in make_dataset(source)['rows'] if r['decision_at'][11:16]=='09:40')
            changed=copy.deepcopy(full)
            # Last completed feature candle is09:35–09:40; its high is past.
            changed['candles'][4]['h']*=5
            source.write_text(json.dumps(changed)+'\n')
            b=next(r for r in make_dataset(source)['rows'] if r['decision_at'][11:16]=='09:40')
            self.assertEqual(a['targets'],b['targets'])
            self.assertNotEqual(a['features']['observed_range_log_bps'],b['features']['observed_range_log_bps'])
            changed=copy.deepcopy(full);changed['candles'][-1]['h']*=5
            source.write_text(json.dumps(changed)+'\n')
            c=next(r for r in make_dataset(source)['rows'] if r['decision_at'][11:16]=='09:40')
            self.assertEqual(a['features'],c['features']);self.assertGreater(c['targets']['max_up_bps'],a['targets']['max_up_bps'])

    def test_market_context_is_aligned_known_prefix_and_keeps_source_unverified(self):
        dataset=make_dataset(SOURCE,benchmark_path=BENCHMARK)
        self.assertEqual(len(dataset['rows']),68)
        self.assertTrue(all('market_return_from_open_log_bps' in r['features'] for r in dataset['rows']))
        self.assertFalse(dataset['benchmark_source_verified'])
        self.assertTrue(all(r['provenance']['benchmark']['index_volume_used'] is False for r in dataset['rows']))

    def test_lag_assumption_moves_reference_back_and_label_availability_forward(self):
        zero=make_dataset(SOURCE,0);lag=make_dataset(SOURCE,60)
        a=next(r for r in zero['rows'] if r['decision_at'][11:16]=='09:40')
        b=next(r for r in lag['rows'] if r['event_id']==a['event_id'] and r['decision_at']==a['decision_at'])
        self.assertEqual(a['provenance']['last_feature_bar_end'][11:16],'09:40')
        self.assertEqual(b['provenance']['last_feature_bar_end'][11:16],'09:35')
        self.assertEqual(b['target_available_at'][11:16],'11:01')
        self.assertEqual(b['provenance']['registered_bar_lag_seconds'],60)


if __name__=='__main__':unittest.main()
