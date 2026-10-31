"""
Unit Tests for Laya AI Suite and Ollama Integration
"""

import json
import os
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from laya_ai import LayaAIEngine, get_laya_engine
from decision_engine import evaluate_ai_decision


class TestLayaAIEngine(unittest.TestCase):
    def setUp(self):
        self.engine = LayaAIEngine(ollama_host="http://127.0.0.1:11434", model="gemma4:12b", timeout_sec=2.0)

    def test_engine_initialization(self):
        self.assertEqual(self.engine.ollama_host, "http://127.0.0.1:11434")
        self.assertEqual(self.engine.model, "gemma4:12b")

    def test_analyze_stock_with_deterministic_fallback(self):
        # When Ollama is unavailable, deterministic rules engine produces transparent rules briefing
        stock_data = {
            "price": 2540.0,
            "change_pct": 1.8,
            "vwap": 2525.0,
            "rvol": 1.6,
            "metrics": {"score": 75, "signal": "BUY", "raw": {"rsi": 62.4}},
            "ai_decision": {
                "action": "BREAKOUT LONG",
                "conviction_pct": 88,
                "entry_low": 2535.0,
                "entry_high": 2545.0,
                "stop_loss": 2515.0,
                "target_1": 2575.0,
                "target_2": 2605.0,
                "risk_reward_ratio": 2.1,
                "factors": ["Above VWAP", "RVOL expanding", "Score 75"],
            },
        }

        with patch.object(self.engine, "_call_ollama", return_value=None):
            result = self.engine.analyze_stock("RELIANCE", stock_data, timeframe=5, force_refresh=True)
            self.assertEqual(result["symbol"], "RELIANCE")
            self.assertEqual(result["action"], "BREAKOUT LONG")
            self.assertEqual(result["conviction_pct"], 88)
            self.assertEqual(result["stop_loss"], 2515.0)
            self.assertEqual(result["target_1"], 2575.0)
            self.assertEqual(result["source"], "rules_engine")
            self.assertIn("Rules-engine summary (AI not used) (RELIANCE)", result["institutional_briefing"])

    def test_analyze_stock_with_llm_response(self):
        stock_data = {
            "price": 2540.0,
            "change_pct": 1.8,
            "vwap": 2525.0,
            "rvol": 1.6,
            "metrics": {"score": 75, "signal": "BUY", "raw": {"rsi": 62.4}},
            "ai_decision": {
                "action": "BREAKOUT LONG",
                "conviction_pct": 88,
                "entry_low": 2535.0,
                "entry_high": 2545.0,
                "stop_loss": 2515.0,
                "target_1": 2575.0,
                "target_2": 2605.0,
                "risk_reward_ratio": 2.1,
                "factors": ["Above VWAP", "Score 75"],
            },
        }

        mock_llm_text = "RELIANCE exhibits upward momentum above session VWAP with target at ₹2575."
        with patch.object(self.engine, "_call_ollama", return_value=mock_llm_text):
            result = self.engine.analyze_stock("RELIANCE", stock_data, timeframe=5, force_refresh=True)
            self.assertEqual(result["source"], "local_llm")
            self.assertEqual(result["institutional_briefing"], mock_llm_text)
            self.assertIn("gemma4:12b", result["model"])

    def test_analysis_caching(self):
        stock_data = {
            "price": 1000.0,
            "change_pct": 0.5,
            "vwap": 995.0,
            "rvol": 1.0,
            "metrics": {"score": 50, "signal": "NEUTRAL", "raw": {"rsi": 50.0}},
            "ai_decision": {"action": "RANGE BOUND / AVOID", "conviction_pct": 50, "stop_loss": 990.0, "target_1": 1020.0},
        }

        with patch.object(self.engine, "_call_ollama", return_value="First call analysis") as mock_call:
            res1 = self.engine.analyze_stock("TEST", stock_data, timeframe=5, force_refresh=True)
            self.assertEqual(mock_call.call_count, 1)

            # Second call without force_refresh should hit cache
            res2 = self.engine.analyze_stock("TEST", stock_data, timeframe=5, force_refresh=False)
            self.assertEqual(mock_call.call_count, 1)
            self.assertEqual(res1["institutional_briefing"], res2["institutional_briefing"])

    def test_get_market_pulse(self):
        snapshot = {
            "stocks": {
                "RELIANCE": {"change_pct": 2.5, "name": "Reliance", "sector": "Energy", "metrics": {"signal": "BUY"}},
                "TCS": {"change_pct": 1.2, "name": "Tata Consultancy", "sector": "Technology", "metrics": {"signal": "BUY"}},
                "INFY": {"change_pct": -1.5, "name": "Infosys", "sector": "Technology", "metrics": {"signal": "SELL"}},
                "HDFCBANK": {"change_pct": 0.8, "name": "HDFC Bank", "sector": "Financials", "metrics": {"signal": "BUY"}},
            }
        }

        with patch.object(self.engine, "_call_ollama", return_value=None):
            pulse = self.engine.get_market_pulse(snapshot, force_refresh=True)
            self.assertEqual(pulse["total_constituents"], 4)
            self.assertEqual(pulse["advancers"], 3)
            self.assertEqual(pulse["decliners"], 1)
            self.assertEqual(pulse["breadth_ratio"], 0.75)
            self.assertEqual(pulse["regime"], "STRONG BULLISH MOMENTUM")
            self.assertIn("RELIANCE", [g["symbol"] for g in pulse["top_gainers"]])
            self.assertIn("INFY", [l["symbol"] for l in pulse["top_losers"]])

    def test_chat_copilot(self):
        snapshot = {
            "stocks": {
                "RELIANCE": {
                    "price": 2540.0,
                    "change_pct": 1.8,
                    "vwap": 2525.0,
                    "ai_decision": {"action": "BREAKOUT LONG", "stop_loss": 2515.0, "target_1": 2575.0, "risk_reward_ratio": 2.1},
                }
            }
        }

        # 1. When LLM responds, returns real answer with telemetry context
        with patch.object(self.engine, "_call_ollama", return_value="The stop loss for RELIANCE is ₹2515 on BREAKOUT LONG."):
            reply = self.engine.chat_copilot("What is the stop loss for RELIANCE?", current_symbol="RELIANCE", snapshot_data=snapshot)
            self.assertIn("2515", reply["reply"])
            self.assertIn("BREAKOUT LONG", reply["reply"])
            self.assertEqual(reply["source"], "local_llm")

        # 2. When LLM fails/times out, fallback to website context
        with patch.object(self.engine, "_call_ollama", return_value=None):
            reply_err = self.engine.chat_copilot("What is the trend?", current_symbol="RELIANCE", snapshot_data=snapshot)
            self.assertEqual(reply_err.get("source"), "website_context_fallback")
            self.assertIn("reply", reply_err)


    def test_call_laya_decision(self):
        stock_data = {
            "price": 2540.0,
            "change_pct": 1.8,
            "vwap": 2525.0,
            "rvol": 1.6,
            "metrics": {"score": 75, "signal": "BUY", "raw": {"rsi": 62.4}},
        }
        mock_response_data = {
            "model": "laya",
            "answers": {
                "trade_bias": {
                    "type": "choice",
                    "choice": "bullish",
                    "probabilities": {"bullish": 0.75, "bearish": 0.15, "neutral": 0.10},
                    "confidence": 0.60,
                }
            }
        }
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.read.return_value = json.dumps(mock_response_data).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_cm

            res = self.engine._call_laya_decision("RELIANCE", stock_data)
            self.assertIsNotNone(res)
            self.assertEqual(res["model"], "laya")
            self.assertEqual(res["choice"], "bullish")
            self.assertEqual(res["confidence"], 0.60)
            self.assertEqual(res["probabilities"]["bullish"], 0.75)

    def test_check_status_includes_decision_model(self):
        mock_tags_data = {
            "models": [
                {"name": "gemma4:12b"},
                {"name": "laya:latest"},
                {"name": "laya-ai:latest"},
            ]
        }
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.read.return_value = json.dumps(mock_tags_data).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_cm

            status = self.engine.check_status()
            self.assertTrue(status["online"])
            self.assertEqual(status["decision_model"], "laya")
            self.assertTrue(status["decision_model_available"])
            self.assertTrue(status["model_available"])


