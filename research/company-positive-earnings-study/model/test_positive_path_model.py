"""Synthetic mechanics tests only; no claimed market accuracy or strategy P&L."""
import copy
import json
import math
import unittest
from datetime import datetime,timedelta

import numpy as np

from positive_path_model import (Config, QUALITY_FLAGS, apply_intervals, canonical_hash,
    chronological_split, date_weights, effective_size, fit_kernel, forecast,
    raw_prediction, row_reasons, train, validate_dataset)


def fixture(days=30,per_day=10):
    rows=[]
    first=datetime.fromisoformat('2024-01-02T09:40:00+05:30')
    for d in range(days):
        day=first+timedelta(days=d)
        for j in range(per_day):
            signal=(-2,-1,0,1,2)[j%5]
            value=signal*55+(d%3-1)*.5
            rows.append({
              'event_id':f'E{d}_{j}','symbol':f'TEST{j}','session_date':day.date().isoformat(),
              'announced_at':(day-timedelta(days=1)).replace(hour=17,minute=0).isoformat(),
              'decision_at':day.isoformat(),'features_known_at':day.isoformat(),
              'target_available_at':day.replace(hour=11,minute=1).isoformat(),
              'reference_price':100.,'movement_band_bps':10.,'sector':'TEST',
              'feature_variant':'synthetic_mechanics_core_v1',
              'features':{'momentum':float(signal),'revenue_growth':.1,'profit_growth':.2},
              'targets':{'return_1100_bps':value,'max_up_bps':max(value,0)+20,
                         'max_down_bps':min(value,0)-20,'return_1000_bps':value*.4},
              'eligibility':{'positive':True,'leverage_above_3':True,'in_dashboard':True},
              'quality':dict.fromkeys(QUALITY_FLAGS,True),
              'provenance':{
                'financial_version_available_at':(day-timedelta(days=1)).replace(hour=17,minute=1).isoformat(),
                'original_publication_at':(day-timedelta(days=1)).replace(hour=17,minute=0).isoformat(),
                'last_feature_bar_end':day.isoformat(),'registered_bar_lag_seconds':0,
                'acquired_at':'2026-10-27T14:00:00+05:30',
                'app_first_seen_at':'2026-10-27T14:00:00+05:30',
                'extraction_completed_at':'2026-10-27T14:01:00+05:30',
                'last_feature_bar_arrived_at':day.isoformat()}})
    return {'schema_version':'1.0','research_mode':'PUBLIC_INFORMATION_BACKTEST','rows':rows,
            'feature_variant':'synthetic_mechanics_core_v1',
            'synthetic_mechanics_only':True}


def small_config():
    return Config(min_dates=20,min_rows=150,min_train_dates=8,min_selection_dates=3,
                  min_calibration_dates=5,min_test_dates=5,min_symbols=5,min_class_train=3,
                  min_local_event_ess=3,min_local_date_ess=2,
                  bandwidths=(.5,1.),half_lives_days=(365.,),sector_shrinkages=(10.,),
                  temperatures=(.75,1.,1.5),bootstrap_repetitions=100)


def future_query(row):
    result=copy.deepcopy(row)
    result.update(session_date='2026-10-27',event_id='NEW',announced_at='2026-10-26T17:00:00+05:30',
        decision_at='2026-10-27T09:40:00+05:30',features_known_at='2026-10-27T09:40:00+05:30')
    result['provenance']['last_feature_bar_end']=result['decision_at']
    return result


