"""Unit tests for marketscope.fyers_history and historical API endpoints."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from marketscope.fyers_history import (
    FyersHistoryProvider,
    FyersHistoryError,
    FyersHistoryNotConfigured,
    FyersHistoryAuthError,
    FyersHistoryRateLimitError,
    get_fyers_history_provider,
)
from marketscope.server import MarketScopeHandler, DEFAULT_DB_PATH
from marketscope.security import DatabaseManager


SAMPLE_NSE_MASTER_CSV = """10100000001,RELIANCE INDUSTRIES LTD,0,1,0.05,INE002A01018,0915-1530:,2026-10-23,,NSE:RELIANCE-EQ,10,10,1,RELIANCE,1,-1.0,XX,10100000001,None,1,2.0
10100000002,TATA CONSULTANCY SERVICES LTD,0,1,0.05,INE467B01029,0915-1530:,2026-10-23,,NSE:TCS-EQ,10,10,2,TCS,2,-1.0,XX,10100000002,None,1,2.0
10100000003,NIFTY 50,10,1,0.05,IN9999999999,0915-1530:,2026-10-23,,NSE:NIFTY50-INDEX,10,10,3,NIFTY50,3,-1.0,XX,10100000003,None,1,2.0
"""


class TestFyersHistoryProvider(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self.tmp_dir.name)
        self.provider = FyersHistoryProvider(runtime_dir=self.runtime_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_credentials_configured_detection(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(FyersHistoryProvider.credentials_configured())
            with self.assertRaises(FyersHistoryNotConfigured):
                self.provider._credentials()

        with patch.dict(os.environ, {"FYERS_APP_ID": "APP123", "FYERS_ACCESS_TOKEN": "TOK456"}):
            self.assertTrue(FyersHistoryProvider.credentials_configured())
            app_id, token = self.provider._credentials()
            self.assertEqual(app_id, "APP123")
            self.assertEqual(token, "TOK456")

    def test_read_symbol_master_csv(self):
        instruments = self.provider._read_master(SAMPLE_NSE_MASTER_CSV)
        self.assertEqual(len(instruments), 3)
        self.assertEqual(instruments[0]["symbol"], "NSE:RELIANCE-EQ")
        self.assertEqual(instruments[0]["short_symbol"], "RELIANCE")
        self.assertEqual(instruments[0]["kind"], "equity")
        self.assertEqual(instruments[2]["symbol"], "NSE:NIFTY50-INDEX")
        self.assertEqual(instruments[2]["kind"], "index")

    def test_search_instruments_ranking(self):
        self.provider._master = self.provider._read_master(SAMPLE_NSE_MASTER_CSV)
        self.provider._master_loaded_at = time.time()

        empty_res = self.provider.search_instruments("")
        self.assertEqual(empty_res["instruments"], [])

        res = self.provider.search_instruments("REL")
        self.assertTrue(len(res["instruments"]) >= 1)
        self.assertEqual(res["instruments"][0]["short_symbol"], "RELIANCE")

        res_exact = self.provider.search_instruments("TCS")
        self.assertEqual(res_exact["instruments"][0]["short_symbol"], "TCS")

    def test_candle_parameter_validations(self):
        with self.assertRaises(FyersHistoryError) as cm:
            self.provider.get_candles("NSE:RELIANCE-EQ", "2024-01-01", "2024-01-10", "INVALID_TF")
        self.assertIn("interval", str(cm.exception).lower())

        with self.assertRaises(FyersHistoryError) as cm:
            self.provider.get_candles("NSE:RELIANCE-EQ", "invalid-date", "2024-01-10", "D")
        self.assertIn("valid dates", str(cm.exception).lower())

        with self.assertRaises(FyersHistoryError) as cm:
            self.provider.get_candles("NSE:RELIANCE-EQ", "2020-01-01", "2020-01-10", "D")
        self.assertIn("6 may 2023", str(cm.exception).lower())

        with self.assertRaises(FyersHistoryError) as cm:
            self.provider.get_candles("NSE:RELIANCE-EQ", "2024-02-10", "2024-01-10", "D")
        self.assertIn("choose a date range", str(cm.exception).lower())

    def test_request_chunk_handles_auth_and_rate_limits(self):
        import urllib.error

        with patch.dict(os.environ, {"FYERS_APP_ID": "TEST", "FYERS_ACCESS_TOKEN": "TEST"}):
            auth_err = urllib.error.HTTPError("https://api.fyers.in", 401, "Unauthorized", {}, None)
            with patch("urllib.request.urlopen", side_effect=auth_err):
                from datetime import date
                with self.assertRaises(FyersHistoryAuthError):
                    self.provider._request_chunk("NSE:RELIANCE-EQ", "D", date(2024, 1, 1), date(2024, 1, 10))

            limit_err = urllib.error.HTTPError("https://api.fyers.in", 429, "Too Many Requests", {}, None)
            with patch("urllib.request.urlopen", side_effect=limit_err):
                with self.assertRaises(FyersHistoryRateLimitError):
                    self.provider._request_chunk("NSE:RELIANCE-EQ", "D", date(2024, 1, 1), date(2024, 1, 10))

    def test_request_chunk_parses_candles_successfully(self):
        mock_payload = {
            "s": "ok",
            "candles": [
                [1704081300, 2500.0, 2520.0, 2490.0, 2515.0, 150000],
                [1704167700, 2515.0, 2530.0, 2510.0, 2525.0, 180000],
            ]
        }
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch.dict(os.environ, {"FYERS_APP_ID": "TEST", "FYERS_ACCESS_TOKEN": "TEST"}):
            with patch("urllib.request.urlopen", return_value=mock_response):
                from datetime import date
                candles = self.provider._request_chunk("NSE:RELIANCE-EQ", "D", date(2024, 1, 1), date(2024, 1, 2))
                self.assertEqual(len(candles), 2)
                self.assertEqual(candles[0]["o"], 2500.0)
                self.assertEqual(candles[0]["c"], 2515.0)
                self.assertEqual(candles[0]["v"], 150000.0)


class TestFyersServerEndpoints(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(MarketScopeHandler)
        self.handler.client_address = ("127.0.0.1", 54321)
        self.handler.db = DatabaseManager(DEFAULT_DB_PATH)
        self.sent = {}
        self.status_code = [200]

        def mock_send_json(data, status=200, cookies=None):
            self.sent = data
            self.status_code[0] = status

        self.handler._send_json = mock_send_json

    def test_historical_symbols_endpoint(self):
        # Patch the handler's dependency; package and script imports can load
        # distinct provider classes after the owner's import-path fix.
        with patch("marketscope.server.get_fyers_history_provider") as provider:
            provider.return_value.search_instruments.return_value = {"instruments": [{"symbol": "NSE:TCS-EQ"}]}
            self.handler._handle_historical_symbols({"q": ["TCS"]})
            self.assertEqual(self.status_code[0], 200)
            self.assertEqual(self.sent["instruments"][0]["symbol"], "NSE:TCS-EQ")

    def test_historical_candles_endpoint_unconfigured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.handler._handle_historical_candles({
                "symbol": ["NSE:RELIANCE-EQ"],
                "from": ["2024-01-01"],
                "to": ["2024-01-10"],
                "resolution": ["D"],
            })
            self.assertEqual(self.status_code[0], 503)
            self.assertEqual(self.sent.get("error"), "fyers_not_configured")


if __name__ == "__main__":
    unittest.main()

