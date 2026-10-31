"""Offline HTTP wiring, read-only history and all-or-nothing input validation."""
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from marketscope import server
from marketscope.results_history import fetch_fyers_event_candles, import_candle_csv, valid_candle


class TestResultsEndpoints(unittest.TestCase):
    def handler(self):
        handler = object.__new__(server.MarketScopeHandler)
        handler._send_json = MagicMock()
        return handler

    def test_bad_dates_cannot_reach_storage(self):
        with patch.object(server, "get_results_service") as service:
            for query in ({"from": ["not-a-date"]}, {"from": ["2026-10-20"], "to": ["2026-10-01"]}, {"limit": ["999"]}, {"offset": ["-1"]}):
                handler = self.handler()
                handler._handle_results(query)
                self.assertEqual(handler._send_json.call_args.kwargs["status"], 400)
            service.assert_not_called()

    def test_filters_are_applied_before_pagination_in_service(self):
        with patch.object(server, "get_results_service") as get_service:
            get_service.return_value.list_events.return_value = {"events": [], "total": 0}
            handler = self.handler()
            handler._handle_results({"from": ["2026-10-01"], "search": ["Bank"], "tone": ["positive"], "offset": ["100"]})
            options = get_service.return_value.list_events.call_args.kwargs
            self.assertEqual(options["search"], "bank")
            self.assertEqual(options["offset"], 100)
            self.assertEqual(options["tone"], "positive")

    def test_missing_result_is_404_without_analysis(self):
        with patch.object(server, "get_results_service") as get_service, patch.object(server, "analyze_event") as analyze:
            get_service.return_value.get_event.return_value = None
            handler = self.handler()
            handler._handle_result_detail({"id": ["missing"]})
            self.assertEqual(handler._send_json.call_args.kwargs["status"], 404)
            analyze.assert_not_called()

    def test_detail_uses_actual_symbol_archive_and_aware_clock(self):
        with patch.object(server, "get_results_service") as get_service, patch.object(server, "analyze_event", return_value={"status": "insufficient_data"}) as analyze:
            service = get_service.return_value
            service.get_event.return_value = {"id": "filing", "symbol": "TEST"}
            service.get_candles.return_value = []
            handler = self.handler()
            handler._handle_result_detail({"id": ["filing"]})
            self.assertEqual(analyze.call_args.args[2], {"TEST": []})
            self.assertIsNotNone(analyze.call_args.args[3].tzinfo)
            self.assertEqual(handler._send_json.call_args.args[0]["analysis"]["status"], "insufficient_data")


class TestResultsHistory(unittest.TestCase):
    now = datetime(2026, 10, 26, 7, tzinfo=timezone.utc)
    stamp = int(datetime(2026, 10, 26, 3, 45, tzinfo=timezone.utc).timestamp())

    def test_rejects_future_nonfinite_invalid_ohlc_and_off_grid(self):
        row = {"t": self.stamp, "o": 100, "h": 102, "l": 99, "c": 101, "v": 100}
        self.assertIsNotNone(valid_candle(row, 5, self.now))
        for changes in ({"t": int(self.now.timestamp())}, {"h": 90}, {"c": float("nan")}, {"t": self.stamp + 1}, {"v": -1}):
            self.assertIsNone(valid_candle(row | changes, 5, self.now))

    def test_csv_rejects_whole_file_before_writing_any_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.csv"
            path.write_text(f"symbol,t,o,h,l,c,v\nTEST,{self.stamp},100,102,99,101,100\nTEST,{self.stamp+300},100,90,99,101,100\n")
            archive = MagicMock()
            with self.assertRaises(ValueError):
                import_candle_csv(str(path), archive, source="test provider", now=self.now)
            archive.archive_closed_candles.assert_not_called()

    def test_csv_preserves_source_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.csv"
            row = f"TEST,{self.stamp},100,102,99,101,100\n"
            path.write_text("symbol,t,o,h,l,c,v\n" + row + row)
            archive = MagicMock()
            archive.archive_closed_candles.return_value = 1
            result = import_candle_csv(str(path), archive, source="test provider", now=self.now)
            self.assertEqual(result["rows"], 1)
            self.assertEqual(archive.archive_closed_candles.call_args.kwargs["source"], "test provider")

    def test_fyers_only_requests_fixed_readonly_host_and_drops_partial_candles(self):
        with patch.dict(os.environ, {"FYERS_APP_ID": "example-app", "FYERS_ACCESS_TOKEN": "example-access"}):
            response = {"s": "ok", "candles": [[self.stamp, 100, 102, 99, 101, 100], [int(self.now.timestamp()), 100, 102, 99, 101, 100]]}
            opener = MagicMock(return_value=io.BytesIO(json.dumps(response).encode()))
            rows = fetch_fyers_event_candles("TEST", "2026-10-26", now=self.now, opener=opener)
            self.assertEqual(len(rows), 1)
            request = opener.call_args.args[0]
            self.assertTrue(request.full_url.startswith("https://api-t1.fyers.in/data/history?"))
            self.assertNotIn("example-access", request.full_url)
            self.assertIn("Authorization", request.headers)

    def test_provider_errors_do_not_echo_authentication_material(self):
        with patch.dict(os.environ, {"FYERS_APP_ID": "example-app", "FYERS_ACCESS_TOKEN": "example-access"}):
            opener = MagicMock(side_effect=RuntimeError("example-access"))
            with self.assertRaises(RuntimeError) as error:
                fetch_fyers_event_candles("TEST", "2026-10-26", now=self.now, opener=opener)
            self.assertNotIn("example-access", str(error.exception))
