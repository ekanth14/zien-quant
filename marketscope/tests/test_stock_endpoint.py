"""Regression tests for /api/stock fallbacks."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from server import MarketScopeHandler


class TestStockEndpointFallback(unittest.TestCase):
    def _handler(self):
        handler = object.__new__(MarketScopeHandler)
        handler.db = MagicMock()
        sent = {}

        def send(payload, status=200):
            sent.update(payload)
            sent["_status"] = status

        handler._send_json = send
        return handler, sent

    def test_unknown_symbol_returns_404_not_crash(self):
        """An unknown symbol with no archive or live data returns 404, not 500."""
        handler, sent = self._handler()
        feed = MagicMock()
        feed.get_full_stock_candles.return_value = []
        archive = MagicMock()
        archive.get_latest_session_date.return_value = None
        archive.get_candles_for_session.return_value = []
        state_info = {"session_date": None, "session_state": "POST_MARKET"}
        with patch("server.get_live_scraper_feed", return_value=feed), \
             patch("server.get_candle_archive", return_value=archive), \
             patch("server.get_session_state", return_value=state_info):
            handler._handle_stock({"symbol": ["ZZZZ"]})
        self.assertEqual(sent["_status"], 404)
        self.assertIn("unavailable", sent["error"].lower())

    def test_falls_back_to_latest_archived_session(self):
        """When live feed is empty, handler serves candles from the archived session."""
        handler, sent = self._handler()
        feed = MagicMock()
        feed.get_full_stock_candles.return_value = []
        archive = MagicMock()
        archive.get_latest_session_date.return_value = "2026-10-26"
        candle = {"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
        archive.get_candles_for_session.side_effect = (
            lambda sym, tf, date: [candle] if date == "2026-10-26" else []
        )
        state_info = {"session_date": "2026-10-26", "session_state": "POST_MARKET"}
        with patch("server.get_live_scraper_feed", return_value=feed), \
             patch("server.get_candle_archive", return_value=archive), \
             patch("server.get_session_state", return_value=state_info), \
             patch("server.get_stock_database") as gdb:
            gdb.return_value.get_stock.return_value = {"name": "Test", "sector": "X"}
            handler._handle_stock({"symbol": ["TEST"]})
        self.assertEqual(sent["_status"], 200)
        self.assertEqual(sent["count"], 1)


if __name__ == "__main__":
    unittest.main()

