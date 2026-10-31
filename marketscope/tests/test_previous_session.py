"""
Unit Tests for MarketScope Previous Session & Session State Engine
- Frozen clock session state resolution (Saturday, Monday pre-open, live, after-hours, holiday)
- Session snapshots: building from fixtures, incomplete session handling, and immutability
- Live feed down fallback (with stored snapshot vs without stored snapshot)
- Signal scorecard generation (0 signals honest note vs fixture signals)
- Zero-demo grep test asserting no banned words in runtime code
- Signal journal LIVE-only enforcement (never logs signals on PREVIOUS/CLOSED)
"""

from __future__ import annotations
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, date, time as dt_time, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Setup sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from market_schedule import IST
from security import DatabaseManager
from session_manager import (
    get_session_state,
    build_session_snapshot,
    get_session_scorecard,
)
from candle_archive import CandleArchive
from signal_journal import SignalJournal
try:
    from marketscope.tests.fixture_loader import load_test_fixtures, get_fixture_stock_candles
except ImportError:
    try:
        from tests.fixture_loader import load_test_fixtures, get_fixture_stock_candles
    except ImportError:
        from fixture_loader import load_test_fixtures, get_fixture_stock_candles


class TestFrozenClockSessionState(unittest.TestCase):
    """Verifies get_session_state against frozen datetime clocks."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        # Apply migration 004
        mig_path = Path(__file__).parent.parent / "migrations" / "004_session_snapshots.sql"
        if mig_path.exists():
            with open(mig_path, "r", encoding="utf-8") as f:
                with self.db.get_connection() as conn:
                    conn.executescript(f.read())

        # Seed completed snapshot for Friday 2026-10-23
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO session_snapshots (session_date, timeframe, payload_json, complete, data_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("2026-10-23", 5, "{}", 1, "Exchange Live Feed (Yahoo Finance)", "2026-10-23T15:30:00Z"),
            )
            conn.commit()

        self.archive_path = os.path.join(self.tmp_dir, "candles.db")
        self.archive = CandleArchive(self.archive_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_saturday_weekend_returns_previous_friday(self):
        # Saturday 2026-10-24 12:00:00 IST
        clock = datetime(2026, 10, 24, 12, 0, 0, tzinfo=IST)
        res = get_session_state(clock, candle_archive=self.archive, db_manager=self.db)
        self.assertEqual(res["session_state"], "PREVIOUS")
        self.assertEqual(res["session_date"], "2026-10-23")
        self.assertFalse(res["is_trading_day"])

    def test_monday_pre_open_returns_previous_friday(self):
        # Monday 2026-10-26 08:30:00 IST (pre-open)
        clock = datetime(2026, 10, 26, 8, 30, 0, tzinfo=IST)
        res = get_session_state(clock, candle_archive=self.archive, db_manager=self.db)
        self.assertEqual(res["session_state"], "PREVIOUS")
        self.assertEqual(res["session_date"], "2026-10-23")
        self.assertTrue(res["is_trading_day"])

    def test_monday_live_market_returns_live(self):
        # Monday 2026-10-26 11:00:00 IST (market open)
        clock = datetime(2026, 10, 26, 11, 0, 0, tzinfo=IST)
        res = get_session_state(clock, candle_archive=self.archive, db_manager=self.db)
        self.assertEqual(res["session_state"], "LIVE")
        self.assertEqual(res["session_date"], "2026-10-26")
        self.assertTrue(res["is_trading_day"])

    def test_monday_after_hours_returns_closed_today(self):
        # Monday 2026-10-26 16:00:00 IST (after 15:30 close)
        clock = datetime(2026, 10, 26, 16, 0, 0, tzinfo=IST)
        res = get_session_state(clock, candle_archive=self.archive, db_manager=self.db)
        self.assertEqual(res["session_state"], "CLOSED_TODAY")
        self.assertEqual(res["session_date"], "2026-10-26")
        self.assertTrue(res["is_trading_day"])

    def test_republic_day_holiday_returns_prior_session(self):
        # Republic Day 2026-01-26 (Monday holiday) at 11:00:00 IST
        clock = datetime(2026, 1, 26, 11, 0, 0, tzinfo=IST)
        res = get_session_state(clock, candle_archive=self.archive, db_manager=self.db)
        self.assertEqual(res["session_state"], "PREVIOUS")
        self.assertFalse(res["is_trading_day"])
        self.assertIn(res["session_date"], ("2026-10-23", "2026-01-23"))


class TestSessionSnapshotLogic(unittest.TestCase):
    """Verifies snapshot building from candles, incomplete session handling, and immutability."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        mig_path = Path(__file__).parent.parent / "migrations" / "004_session_snapshots.sql"
        if mig_path.exists():
            with open(mig_path, "r", encoding="utf-8") as f:
                with self.db.get_connection() as conn:
                    conn.executescript(f.read())

        self.archive_path = os.path.join(self.tmp_dir, "candles.db")
        self.archive = CandleArchive(self.archive_path)

        # Seed candles for all stocks from fixture into archive
        fixtures = load_test_fixtures()
        stocks = fixtures.get("stocks", {})
        for sym, data in stocks.items():
            candles = data["candles"]
            self.archive.archive_closed_candles(sym, 5, candles, source="exchange")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_snapshot_built_from_archive_candles(self):
        mock_scraper = MagicMock()
        mock_scraper.scrape_stock_candles.return_value = {"candles": []}

        snapshot = build_session_snapshot(
            session_date="2026-10-23",
            timeframe=5,
            candle_archive=self.archive,
            scraper_feed=mock_scraper,
            db_manager=self.db,
        )
        self.assertEqual(snapshot["session_date"], "2026-10-23")
        self.assertEqual(snapshot["timeframe"], 5)
        self.assertIn(snapshot["complete"], (1, True))
        self.assertIn("RELIANCE", snapshot["stocks"])
        rel = snapshot["stocks"]["RELIANCE"]
        self.assertEqual(rel["status"], "ok")
        self.assertGreater(rel["price"], 0)
        self.assertIn("metrics", rel)
        self.assertIn("ai_decision", rel)
        self.assertIn("vwap", rel)
        self.assertIn("support", rel)
        self.assertIn("resistance", rel)

    def test_incomplete_session_not_saved_as_final(self):
        tmp_archive = CandleArchive(os.path.join(self.tmp_dir, "incomplete_candles.db"))
        mock_scraper = MagicMock()
        mock_scraper.scrape_stock_candles.return_value = {"candles": []}

        # Archive only 30 bars for RELIANCE
        rel_candles = get_fixture_stock_candles("RELIANCE")[:30]
        tmp_archive.archive_closed_candles("RELIANCE", 5, rel_candles, source="exchange")

        snapshot = build_session_snapshot(
            session_date="2026-10-23",
            timeframe=5,
            candle_archive=tmp_archive,
            scraper_feed=mock_scraper,
            db_manager=self.db,
        )
        self.assertIn(snapshot["complete"], (0, False))
        self.assertEqual(snapshot["status_label"], "INCOMPLETE SESSION")

        # Verify it was NOT stored as complete=1 in session_snapshots table
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT complete FROM session_snapshots WHERE session_date = '2026-10-23'")
            row = cur.fetchone()
            self.assertTrue(row is None or row[0] == 0)

    def test_snapshot_immutability(self):
        mock_scraper = MagicMock()
        mock_scraper.scrape_stock_candles.return_value = {"candles": []}

        # First build and save
        snap1 = build_session_snapshot(
            session_date="2026-10-23",
            timeframe=5,
            candle_archive=self.archive,
            scraper_feed=mock_scraper,
            db_manager=self.db,
        )
        self.assertIn(snap1["complete"], (1, True))

        # Check DB has stored it
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT complete, payload_json FROM session_snapshots WHERE session_date = '2026-10-23'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1)

        # Empty archive at separate path
        empty_archive = CandleArchive(os.path.join(self.tmp_dir, "empty_candles.db"))

        # Second build must return from database immutably
        snap2 = build_session_snapshot(
            session_date="2026-10-23",
            timeframe=5,
            candle_archive=empty_archive,
            scraper_feed=mock_scraper,
            db_manager=self.db,
        )
        self.assertEqual(snap1["session_date"], snap2["session_date"])
        self.assertEqual(snap1["stocks"]["RELIANCE"]["price"], snap2["stocks"]["RELIANCE"]["price"])


