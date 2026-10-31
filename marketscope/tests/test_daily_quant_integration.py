"""Real archive and snapshot boundary checks for daily formula integration."""

from __future__ import annotations

import json
import gc
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from candle_archive import CandleArchive
from market_schedule import IST
from security import DatabaseManager
from scraper import RealTimeScraperFeed, calculate_daily_quant_from_archive
from session_manager import build_session_snapshot, enrich_daily_rankings
from daily_quant import rank_daily_stocks


def bars(day: str, count: int = 75, base: float = 100.0):
    start = datetime.fromisoformat(day).replace(hour=9, minute=15, tzinfo=IST)
    return [
        {"t": int((start + timedelta(minutes=5 * i)).timestamp()),
         "o": base + i * 0.1, "h": base + i * 0.1 + 0.3,
         "l": base + i * 0.1 - 0.2, "c": base + i * 0.1 + 0.2,
         "v": 1000 + i * 20, "closed": True}
        for i in range(count)
    ]


class DailyArchiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = CandleArchive(str(Path(self.tmp.name) / "candles.db"))

    def tearDown(self):
        gc.collect()
        self.tmp.cleanup()

    def test_prior_pivots_require_entire_latest_session_and_exclude_selected_day(self):
        self.archive.archive_closed_candles("TEST", 5, bars("2026-10-22"))
        partial = bars("2026-10-23")
        del partial[30]
        self.archive.archive_closed_candles("TEST", 5, partial)
        self.archive.archive_closed_candles("TEST", 5, bars("2026-10-26", base=500))
        prior = self.archive.get_prior_session_ohlc("TEST", 5, "2026-10-26")
        self.assertEqual(prior["session_date"], "2026-10-23")
        self.assertFalse(prior["complete"])
        self.assertEqual(prior["bar_count"], 74)
        self.archive.archive_closed_candles("TEST", 5, [bars("2026-10-23")[30]])
        complete = self.archive.get_prior_session_ohlc("TEST", 5, "2026-10-26")
        self.assertTrue(complete["complete"])
        self.assertEqual(complete["expected_bar_count"], 75)
        self.assertLess(complete["h"], 500)

    def test_warmup_is_strictly_before_session_and_only_elapsed_bars_confirm(self):
        self.archive.archive_closed_candles("TEST", 5, bars("2026-10-23"))
        current = bars("2026-10-26", 5)
        current[-2]["closed"] = False
        current[-1]["closed"] = False
        # At 09:37, the 09:30 bar elapsed, while 09:35 is still forming.
        now = datetime(2026, 10, 26, 9, 37, tzinfo=IST)
        with patch("scraper.analyze_daily_stock", return_value={"eligible": False}) as analyzer:
            calculate_daily_quant_from_archive("TEST", "2026-10-26", current, self.archive, now)
        forwarded = analyzer.call_args.args[0]
        self.assertEqual(len(forwarded), 77)
        self.assertTrue(forwarded[-2]["closed"])
        self.assertFalse(forwarded[-1]["closed"])
        self.assertFalse(current[-2]["closed"], "Raw caller data must stay unchanged")
        self.assertEqual(analyzer.call_args.kwargs["timeframe"], 5)
        self.assertEqual(analyzer.call_args.kwargs["prior_session"]["session_date"], "2026-10-23")

    def test_stored_snapshot_is_enriched_from_full_archive_without_rewriting_json(self):
        self.archive.archive_closed_candles("TEST", 5, bars("2026-10-23"))
        db = DatabaseManager(str(Path(self.tmp.name) / "market.db"))
        original = {"session_date": "2026-10-23", "session_state": "PREVIOUS", "stocks": {
            "TEST": {"symbol": "TEST", "name": "Test Equity", "status": "ok", "price": 107.6,
                     "candles_preview": bars("2026-10-23")[-20:]},
        }}
        serialized = json.dumps(original)
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO session_snapshots(session_date,timeframe,payload_json,complete,data_source,created_at) VALUES(?,?,?,?,?,?)",
                ("2026-10-23", 5, serialized, 1, "exchange", datetime.now(timezone.utc).isoformat()),
            )
        scraper = MagicMock()
        result = build_session_snapshot("2026-10-23", candle_archive=self.archive, db_manager=db, scraper_feed=scraper)
        self.assertIn("daily_ranking", result)
        self.assertEqual(result["stocks"]["TEST"]["daily_quant"]["session_candle_count"], 72)
        scraper.scrape_stock_candles.assert_not_called()
        with db.get_connection() as conn:
            stored = conn.execute("SELECT payload_json FROM session_snapshots").fetchone()[0]
        self.assertEqual(stored, serialized)
        self.assertNotIn("daily_quant", original["stocks"]["TEST"])

    def test_chart_interval_does_not_change_daily_five_minute_score(self):
        self.archive.archive_closed_candles("TEST", 5, bars("2026-10-23"))
        snapshot = {"stocks": {"TEST": {"symbol": "TEST", "status": "ok", "name": "Test Equity"}}}
        five = enrich_daily_rankings(snapshot, "2026-10-23", 5, self.archive)
        fifteen = enrich_daily_rankings(snapshot, "2026-10-23", 15, self.archive)
        self.assertEqual(five["stocks"]["TEST"]["daily_quant"], fifteen["stocks"]["TEST"]["daily_quant"])

    def test_previous_day_raw_response_cannot_duplicate_archived_warmup(self):
        prior = bars("2026-10-23")
        self.archive.archive_closed_candles("TEST", 5, prior)
        with patch("scraper.analyze_daily_stock", return_value={"eligible": False}) as analyzer:
            result = calculate_daily_quant_from_archive(
                "TEST", "2026-10-26", prior, self.archive,
                datetime(2026, 10, 26, 8, 30, tzinfo=IST),
            )
        timestamps = [c["t"] for c in analyzer.call_args.args[0]]
        self.assertEqual(len(timestamps), 72)
        self.assertEqual(len(set(timestamps)), 72)
        self.assertEqual(result["warmup_candle_count"], 72)

    def test_partial_older_archive_does_not_poison_complete_current_session(self):
        prior = bars("2026-10-22")
        del prior[30]
        self.archive.archive_closed_candles("TEST", 5, prior)
        current = bars("2026-10-23")
        result = calculate_daily_quant_from_archive("TEST", "2026-10-23", current, self.archive)
        self.assertTrue(result["eligible"], result["reason"])
        self.assertEqual(result["warmup_candle_count"], 41)
        self.assertEqual(result["session_candle_count"], 72)
        self.assertIsNone(result["pivots"])

    def test_prior_session_without_closing_bar_supplies_no_warmup(self):
        self.archive.archive_closed_candles("TEST", 5, bars("2026-10-22")[:71])
        result = calculate_daily_quant_from_archive("TEST", "2026-10-23", bars("2026-10-23"), self.archive)
        self.assertTrue(result["eligible"], result["reason"])
        self.assertEqual(result["warmup_candle_count"], 0)

    def test_invalid_old_warmup_truncates_at_boundary_without_dropping_current_volume(self):
        prior = bars("2026-10-22")
        prior[0]["v"] = 0
        self.archive.archive_closed_candles("TEST", 5, prior)
        current = bars("2026-10-23")
        result = calculate_daily_quant_from_archive("TEST", "2026-10-23", current, self.archive)
        self.assertTrue(result["eligible"], result["reason"])
        self.assertEqual(result["warmup_candle_count"], 71)
        current[0]["v"] = 0
        invalid = calculate_daily_quant_from_archive("TEST", "2026-10-23", current, self.archive)
        self.assertFalse(invalid["eligible"])

    def test_auction_gaps_and_prices_do_not_change_the_continuous_window_ranking(self):
        continuous = bars("2026-10-23")[:72]
        expected = calculate_daily_quant_from_archive("TEST", "2026-10-23", continuous, self.archive)
        auction = bars("2026-10-23")[-1:]
        auction[0].update(o=5000, h=5000, l=5000, c=5000, v=999999)
        result = calculate_daily_quant_from_archive("TEST", "2026-10-23", continuous + auction, self.archive)
        self.assertEqual(result["score"], expected["score"])
        self.assertEqual(result["price"], continuous[-1]["c"])
        self.assertEqual(result["window"]["close"], "15:15")
        stocks = {symbol: {"symbol": symbol, "status": "ok", "daily_quant": dict(result, score=score)}
                  for symbol, score in (("AAA", 70), ("BBB", 30))}
        ranked = rank_daily_stocks(stocks, "2026-10-23", "LIVE", now=datetime(2026,10,23,15,27,tzinfo=IST))
        self.assertEqual(ranked["eligible_count"], 2, "Auction time does not age completed continuous-window bars")

    def test_malformed_candle_timestamp_remains_an_explicit_rejection(self):
        current = bars("2026-10-23")
        current[30]["t"] = None
        result = calculate_daily_quant_from_archive("TEST", "2026-10-23", current, self.archive)
        self.assertFalse(result["eligible"])
        self.assertIn("timestamp", result["reason"].lower())

    def test_elapsed_final_chart_bar_is_closed_and_archived(self):
        final = bars("2026-10-23")[-1]
        payload = {"chart": {"result": [{
            "meta": {"regularMarketPrice": final["c"]},
            "timestamp": [final["t"]],
            "indicators": {"quote": [{
                "open": [final["o"]], "high": [final["h"]], "low": [final["l"]],
                "close": [final["c"]], "volume": [final["v"]],
            }]},
        }]}}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        feed = RealTimeScraperFeed(monitored_symbols=["TEST"])
        with patch("scraper.urllib.request.urlopen", return_value=response), \
             patch("scraper.time.time", return_value=final["t"] + 301), \
             patch("marketscope.candle_archive.get_candle_archive", return_value=self.archive):
            result = feed.scrape_stock_candles("TEST", timeframe=5)
        self.assertTrue(result["candles"][-1]["closed"])
        stored = self.archive.get_candles_for_session("TEST", 5, "2026-10-23")
        self.assertEqual(stored[-1]["t"], final["t"])


if __name__ == "__main__":
    unittest.main()