class TestServerAIEndpoints(unittest.TestCase):
    def setUp(self):
        import tempfile, shutil, os
        from security import DatabaseManager
        try:
            from marketscope.tests.fixture_loader import load_test_fixtures
        except ImportError:
            try:
                from tests.fixture_loader import load_test_fixtures
            except ImportError:
                from fixture_loader import load_test_fixtures
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        mig_path = Path(__file__).parent.parent / "migrations" / "004_session_snapshots.sql"
        if mig_path.exists():
            with open(mig_path, "r", encoding="utf-8") as f:
                with self.db.get_connection() as conn:
                    conn.executescript(f.read())
        fixtures = load_test_fixtures()
        snap = fixtures.get("snapshot")
        if snap:
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO session_snapshots (session_date, timeframe, payload_json, complete, data_source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-10-23", 5, json.dumps(snap), 1, "TEST FIXTURE", "2026-10-23T15:30:00Z"),
                )
                conn.commit()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_bootstrap_includes_laya_info(self):
        from server import MarketScopeHandler
        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db
        
        sent_data = {}
        def mock_send_json(payload, status=200):
            sent_data.update(payload)
        
        handler._send_json = mock_send_json
        handler._handle_bootstrap()
        
        self.assertIn("laya_ai", sent_data)
        self.assertEqual(sent_data["laya_ai"]["name"], "Laya AI Intelligence Suite")
        expected_model = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
        self.assertIn(expected_model, sent_data["laya_ai"]["model"])

    def test_ai_status_endpoint(self):
        from server import MarketScopeHandler
        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db
        
        sent_data = {}
        handler._send_json = lambda payload, status=200: sent_data.update(payload)
        handler._handle_ai_status()
        self.assertIn("target_model", sent_data)
        # Local model selection is configurable; the endpoint reports that
        # configured target rather than assuming one installed model name.
        self.assertEqual(sent_data["target_model"], get_laya_engine().model)

    def test_ai_pulse_endpoint(self):
        from server import MarketScopeHandler
        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db
        
        sent_data = {}
        handler._send_json = lambda payload, status=200: sent_data.update(payload)
        handler._handle_ai_pulse({"refresh": ["0"]})
        self.assertIn("regime", sent_data)
        self.assertIn("breadth_ratio", sent_data)
        self.assertIn("top_gainers", sent_data)

    def test_ai_analyze_endpoint_success(self):
        from server import MarketScopeHandler
        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db
        handler._parse_json_body = MagicMock(return_value={"symbol": "RELIANCE", "timeframe": 5})
        
        sent_data = {}
        handler._send_json = lambda payload, status=200: sent_data.update(payload)
        handler._handle_ai_analyze()
        self.assertEqual(sent_data.get("symbol"), "RELIANCE")
        self.assertIn("action", sent_data)
        self.assertIn("institutional_briefing", sent_data)

    def test_ai_analyze_endpoint_not_found(self):
        from server import MarketScopeHandler
        handler = object.__new__(MarketScopeHandler)
        handler.db = self.db
        handler._parse_json_body = MagicMock(return_value={"symbol": "NONEXISTENT", "timeframe": 5})
        
        sent_data = {}
        status_code = [200]
        def mock_send(payload, status=200):
            sent_data.update(payload)
            status_code[0] = status
        handler._send_json = mock_send
        handler._handle_ai_analyze()
        self.assertEqual(status_code[0], 404)
        self.assertIn("error", sent_data)

    def test_ai_chat_endpoint(self):
        from server import MarketScopeHandler
        from laya_ai import get_laya_engine
        handler = object.__new__(MarketScopeHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.db = self.db
        handler._get_current_session = lambda: {"user_id": 1, "role": "member"}
        handler._parse_json_body = MagicMock(return_value={"message": "What is the trend?", "symbol": "TCS"})
        
        sent_data = {}
        handler._send_json = lambda payload, status=200: sent_data.update(payload)
        with patch.object(get_laya_engine(), "chat_copilot", return_value={"reply": "TCS is in consolidation.", "symbol": "TCS"}):
            handler._handle_ai_chat()
        self.assertIn("reply", sent_data)
        self.assertEqual(sent_data.get("symbol"), "TCS")


if __name__ == "__main__":
    unittest.main()