class TestFeedDownFallback(unittest.TestCase):
    """Verifies live feed down fallback with vs without stored snapshot."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        mig_path = Path(__file__).parent.parent / "migrations" / "004_session_snapshots.sql"
        if mig_path.exists():
            with open(mig_path, "r", encoding="utf-8") as f:
                with self.db.get_connection() as conn:
                    conn.executescript(f.read())

        self.archive_path = os.path.join(self.tmp_dir, "candles.db")
        self.archive = CandleArchive(self.archive_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_feed_down_with_stored_snapshot_returns_previous_session(self):
        from server import MarketScopeHandler
        fixtures = load_test_fixtures()
        snap = fixtures.get("snapshot", {})

        # Insert stored snapshot
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO session_snapshots (session_date, timeframe, payload_json, complete, data_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("2026-10-23", 5, json.dumps(snap), 1, "Exchange Live Feed (Yahoo Finance)", "2026-10-23T15:30:00Z"),
            )
            conn.commit()

        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db

        sent_data = {}
        status_code = [200]
        def mock_send(payload, status=200):
            sent_data.update(payload)
            status_code[0] = status
        handler._send_json = mock_send

        # Mock scraper throwing 503 or empty
        with patch("server.get_live_scraper_feed") as mock_feed:
            mock_feed.return_value.get_snapshot.side_effect = Exception("HTTP Error 503: Service Unavailable")
            with patch("server.get_session_state", return_value={"session_state": "LIVE", "session_date": "2026-10-23"}):
                handler._handle_market({"timeframe": ["5"]})

        self.assertEqual(status_code[0], 200)
        self.assertEqual(sent_data.get("data_mode"), "PREVIOUS_SESSION")
        self.assertEqual(sent_data.get("session_date"), "2026-10-23")

    def test_feed_down_without_stored_snapshot_returns_http_503(self):
        from server import MarketScopeHandler
        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db

        sent_data = {}
        status_code = [200]
        def mock_send(payload, status=200):
            sent_data.update(payload)
            status_code[0] = status
        handler._send_json = mock_send

        with patch("server.get_live_scraper_feed") as mock_feed:
            mock_feed.return_value.get_snapshot.side_effect = Exception("Scraper network error")
            with patch("server.get_session_state", return_value={"session_state": "LIVE", "session_date": "2026-10-26"}):
                with patch("server.build_session_snapshot", return_value=None):
                    handler._handle_market({"timeframe": ["5"]})

        self.assertEqual(status_code[0], 503)
        self.assertEqual(sent_data.get("error"), "feed_unavailable")
        self.assertNotIn("stocks", sent_data)


class TestSessionScorecard(unittest.TestCase):
    """Verifies signal scorecard for zero-signals vs logged signals."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        mig_file = Path(__file__).parent.parent / "migrations" / "004_session_snapshots.sql"
        if mig_file.exists():
            with open(mig_file, "r", encoding="utf-8") as f:
                with self.db.get_connection() as conn:
                    conn.executescript(f.read())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_zero_signals_returns_honest_note(self):
        scorecard = get_session_scorecard("2026-10-23", db_manager=self.db)
        self.assertEqual(scorecard["signals"], 0)
        self.assertEqual(scorecard["session_date"], "2026-10-23")
        self.assertEqual(scorecard["note"], "No signals were logged in this session")

    def test_scorecard_with_logged_signals(self):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            # 2 Wins, 1 Loss
            cur.execute("""
                INSERT INTO signals (created_at, symbol, timeframe, action, grade, score, conviction, price, stop_loss, target_1, rr, outcome)
                VALUES ('2026-10-23T10:00:00Z', 'RELIANCE', 5, 'BREAKOUT LONG', 'A', 75, 80, 2500.0, 2480.0, 2540.0, 2.0, 'T1')
            """)
            cur.execute("""
                INSERT INTO signals (created_at, symbol, timeframe, action, grade, score, conviction, price, stop_loss, target_1, rr, outcome)
                VALUES ('2026-10-23T11:00:00Z', 'TCS', 5, 'BREAKOUT SHORT', 'A', 78, 85, 3800.0, 3830.0, 3740.0, 2.0, 'T2')
            """)
            cur.execute("""
                INSERT INTO signals (created_at, symbol, timeframe, action, grade, score, conviction, price, stop_loss, target_1, rr, outcome)
                VALUES ('2026-10-23T12:00:00Z', 'HDFCBANK', 5, 'BREAKOUT LONG', 'B', 66, 70, 1600.0, 1585.0, 1630.0, 2.0, 'SL')
            """)
            conn.commit()

        scorecard = get_session_scorecard("2026-10-23", db_manager=self.db)
        self.assertEqual(scorecard["signals"], 3)
        self.assertEqual(scorecard["wins"], 2)
        self.assertEqual(scorecard["losses"], 1)
        self.assertEqual(scorecard["win_rate_pct"], 66.7)
        self.assertAlmostEqual(scorecard["avg_r"], 2.0, places=1)


