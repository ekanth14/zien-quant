"""
Unit & Integration Tests for IPO Service, Pre-Market Sync, and REST Endpoints.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "marketscope"))

from marketscope.ipo_service import IPOService, get_ipo_service
from marketscope.server import MarketScopeHandler


class TestIPOService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test_marketscope.db"
        self.master_path = Path(self.temp_dir.name) / "test_stock_master.db"

        # Create mock stock_master.db
        with sqlite3.connect(str(self.master_path)) as conn:
            conn.execute("""
                CREATE TABLE stocks (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    exchange TEXT,
                    series TEXT,
                    isin TEXT,
                    sector TEXT,
                    listing_date TEXT
                )
            """)
            conn.execute("""
                INSERT INTO stocks (symbol, name, exchange, series, isin, sector, listing_date)
                VALUES ('BONFIG', 'Bonfiglioli Transmissions Limited', 'NSE', 'EQ', 'INEBONFIG01', 'Industrial Engineering', '23-OCT-2026')
            """)
            conn.execute("""
                INSERT INTO stocks (symbol, name, exchange, series, isin, sector, listing_date)
                VALUES ('INFY', 'Infosys Limited', 'NSE', 'EQ', 'INE009A01021', 'Information Technology', '08-FEB-1995')
            """)
            conn.commit()

        self.service = IPOService(db_path=self.db_path, master_path=self.master_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_init_creates_tables(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ipo_watchlist', 'ipo_sync_log')")
            tables = {r[0] for r in cur.fetchall()}
            self.assertIn("ipo_watchlist", tables)
            self.assertIn("ipo_sync_log", tables)

    def test_get_listings_all(self):
        res = self.service.get_listings(filter_mode="all")
        self.assertGreater(res["total"], 0)
        self.assertIn("items", res)
        symbols = [it["symbol"] for it in res["items"]]
        self.assertIn("BONFIG", symbols)
        self.assertIn("NSDL", symbols)
        self.assertIn("GROWW", symbols)

    def test_get_listings_upcoming(self):
        res = self.service.get_listings(filter_mode="upcoming")
        symbols = [it["symbol"] for it in res["items"]]
        self.assertIn("NSDL", symbols)
        self.assertIn("BOAT", symbols)
        self.assertNotIn("BONFIG", symbols)

    def test_get_listings_recent_2026(self):
        res = self.service.get_listings(filter_mode="recent_2026")
        symbols = [it["symbol"] for it in res["items"]]
        self.assertIn("BONFIG", symbols)

    def test_track_and_untrack_ipo(self):
        # 1. Track
        res_track = self.service.track_ipo(
            symbol="BONFIG",
            name="Bonfiglioli Transmissions Limited",
            listing_date="23-OCT-2026",
            notes="Breakout candidate on industrial demand",
            target_price=950.0,
            alert_enabled=True,
        )
        self.assertEqual(res_track["status"], "success")
        self.assertTrue(res_track["tracked"])

        # Check in tracked map
        tracked_map = self.service.get_tracked_map()
        self.assertIn("BONFIG", tracked_map)
        self.assertEqual(tracked_map["BONFIG"]["target_price"], 950.0)
        self.assertEqual(tracked_map["BONFIG"]["notes"], "Breakout candidate on industrial demand")

        # Check via get_listings(filter_mode='tracked')
        res_list = self.service.get_listings(filter_mode="tracked")
        self.assertEqual(res_list["total"], 1)
        self.assertEqual(res_list["items"][0]["symbol"], "BONFIG")
        self.assertTrue(res_list["items"][0]["is_tracked"])

        # 2. Untrack
        res_untrack = self.service.untrack_ipo("BONFIG")
        self.assertEqual(res_untrack["status"], "success")
        self.assertFalse(res_untrack["tracked"])

        res_list_after = self.service.get_listings(filter_mode="tracked")
        self.assertEqual(res_list_after["total"], 0)

    def test_sync_new_shares_pre_market(self):
        res = self.service.sync_new_shares_pre_market()
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["pre_market_ready"])
        self.assertIn("sync_time", res)

        # Check sync log
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT newly_added, sync_time FROM ipo_sync_log ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            self.assertIsNotNone(row)


class TestIPORestEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test_ep_marketscope.db"
        self.master_path = Path(self.temp_dir.name) / "test_ep_master.db"

        # Initialize test tables in test db
        self.service = IPOService(db_path=self.db_path, master_path=self.master_path)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _create_handler(self):
        handler = object.__new__(MarketScopeHandler)
        handler.db = MagicMock()
        handler.db.db_path = self.db_path
        sent_data = {}

        def mock_send(payload, status=200):
            sent_data["payload"] = payload
            sent_data["status"] = status

        handler._send_json = mock_send
        return handler, sent_data

    def test_handle_ipo_listings_endpoint(self):
        handler, sent = self._create_handler()
        handler._handle_ipo_listings({"filter": ["upcoming"], "q": [""], "sort": ["date_desc"]})
        self.assertEqual(sent["status"], 200)
        self.assertIn("items", sent["payload"])
        self.assertIn("total", sent["payload"])

    def test_handle_ipo_track_invalid_symbol(self):
        handler, sent = self._create_handler()
        handler._parse_json_body = MagicMock(return_value={"symbol": "INVALID$$$"})
        handler._handle_ipo_track()
        self.assertEqual(sent["status"], 400)
        self.assertEqual(sent["payload"]["error"], "invalid_symbol")

    def test_handle_ipo_track_success(self):
        handler, sent = self._create_handler()
        handler._parse_json_body = MagicMock(return_value={
            "symbol": "HYUNDAI",
            "name": "Hyundai Motor India",
            "listing_date": "22-OCT-2024",
            "target_price": 2100.0,
            "notes": "Automotive leader",
            "alert_enabled": True
        })
        handler._handle_ipo_track()
        self.assertEqual(sent["status"], 200)
        self.assertEqual(sent["payload"]["status"], "success")
        self.assertTrue(sent["payload"]["tracked"])

    def test_handle_ipo_untrack_success(self):
        handler, sent = self._create_handler()
        handler._parse_json_body = MagicMock(return_value={"symbol": "HYUNDAI"})
        handler._handle_ipo_untrack()
        self.assertEqual(sent["status"], 200)
        self.assertEqual(sent["payload"]["status"], "success")
        self.assertFalse(sent["payload"]["tracked"])

    def test_handle_ipo_sync(self):
        handler, sent = self._create_handler()
        handler._handle_ipo_sync()
        self.assertEqual(sent["status"], 200)
        self.assertTrue(sent["payload"]["pre_market_ready"])


if __name__ == "__main__":
    unittest.main()
