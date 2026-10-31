"""
Regression tests: previous-close resolution in session snapshots.

Covers the stale base_price fallback bug: on a fresh install (empty candle
archive) the snapshot used hardcoded catalogue base_price as the previous
close, producing absurd day-change percentages (e.g. RELIANCE -51.6%).
"""

from __future__ import annotations
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from market_schedule import IST
from security import DatabaseManager
from session_manager import build_session_snapshot
from candle_archive import CandleArchive

SESSION_DATE = "2026-10-23"  # Friday, a regular trading day


def _session_candles(open_price: float, close_price: float):
    """75 real 5-minute bars for SESSION_DATE, 09:15-15:25 IST."""
    start = datetime(2026, 10, 23, 9, 15, tzinfo=IST)
    candles = []
    for i in range(75):
        ts = int((start + timedelta(minutes=5 * i)).timestamp())
        # Drift linearly from open to close across the session
        price = open_price + (close_price - open_price) * (i / 74.0)
        candles.append({
            "t": ts,
            "o": round(price, 2),
            "h": round(price + 1.0, 2),
            "l": round(price - 1.0, 2),
            "c": round(price, 2),
            "v": 100000 + i,
            "closed": True,
        })
    return candles


class _FakeScraper:
    """Returns real-shaped candles plus a configurable previous_close."""

    def __init__(self, previous_close):
        self.previous_close = previous_close

    def scrape_stock_candles(self, symbol, timeframe=5, exchange="NSE"):
        return {
            "status": "ok",
            "symbol": symbol,
            "previous_close": self.previous_close,
            "candles": _session_candles(1186.1, 1183.3),
        }


class TestMissingPriorClose(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.archive = CandleArchive(os.path.join(self.tmp_dir, "candles.db"))
        self.db = DatabaseManager(os.path.join(self.tmp_dir, "app.db"))

    def test_uses_yahoo_previous_close_not_stale_catalogue_base_price(self):
        snap = build_session_snapshot(
            SESSION_DATE,
            timeframe=5,
            candle_archive=self.archive,
            scraper_feed=_FakeScraper(previous_close=1175.0),
            db_manager=self.db,
        )
        stock = snap["stocks"]["RELIANCE"]
        # 1183.3 vs real prev close 1175.0 -> +0.71%; stale base_price 2450 would give -51.7%
        self.assertEqual(stock["change_basis"], "prev_close")
        self.assertAlmostEqual(stock["change_pct"], 0.71, places=2)
        self.assertEqual(stock["previous_close"], 1175.0)

    def test_missing_prior_close_marks_change_unavailable_not_session_open(self):
        snap = build_session_snapshot(
            SESSION_DATE,
            timeframe=5,
            candle_archive=self.archive,
            scraper_feed=_FakeScraper(previous_close=None),
            db_manager=self.db,
        )
        stock = snap["stocks"]["RELIANCE"]
        self.assertIsNone(stock["change_pct"])
        self.assertEqual(stock["change_basis"], "unavailable")
        self.assertIsNone(stock["previous_close"])
        # Session-open move stays available, honestly labelled as open_change_pct
        self.assertAlmostEqual(stock["open_change_pct"], -0.24, places=2)

    def test_breadth_excludes_unpriced_stocks(self):
        snap = build_session_snapshot(
            SESSION_DATE,
            timeframe=5,
            candle_archive=self.archive,
            scraper_feed=_FakeScraper(previous_close=None),
            db_manager=self.db,
        )
        breadth = snap.get("breadth", {})
        self.assertEqual(breadth.get("advancers", -1), 0)
        self.assertEqual(breadth.get("decliners", -1), 0)


if __name__ == "__main__":
    unittest.main()