class TestNoDemoGrep(unittest.TestCase):
    """Ensures zero hits for banned demo/synthetic terms in marketscope runtime."""

    def test_zero_demo_hits_outside_tests(self):
        pattern = re.compile(
            r'(Synthetic|SyntheticSessionGenerator|demo|Demo|demo_feed|get_demo_feed|demo_synthetic|DEMO_SYNTHETIC|mode=demo|demo-preview|DEMO DATA)',
            re.IGNORECASE,
        )
        root_dir = Path(__file__).parent.parent
        hits = []
        for root, dirs, files in os.walk(root_dir):
            if "__pycache__" in root or "tests" in root:
                continue
            for f in files:
                if f.endswith((".py", ".html", ".js", ".css", ".sql")):
                    p = os.path.join(root, f)
                    with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                        for idx, line in enumerate(fp, 1):
                            if pattern.search(line):
                                hits.append(f"{p}:{idx}: {line.strip()[:80]}")

        self.assertEqual(len(hits), 0, f"Found {len(hits)} banned terms in runtime: {hits}")


class TestSignalJournalLiveOnly(unittest.TestCase):
    """Signal journal must never insert signals from PREVIOUS or CLOSED_TODAY snapshots."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        mig_file = Path(__file__).parent.parent / "migrations" / "004_session_snapshots.sql"
        if mig_file.exists():
            with open(mig_file, "r", encoding="utf-8") as f:
                with self.db.get_connection() as conn:
                    conn.executescript(f.read())
        self.journal = SignalJournal(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_previous_session_never_creates_signals(self):
        snapshot = {
            "session_state": "PREVIOUS",
            "data_mode": "PREVIOUS_SESSION",
            "session_date": "2026-10-23",
            "stocks": {
                "RELIANCE": {
                    "status": "ok",
                    "price": 2500.0,
                    "score": 85,
                    "decision": {
                        "action": "BREAKOUT LONG",
                        "grade": "A",
                        "conviction_pct": 85,
                        "stop_loss": 2480.0,
                        "target_1": 2540.0,
                        "risk_reward_ratio": 2.0,
                    },
                    "metrics": {"rvol": 1.8, "rsi": 62.0, "vwap": 2490.0},
                }
            },
        }
        ids = self.journal.log_snapshot_signals(snapshot, timeframe=5)
        self.assertEqual(ids, [])

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM signals")
            count = cur.fetchone()[0]
            self.assertEqual(count, 0)



class TestCandleFilteringAndCorporateActions(unittest.TestCase):
    """Tests candle filtering for pre-2024 data and corporate action gap exclusion."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        self.archive_path = os.path.join(self.tmp_dir, "candles.db")
        self.archive = CandleArchive(self.archive_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_pre_2024_candles_excluded(self):
        # Insert candle from 2023 (< 1704067200)
        pre_2024_candle = [{"t": 1672531200, "o": 100.0, "h": 105.0, "l": 95.0, "c": 102.0, "v": 1000, "closed": True}]
        self.archive.archive_closed_candles("TCS", 5, pre_2024_candle)
        from unittest.mock import MagicMock
        mock_scraper = MagicMock()
        mock_scraper.scrape_stock_candles.return_value = {"candles": []}
        snapshot = build_session_snapshot(
            session_date="2023-01-01",
            timeframe=5,
            candle_archive=self.archive,
            scraper_feed=mock_scraper,
            db_manager=self.db,
        )
        self.assertNotIn("TCS", snapshot.get("stocks", {}))

    def test_corporate_action_overnight_gap_excluded(self):
        # Seed prior day candle closing at 1000
        # 2026-10-22 15:25 IST
        prior_ts = 1792662900 # ~2026-10-22
        self.archive.archive_closed_candles("TCS", 5, [
            {"t": prior_ts, "o": 995.0, "h": 1005.0, "l": 990.0, "c": 1000.0, "v": 5000, "closed": True}
        ])

        # Next day open at 800 (20% gap down > 15%)
        # 2026-10-23 09:15 IST
        day_ts = 1792727100
        mock_candles = [
            {"t": day_ts + i * 300, "o": 800.0, "h": 810.0, "l": 795.0, "c": 805.0, "v": 2000, "closed": True}
            for i in range(70)
        ]
        self.archive.archive_closed_candles("TCS", 5, mock_candles)

        # Snapshot for 2026-10-23 should exclude TCS due to corporate action gap
        snapshot = build_session_snapshot(
            session_date="2026-10-23",
            timeframe=5,
            candle_archive=self.archive,
            db_manager=self.db
        )
        self.assertNotIn("TCS", snapshot.get("stocks", {}))


if __name__ == "__main__":
    unittest.main()