class AvailabilityTests(unittest.TestCase):
    def test_public_reconstruction_allows_later_acquisition(self):
        row=fixture(1)['rows'][0]
        self.assertEqual(row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'),[])

    def test_operational_replay_requires_actual_app_clocks(self):
        row=fixture(1)['rows'][0]
        self.assertIn('APP_INPUTS_NOT_AVAILABLE_AT_DECISION',row_reasons(row,'OPERATIONAL_APP_REPLAY'))

    def test_revision_after_decision_rejected_even_with_earlier_release(self):
        row=fixture(1)['rows'][0]
        row['provenance']['financial_version_available_at']='2024-01-02T12:00:00+05:30'
        self.assertIn('PUBLIC_FINANCIAL_VERSION_AFTER_DECISION',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))

    def test_unclosed_or_late_bar_rejected(self):
        row=fixture(1)['rows'][0]
        row['provenance']['registered_bar_lag_seconds']=60
        self.assertIn('UNAVAILABLE_OR_UNCLOSED_FEATURE_BAR',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))

    def test_stale_but_closed_snapshot_is_rejected(self):
        row=fixture(1)['rows'][0];row['provenance']['last_feature_bar_end']='2024-01-02T09:20:00+05:30'
        self.assertIn('STALE_OR_MISALIGNED_FEATURE_SNAPSHOT',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))

    def test_market_features_require_separately_verified_benchmark_lineage(self):
        row=fixture(1)['rows'][0];row['features']['market_return_from_open_log_bps']=5.
        row['provenance']['benchmark']={'instrument':'NIFTY50_INDEX','provider_verified':False,
            'source_timestamps_verified':True,'price_adjustments_verified':True,'instrument_mapping_verified':True,
            'last_feature_bar_end':row['decision_at'],'registered_bar_lag_seconds':0,'bar_interval_minutes':5}
        self.assertIn('UNVERIFIED_BENCHMARK_SOURCE_OR_DEFINITIONS',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))
        row['provenance']['benchmark']['provider_verified']=True
        self.assertEqual(row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'),[])
        row['provenance']['benchmark']['last_feature_bar_end']='2024-01-02T09:35:00+05:30'
        self.assertIn('STALE_UNALIGNED_OR_UNAVAILABLE_BENCHMARK_SNAPSHOT',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))

    def test_clock_cannot_precede_used_dependency(self):
        row=fixture(1)['rows'][0];row['features_known_at']='2024-01-02T09:35:00+05:30'
        self.assertIn('FEATURE_CLOCK_PRECEDES_A_USED_DEPENDENCY',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))

    def test_duplicate_company_session_not_independent_observation(self):
        data=fixture(2);data['rows'].append(copy.deepcopy(data['rows'][0]))
        _,audit=validate_dataset(data,'09:40')
        self.assertEqual(audit['rejection_counts']['DUPLICATE_COMPANY_SESSION_LANDMARK'],1)

    def test_missing_quality_cannot_be_bypassed_by_more_rows(self):
        data=fixture(30)
        for row in data['rows']:row['quality']['original_financial_vintage_verified']=False
        artifact=train(data,'09:40',small_config())
        self.assertIsNone(artifact['model']);self.assertFalse(artifact['predictions_enabled'])
        self.assertEqual(artifact['data_audit']['eligible_verified_rows'],0)

    def test_current_one_day_is_not_trainable(self):
        data=fixture(1,per_day=400)
        artifact=train(data,'09:40')
        self.assertIn('INSUFFICIENT_INDEPENDENT_DATE_COVERAGE',artifact['blockers'])
        self.assertIsNone(artifact['model'])

    def test_targets_not_available_at_training_are_removed(self):
        data=fixture(30)
        artifact=train(data,'09:40',small_config(),created_at='2024-01-01T00:00:00+05:30')
        self.assertEqual(artifact['data_audit']['eligible_verified_rows'],0)
        self.assertEqual(artifact['data_audit']['rejection_counts']['TARGET_NOT_AVAILABLE_AT_TRAINING_TIME'],300)

    def test_past_checkpoint_and_invalid_extremes_are_rejected(self):
        row=fixture(1)['rows'][0]
        row['decision_at']='2024-01-02T10:00:00+05:30'
        self.assertIn('PAST_OR_CURRENT_CHECKPOINT_IS_NOT_A_FUTURE_TARGET',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))
        row=fixture(1)['rows'][0];row['targets']['max_down_bps']=10.
        self.assertIn('INVALID_FUTURE_EXCURSION_SIGNS',row_reasons(row,'PUBLIC_INFORMATION_BACKTEST'))


