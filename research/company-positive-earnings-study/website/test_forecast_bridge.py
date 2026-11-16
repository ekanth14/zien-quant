import json,tempfile,unittest,importlib.util
from datetime import datetime,timedelta
from pathlib import Path
from forecast_bridge import predict_from_snapshot,model_status


class FakeEngine:
    def __init__(self):self.received=None
    def build_feature_snapshot(self,current,previous,bars,decision_at,lag_seconds=0,benchmark_bars=None):
        return {'features':{'verified_by_engine_fixture':1},'reference_price':100,
                'last_feature_bar_end':'2026-10-28T09:40:00+05:30'}
    def forecast(self,artifact,row):
        self.received=row
        return {'status':'ABSTAIN','predictions':None,'orders_enabled':False,'blockers':['TEST_ENGINE']}


def snapshot():
    return {'current':{},'previous':{},'bars':[],
      'row':{'symbol':'INFY','decision_at':'2026-10-28T09:40:00+05:30',
        'targets':{'return_1100_bps':999},'target_available_at':'2026-10-28T11:00:00+05:30',
        'provenance':{'registered_bar_lag_seconds':0,
          'original_publication_at':'2026-10-27T17:00:00+05:30',
          'financial_version_available_at':'2026-10-27T17:01:00+05:30'}}}


class BridgeTests(unittest.TestCase):
    def test_absent_artifact_is_unavailable_without_importing_numpy(self):
        with tempfile.TemporaryDirectory() as folder:
            r=predict_from_snapshot(snapshot(),folder,engine_path='/missing/file.py')
        self.assertEqual(r['status'],'MODEL_NOT_TRAINED');self.assertIsNone(r['predictions'])

    def test_failed_training_artifact_stays_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'09-40.json').write_text(json.dumps({'status':'INSUFFICIENT_VERIFIED_DATA',
                'predictions_enabled':False,'model':None,'blockers':['INSUFFICIENT_HISTORY']}))
            r=predict_from_snapshot(snapshot(),folder)
        self.assertEqual(r['status'],'MODEL_NOT_VALIDATED');self.assertEqual(r['blockers'],['INSUFFICIENT_HISTORY'])

    def test_no_future_target_is_forwarded_to_inference(self):
        e=FakeEngine()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'09-40.json').write_text(json.dumps({'model':{'fixture':True},'predictions_enabled':True}))
            r=predict_from_snapshot(snapshot(),folder,engine=e)
        self.assertNotIn('targets',e.received);self.assertNotIn('target_available_at',e.received)
        self.assertEqual(e.received['features'],{'verified_by_engine_fixture':1})
        self.assertEqual(e.received['features_known_at'],'2026-10-28T09:40:00+05:30')
        self.assertIsNone(r['predictions']);self.assertFalse(r['orders_enabled'])

    def test_no_path_from_company_can_choose_artifact_file(self):
        data=snapshot();data['row']['symbol']='../../secret'
        with tempfile.TemporaryDirectory() as folder:r=predict_from_snapshot(data,folder)
        self.assertEqual(r['status'],'SNAPSHOT_UNAVAILABLE')

    def test_naive_clock_rejected(self):
        data=snapshot();data['row']['decision_at']='2026-10-28T09:40:00'
        with tempfile.TemporaryDirectory() as folder:r=predict_from_snapshot(data,folder)
        self.assertEqual(r['status'],'SNAPSHOT_UNAVAILABLE')

    def test_corrupt_json_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'09-40.json').write_text('invalid-json')
            r=predict_from_snapshot(snapshot(),folder)
        self.assertEqual(r['status'],'SNAPSHOT_UNAVAILABLE')

    def test_status_for_all_registered_landmarks(self):
        with tempfile.TemporaryDirectory() as folder:status=model_status(folder)
        self.assertEqual(len(status['models']),4);self.assertFalse(status['orders_enabled'])

    def test_malformed_financial_snapshot_abstains(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'09-40.json').write_text(json.dumps({'model':{'fixture':True},'predictions_enabled':True}))
            for bad in (None,[],1):
                data=snapshot();data['current']=bad
                r=predict_from_snapshot(data,folder,engine=FakeEngine())
                self.assertEqual(r['status'],'SNAPSHOT_UNAVAILABLE')

    @unittest.skipUnless(importlib.util.find_spec('numpy'),'Real engine integration needs the tested NumPy runtime')
    def test_real_engine_and_shared_feature_module_are_both_loaded(self):
        data=snapshot()
        definitions={'unit':'INR lakh','basis':'consolidated','revenue_metric':'revenue_from_operations',
                     'profit_metric':'profit_for_period'}
        data['current']={**definitions,'start':'2026-07-01','period_end':'2026-09-30','revenue':110,'net_profit':12}
        data['previous']={**definitions,'start':'2026-04-01','period_end':'2026-06-30','revenue':100,'net_profit':10}
        begin=datetime.fromisoformat('2026-10-28T09:15:00+05:30')
        data['bars']=[{'ts':int((begin+timedelta(minutes=5*i)).timestamp()),
                      'o':100.,'h':101.,'l':99.,'c':100.+i/10} for i in range(5)]
        # This is deliberately not a validated model. The test exercises the
        # real feature/module interface and expects the real forecast gate.
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'09-40.json').write_text(json.dumps({'schema_version':'unsupported-test',
                'model':{'fixture':True},'predictions_enabled':True}))
            r=predict_from_snapshot(data,folder)
        self.assertEqual(r['status'],'ABSTAIN')
        self.assertEqual(r['blockers'],['UNSUPPORTED_MODEL_OR_ARTIFACT_SCHEMA_VERSION'])
        self.assertIsNone(r['predictions'])


if __name__=='__main__':unittest.main()
