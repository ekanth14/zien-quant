"""
Comprehensive End-to-End Live Integration Test.
Boots the full ThreadingHTTPServer on an ephemeral port and tests all HTTP endpoints,
payloads, daily ranking, best/worst stock, tiers summary, and Laya AI responses.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from marketscope.server import MarketScopeHandler, DEFAULT_DB_PATH
from marketscope.security import DatabaseManager


class TestServerLiveE2E(unittest.TestCase):
    server: ThreadingHTTPServer = None
    server_thread: threading.Thread = None
    base_url: str = ""

    @classmethod
    def setUpClass(cls):
        # Bind to an ephemeral port on 127.0.0.1
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        cls.server = ThreadingHTTPServer(("127.0.0.1", port), MarketScopeHandler)
        cls.server.db_manager = DatabaseManager(DEFAULT_DB_PATH)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{port}"
        time.sleep(0.15)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()
        if cls.server_thread:
            cls.server_thread.join(timeout=2.0)

    def _get(self, path: str):
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "E2ETest"})
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = resp.read()
            return resp.status, resp.headers, data

    def _post(self, path: str, payload: dict):
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "E2ETest"},
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = resp.read()
            return resp.status, resp.headers, data

    def test_01_root_workspace(self):
        status, headers, data = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Zien Quant", data)
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_02_flow_page(self):
        status, headers, data = self._get("/flow")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_03_static_assets(self):
        status_js, _, data_js = self._get("/static/workspace.js")
        self.assertEqual(status_js, 200)
        self.assertIn(b"renderDailyRanking", data_js)

        status_css, _, data_css = self._get("/static/workspace.css")
        self.assertEqual(status_css, 200)
        self.assertIn(b"daily-rank-card", data_css)

    def test_04_api_health(self):
        status, _, data = self._get("/api/health")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertEqual(res.get("status"), "healthy")
        self.assertIn("uptime_seconds", res)
        self.assertIn("version", res)

    def test_05_api_schedule(self):
        status, _, data = self._get("/api/market/schedule")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertIn("is_open", res)
        self.assertIn("state", res)
        self.assertIn("status_badge", res)

    def test_06_api_bootstrap(self):
        status, _, data = self._get("/api/bootstrap")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertIn("config", res)
        self.assertIn("catalogue", res)
        self.assertIn("session", res)

    def test_07_api_stocks_database(self):
        status, _, data = self._get("/api/stocks/database?q=TCS")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertIn("stocks", res)
        self.assertTrue(any(s.get("symbol") == "TCS" for s in res["stocks"]))

    def test_08_api_market_with_daily_quant_and_tiers(self):
        status, _, data = self._get("/api/market?session=previous&timeframe=5")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertIn("session_date", res)
        self.assertIn("stocks", res)
        self.assertIn("daily_ranking", res)
        self.assertIn("tiers_summary", res)
        ts = res["tiers_summary"]
        self.assertIn("bullish_count", ts)
        self.assertIn("bearish_count", ts)
        self.assertIn("normal_count", ts)

        # Check Best and Worst stock are surfaced
        self.assertIn("best_stock", res)
        self.assertIn("worst_stock", res)
        self.assertIn("spotlight", res)

    def test_09_api_flow(self):
        status, _, data = self._get("/api/flow")
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertIn("bars", res)
        self.assertIn("stocks", res)
        self.assertIn("sectors", res)

    def test_10_api_ai_chat_copilot(self):
        # Create or fetch an owner session for authenticated chat
        conn = self.server.db_manager.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1")
            row = cur.fetchone()
            if row:
                user_id = row[0]
            else:
                user_id = self.server.db_manager.create_user("testowner@example.com", "SecretPass123!", role="owner")
        finally:
            conn.close()

        raw_token, _ = self.server.db_manager.create_session(user_id)

        url = f"{self.base_url}/api/ai/chat"
        body = json.dumps({"message": "Explain the daily ranking formula"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "E2ETest",
                "Cookie": f"ms_session={raw_token}",
            },
        )
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = resp.read()
            self.assertEqual(resp.status, 200)
            res = json.loads(data.decode("utf-8"))
            self.assertIn("reply", res)
            self.assertIsInstance(res["reply"], str)
            self.assertGreater(len(res["reply"]), 10)


if __name__ == "__main__":
    unittest.main()