class EstimatorTests(unittest.TestCase):
    def test_dates_are_disjoint_and_ordered(self):
        rows=fixture(30)['rows'];fit,cal,test=chronological_split(rows,small_config())
        self.assertLess(max(r['session_date'] for r in fit),min(r['session_date'] for r in cal))
        self.assertLess(max(r['session_date'] for r in cal),min(r['session_date'] for r in test))
        allids=[set(r['event_id'] for r in part) for part in (fit,cal,test)]
        self.assertFalse(allids[0]&allids[1] or allids[1]&allids[2])

    def test_date_balancing_does_not_let_busy_day_dominate(self):
        rows=[{'session_date':'2024-01-01'}]*100+[{'session_date':'2024-01-02'}]
        weights=date_weights(rows)
        self.assertAlmostEqual(float(weights[:100].sum()),.5)
        self.assertAlmostEqual(float(weights[-1]),.5)
        self.assertAlmostEqual(effective_size(np.asarray([.5,.5])),2.)

    def test_training_scales_do_not_see_heldout_features(self):
        data=fixture(30);cfg=small_config()
        a=train(data,'09:40',cfg,created_at='2025-01-01T00:00:00+05:30')
        changed=copy.deepcopy(data)
        for row in changed['rows'][-60:]:row['features']['momentum']+=100
        b=train(changed,'09:40',cfg,created_at='2025-01-01T00:00:00+05:30')
        self.assertEqual(a['model']['median'],b['model']['median'])
        self.assertEqual(a['model']['scale'],b['model']['scale'])
        self.assertEqual(a['model']['hyperparameters'],b['model']['hyperparameters'])
        self.assertEqual(a['calibration'],b['calibration'])

    def test_joint_kernel_distribution_and_calibration_fit_actual_parameters(self):
        data=fixture(30);artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        self.assertIsNotNone(artifact['model']);self.assertIsNotNone(artifact['calibration'])
        self.assertEqual(artifact['calibration']['date_block_count'],6)
        self.assertEqual(artifact['partitions']['test']['dates'],6)
        self.assertTrue(math.isfinite(artifact['test_metrics']['log_loss']))
        json.dumps(artifact,allow_nan=False)

    def test_synthetic_fit_can_never_enable_market_forecasts(self):
        artifact=train(fixture(30),'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        self.assertEqual(artifact['status'],'SYNTHETIC_MECHANICS_ONLY')
        self.assertFalse(artifact['predictions_enabled'])
        self.assertIn('SYNTHETIC_DATA_CANNOT_ESTABLISH_MARKET_FORECASTS',artifact['blockers'])

    def test_query_never_uses_future_training_labels_or_same_event(self):
        data=fixture(2);rows=data['rows'];model=fit_kernel(rows,{'bandwidth':1.,'half_life_days':365.,'sector_shrinkage':10.})
        with self.assertRaisesRegex(ValueError,'MODEL_INCLUDES_FUTURE_OR_SAME_SESSION_INFORMATION'):
            raw_prediction(model,rows[0])

    def test_mixed_past_and_future_fitting_rows_reject_complete_artifact(self):
        rows=fixture(3)['rows']
        model=fit_kernel(rows[:10]+rows[20:],{'bandwidth':1.,'half_life_days':365.,'sector_shrinkage':10.})
        with self.assertRaisesRegex(ValueError,'MODEL_INCLUDES_FUTURE_OR_SAME_SESSION_INFORMATION'):
            raw_prediction(model,rows[10])

    def test_all_required_magnitudes_and_joint_date_coverage_are_evaluated(self):
        artifact=train(fixture(30),'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        metrics=artifact['test_metrics']
        self.assertEqual(set(metrics['magnitude_component_gates']),set(artifact['model']['target_names']))
        self.assertEqual(metrics['joint_coverage_date_count'],6)
        self.assertGreaterEqual(metrics['simultaneous_all_issuers_all_targets_date_coverage'],0)
        self.assertLessEqual(metrics['simultaneous_all_issuers_all_targets_date_coverage'],1)

    def test_bad_simultaneous_heldout_coverage_prevents_release(self):
        data=fixture(30)
        for row in data['rows'][-60:]:
            row['targets'].update(return_1100_bps=5000.,max_up_bps=5020.,max_down_bps=-20.,return_1000_bps=2000.)
        artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        self.assertEqual(artifact['test_metrics']['simultaneous_all_issuers_all_targets_date_coverage'],0.)
        self.assertIn('HELDOUT_SIMULTANEOUS_DATE_INTERVAL_UNDERCOVERAGE',artifact['test_metrics']['release_gate_reasons'])
        self.assertFalse(artifact['predictions_enabled'])

    def test_forecast_honors_artifact_time_and_validation_gate(self):
        data=fixture(30);artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        row=copy.deepcopy(data['rows'][-1])
        result=forecast(artifact,row)
        self.assertIn('ARTIFACT_NOT_AVAILABLE_AT_DECISION',result['blockers']);self.assertIsNone(result['predictions'])
        future=copy.deepcopy(row)
        for key in ('decision_at','features_known_at'):future[key]='2026-10-27T09:40:00+05:30'
        future['session_date']='2026-10-27';future['event_id']='NEW';future['announced_at']='2026-10-26T17:00:00+05:30'
        future['provenance']['last_feature_bar_end']=future['decision_at']
        artifact['predictions_enabled']=False
        result=forecast(artifact,future,diagnostic=True)
        self.assertIn('MODEL_FAILED_VALIDATION_RELEASE_GATE',result['blockers'])
        self.assertIsNone(result['predictions']);self.assertIsNotNone(result['unvalidated_diagnostics'])
        self.assertAlmostEqual(sum(result['unvalidated_diagnostics']['direction_probabilities'].values()),1.)

    def test_ood_query_abstains(self):
        data=fixture(30);artifact=train(data,'09:40',small_config(),created_at='2023-01-01T00:00:00+05:30')
        # A model genuinely fitted before the query is required; use metadata
        # time after synthetic test, then query a future date.
        artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        row=copy.deepcopy(data['rows'][-1]);row.update(session_date='2026-10-27',event_id='NEW',announced_at='2026-10-26T17:00:00+05:30',decision_at='2026-10-27T09:40:00+05:30',features_known_at='2026-10-27T09:40:00+05:30')
        row['provenance']['last_feature_bar_end']=row['decision_at'];row['features']['momentum']=1000.
        result=forecast(artifact,row)
        self.assertIn('OUT_OF_DISTRIBUTION_FEATURES',result['blockers']);self.assertIsNone(result['predictions'])

    def test_artifact_clock_and_landmark_are_integrity_bound(self):
        data=fixture(30);artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        query=future_query(data['rows'][-1])
        for key,value in (('created_at','2024-01-01T00:00:00+05:30'),('landmark_ist','10:30')):
            changed=copy.deepcopy(artifact);changed[key]=value
            self.assertIn('ARTIFACT_INTEGRITY_MISMATCH',forecast(changed,query)['blockers'])

    def test_lag_and_feature_variant_are_training_serving_contracts(self):
        data=fixture(30);artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        query=future_query(data['rows'][-1]);query['provenance']['registered_bar_lag_seconds']=60
        query['provenance']['last_feature_bar_end']='2026-10-27T09:35:00+05:30'
        self.assertIn('INFERENCE_FEATURE_LAG_BAND_OR_BENCHMARK_CONTRACT_MISMATCH',forecast(artifact,query)['blockers'])
        query=future_query(data['rows'][-1]);query['feature_variant']='different_feature_variant'
        self.assertIn('INFERENCE_FEATURE_LAG_BAND_OR_BENCHMARK_CONTRACT_MISMATCH',forecast(artifact,query)['blockers'])

    def test_unsupported_artifact_version_is_rejected(self):
        data=fixture(30);artifact=train(data,'09:40',small_config(),created_at='2025-01-01T00:00:00+05:30')
        artifact['model_version']='future-incompatible-model-99'
        self.assertIn('UNSUPPORTED_MODEL_OR_ARTIFACT_SCHEMA_VERSION',forecast(artifact,future_query(data['rows'][-1]))['blockers'])

    def test_nan_features_rejected_but_audit_hash_is_serializable(self):
        data=fixture(1);data['rows'][0]['features']['momentum']=float('nan')
        artifact=train(data)
        self.assertIn('MISSING_OR_NONFINITE_FEATURES',artifact['data_audit']['rejections'][0]['reasons'])
        json.dumps(artifact,allow_nan=False)
        self.assertEqual(canonical_hash({'x':float('nan')}),canonical_hash({'x':float('nan')}))


if __name__=='__main__':unittest.main()
