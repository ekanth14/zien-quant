"""Imported observations stay isolated; no labels or client attestations enter inference."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from marketscope import company_study, server


class CompanyStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = company_study.CompanyStudy()

    def test_actual_import_coverage_and_no_orders(self):
        result = self.study.response()
        self.assertEqual(result['counts']['positive_events'], 62)
        self.assertEqual(result['counts']['complete_windows'], 17)
        self.assertEqual(result['counts']['missing_windows'], 45)
        self.assertEqual(result['counts']['above_open'], 9)
        self.assertEqual(result['counts']['below_open'], 8)
        self.assertFalse(result['orders_enabled'])
        self.assertTrue(all(m['status'] == 'INSUFFICIENT_VERIFIED_DATA' for m in result['models'].values()))

    def test_missing_prices_never_gain_forecast_or_candles(self):
        event = next(e for e in self.study.events if not e['complete_price_window'])
        detail = self.study.response(event_id=event['id'])['detail']
        self.assertEqual(detail['candles'], [])
        self.assertIsNone(detail['forecast']['predictions'])
        self.assertEqual(detail['forecast']['status'], 'PRICE_WINDOW_MISSING')

    def test_original_complete_prices_and_disabled_registry(self):
        event = next(e for e in self.study.events if e['complete_price_window'])
        for landmark in company_study.LANDMARKS:
            detail = self.study.response(event_id=event['id'], landmark=landmark)['detail']
            self.assertEqual(len(detail['candles']), 21)
            self.assertEqual(detail['event']['symbol'], event['symbol'])
            self.assertIsNone(detail['forecast']['predictions'])
            self.assertEqual(detail['forecast']['status'], 'MODEL_NOT_VALIDATED')
            self.assertFalse(detail['quality']['original_financial_vintage_verified'])

    def test_only_backend_prefix_and_no_targets_sent_to_bridge(self):
        event = next(e for e in self.study.events if e['complete_price_window'])
        original = copy.deepcopy(self.study.rows)
        bridge = Mock()
        bridge.model_status.return_value = {'models': {}}
        bridge.predict_from_snapshot.return_value = {'predictions': None}
        with patch.object(company_study, '_bridge', return_value=bridge):
            self.study.response(event_id=event['id'], landmark='09:40')
        snapshot = bridge.predict_from_snapshot.call_args.args[0]
        self.assertNotIn('targets', snapshot['row'])
        self.assertNotIn('target_available_at', snapshot['row'])
        self.assertEqual(len(snapshot['bars']), 5)
        self.assertEqual(len(snapshot['benchmark_bars']), 21)
        self.assertEqual(self.study.rows, original)

    def test_filters_ids_and_internal_paths(self):
        self.assertEqual(len(self.study.response(coverage='complete')['events']), 17)
        self.assertEqual(len(self.study.response(coverage='missing')['events']), 45)
        self.assertTrue(all('APOLLO' in (e['symbol']+e['company']).upper() for e in self.study.response(search='apollo')['events']))
        for kwargs in ({'event_id': '../../.env'}, {'landmark': '11:00'}, {'coverage': 'execute'}, {'search': 'x'*101}):
            with self.assertRaises(ValueError): self.study.response(**kwargs)
        with self.assertRaises(KeyError): self.study.response(event_id='0'*24)
        self.assertNotIn(chr(47)+'Users'+chr(47), json.dumps(self.study.response()))

    def test_http_input_cannot_select_registry_or_quality(self):
        handler = object.__new__(server.MarketScopeHandler)
        handler._send_json = Mock()
        handler.path = '/api/company-study?registry=../../.env&quality=true'
        with patch.object(server.rate_limiter, 'is_allowed', return_value=True), patch.object(server, 'get_company_study') as get:
            handler.do_GET()
        self.assertEqual(handler._send_json.call_args.kwargs['status'], 400)
        get.assert_not_called()

    def test_http_dispatch_returns_real_snapshot(self):
        handler = object.__new__(server.MarketScopeHandler)
        handler._send_json = Mock()
        handler.path = '/api/company-study?coverage=complete'
        with patch.object(server.rate_limiter, 'is_allowed', return_value=True): handler.do_GET()
        self.assertEqual(len(handler._send_json.call_args.args[0]['events']), 17)

    def test_integrity_failure_rejects_changed_snapshot(self):
        original = company_study._json
        def changed(path):
            value = original(path)
            if path.name == 'SHA256SUMS.json': value['local_data/positive_events.jsonl'] = '0'*64
            return value
        with patch.object(company_study, '_json', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'integrity'): company_study.CompanyStudy()


if __name__ == '__main__': unittest.main()
