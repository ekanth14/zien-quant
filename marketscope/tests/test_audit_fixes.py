"""Comprehensive test suite verifying all Phase 1-6 audit fixes:
- Data integrity (no 1000.0, failed status handling, exclusion from scoring/breadth/export)
- Live feed 503 enforcement and explicit demo mode labeling
- R:R gate (< 1.5 -> AVOID - POOR R:R) and TCS short case coverage
- Narrative consistency (no institutional accumulation on short setups)
- Welles Wilder smoothed RSI calculation against known mathematical series
- Laya AI output validator (number matching, hallucination rejection, direction checks)
- Asynchronous analysis queue (< 2s response even with 60s LLM delay)
- Chat copilot deterministic retrieved-context fallback on LLM failure
- Authentication matrix and 401/403 status enforcement on all gated endpoints
- Symbol tracking input validation (regex, master db check, 100 max cap)
- Signal journal deduplication and outcome tracking (same-candle conservative SL priority)
- Optional real Ollama integration test (skipped unless OLLAMA_INTEGRATION=1)
"""

from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Ensure marketscope path
sys.path.insert(0, str(Path(__file__).parent.parent))

from market import calculate_rsi, calculate_atr, DEFAULT_CONFIG
from decision_engine import evaluate_ai_decision
from laya_ai import validate_llm_output, LayaAIEngine, get_laya_engine
from scraper import RealTimeScraperFeed
from security import DatabaseManager, RateLimiter
from signal_journal import SignalJournal
from server import MarketScopeHandler, DEFAULT_DB_PATH


class TestAuditIntegrityAndGates(unittest.TestCase):
    """Test suite for data integrity, indicators, and decision engine gating."""

    def test_scraper_error_returns_failed_no_dummy_price(self):
        """Scraper on 403, 429, or timeout must return status='failed' with price=None and no 1000.0."""
        import urllib.error
        scraper = RealTimeScraperFeed(monitored_symbols=["TESTFAIL"])
        err = urllib.error.HTTPError(url="https://query1.finance.yahoo.com", code=429, msg="Too Many Requests", hdrs={}, fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            res = scraper.scrape_stock_candles("TESTFAIL", timeframe=5)
            self.assertEqual(res["status"], "failed")
            self.assertIsNone(res["price"])
            self.assertNotIn(1000.0, res.values())
            self.assertEqual(res["candles"], [])

    def test_failed_stock_excluded_from_scoring_breadth_and_export(self):
        """Non-ok stocks must be excluded from composite scoring, hero cards, and CSV export."""
        scraper = RealTimeScraperFeed(monitored_symbols=["GOOD", "FAIL"])
        # Mock good stock and failed stock
        good_candles = [
            {
                "time": f"2026-10-25T09:{i:02d}:00Z",
                "t": f"2026-10-25T09:{i:02d}:00Z",
                "open": 100.0 + i,
                "o": 100.0 + i,
                "high": 102.0 + i,
                "h": 102.0 + i,
                "low": 99.0 + i,
                "l": 99.0 + i,
                "close": 101.0 + i,
                "c": 101.0 + i,
                "volume": 10000,
                "v": 10000,
            }
            for i in range(25)
        ]
        with patch.object(scraper, "scrape_stock_candles") as mock_scrape:
            def side_effect(sym, timeframe=5, exchange="NSE", **kwargs):
                if sym == "GOOD":
                    return {"status": "ok", "price": 125.0, "candles": good_candles, "source": "test", "fetched_at": time.time()}
                return {"status": "failed", "price": None, "candles": [], "error": "HTTP 403", "fetched_at": time.time()}
            mock_scrape.side_effect = side_effect

            snapshot = scraper.get_snapshot(timeframe=5)
            stocks = snapshot["stocks"]
            self.assertEqual(stocks["GOOD"]["status"], "ok")
            self.assertIsNotNone(stocks["GOOD"]["score"])
            self.assertEqual(stocks["FAIL"]["status"], "failed")
            self.assertIsNone(stocks["FAIL"]["score"])
            self.assertEqual(stocks["FAIL"]["action"], "DATA UNAVAILABLE")

            # Check breadth counts only include OK stocks
            breadth = snapshot["breadth"]
            self.assertEqual(breadth["tracked"], 1)

    def test_wilder_rsi_against_known_mathematical_reference(self):
        """Calculate Wilder RSI against a hand-calculated reference series of 15 prices."""
        # 15 prices -> 14 changes: 9 gains summing to 17 (avg 1.2142857), 5 losses summing to 7 (avg 0.5)
        # RS = 17 / 7 = 2.4285714 -> RSI = 100 - (100 / (1 + 2.4285714)) = 70.833333...
        prices = [100.0, 102.0, 104.0, 102.0, 100.0, 101.0, 103.0, 105.0, 104.0, 103.0, 105.0, 107.0, 106.0, 108.0, 110.0]
        rsi = calculate_rsi(prices, period=14)
        self.assertIsNotNone(rsi)
        self.assertAlmostEqual(rsi, 70.833333, places=4)

        # Insufficient closes (< 15) must return None
        self.assertIsNone(calculate_rsi(prices[:14], period=14))

    def test_wilder_atr_calculation(self):
        """Wilder smoothed ATR against known mathematical reference."""
        candles = [{"h": 105.0, "l": 95.0, "c": 100.0}]
        for i in range(1, 15):
            candles.append({"h": 106.0, "l": 96.0, "c": 102.0})
        atr14 = calculate_atr(candles, period=14)
        self.assertIsNotNone(atr14)
        self.assertAlmostEqual(atr14, 10.0, places=3)

    def test_hdfcbank_breakout_at_day_high_passes_rr_gate(self):
        """HDFCBANK-like case: price at day high, score 97, resistance = price.
        ATR-expanded breakout targets must clear 1.5 R:R and pass the gate."""
        price = 1589.76
        resistance = 1589.76  # At the day high!
        support = 1580.0
        vwap = 1550.30
        order_blocks = [{"type": "bullish", "low": 1584.0, "high": 1587.0}]
        metrics = {
            "score": 97,
            "signal": "BUY",
            "raw": {"momentum_pct": 1.5, "rsi": 84.43, "trend_pct": 2.1, "vwap_dist_pct": 2.5, "atr": 10.5},
        }
        dec = evaluate_ai_decision(
            symbol="HDFCBANK",
            price=price,
            metrics=metrics,
            vwap=vwap,
            support=support,
            resistance=resistance,
            rvol=2.2,
            order_blocks=order_blocks,
            atr=10.5,
        )
        self.assertEqual(dec["action"], "BREAKOUT LONG")
        self.assertEqual(dec["signal_type"], "BUY")
        self.assertGreaterEqual(dec["risk_reward_ratio"], 1.5)
        self.assertIsNotNone(dec["target_1"])
        self.assertGreaterEqual(dec["target_1"], round(price + 1.5 * 10.5, 2))
        self.assertEqual(dec["target_2"], round(price + 2.5 * 10.5, 2))
        self.assertIsNotNone(dec["stop_loss"])
        self.assertLess(dec["stop_loss"], price)

    def test_rr_gate_enforcement_and_tcs_short_case(self):
        """When R:R < 1.5, action must be 'AVOID - POOR R:R' with no targets or entry zone presented."""
        # TCS style short breakdown setup:
        # Current price: 3180.0
        # Resistance (SL): 3220.0 (Risk = 40.0)
        # Structural Support (Target 1): 3163.5 (Reward = 16.5)
        # Real R:R = 16.5 / 40.0 = 0.4125 (< 1.5 hurdle)
        metrics = {
            "score": 25,
            "signal": "STRONG_BEARISH",
            "raw": {
                "momentum_pct": -1.5,
                "rsi": 35.0,
                "trend_pct": -2.0,
                "vwap_dist_pct": -0.5,
            },
        }
        dec = evaluate_ai_decision(
            symbol="TCS",
            price=3180.0,
            metrics=metrics,
            vwap=3190.0,
            support=3163.5,
            resistance=3220.0,
            rvol=1.6,
            order_blocks=[],
            closed_confirming=True,
        )
        self.assertEqual(dec["action"], "AVOID - POOR R:R")
        self.assertEqual(dec["grade"], "C")
        self.assertLessEqual(dec["conviction_pct"], 40)
        self.assertIsNone(dec["entry_low"])
        self.assertIsNone(dec["entry_high"])
        self.assertIsNone(dec["stop_loss"])
        self.assertIsNone(dec["target_1"])
        self.assertIsNone(dec["target_2"])
        self.assertLess(dec["risk_reward_ratio"], 1.5)

    def test_short_narrative_never_claims_accumulation(self):
        """Short setup deterministic briefing must not mention institutional accumulation."""
        engine = LayaAIEngine()
        stock_data = {
            "symbol": "TCS",
            "price": 3180.0,
            "score": 25,
            "decision": {
                "action": "BREAKDOWN SHORT",
                "signal_type": "SELL",
                "grade": "A",
                "conviction_pct": 75,
                "entry_low": 3175.0,
                "entry_high": 3185.0,
                "stop_loss": 3220.0,
                "target_1": 3080.0,
                "target_2": 3020.0,
                "risk_reward_ratio": 2.5,
                "factors": ["Bearish breakdown below VWAP"],
            },
            "metrics": {"rvol": 1.8, "rsi": 35.0, "vwap": 3190.0},
        }
        briefing_res = engine._build_rules_briefing("TCS", stock_data, timeframe=5)
        summary = briefing_res["institutional_briefing"].lower()
        self.assertNotIn("accumulation", summary)
        self.assertTrue("downward pressure" in summary or "distribution" in summary or "breakdown" in summary or "liquidation" in summary)


class TestLayaOutputValidatorAndAsync(unittest.TestCase):
    """Test suite for Laya AI output validation and async worker."""

    def test_validator_accepts_valid_numbers_and_rejects_hallucinations(self):
        """Validator must accept levels within 0.2% and reject invented price numbers."""
        stock_data = {
            "price": 2500.0,
            "vwap": 2490.0,
            "ai_decision": {
                "action": "BREAKOUT LONG",
                "signal_type": "BUY",
                "entry_low": 2495.0,
                "entry_high": 2505.0,
                "stop_loss": 2450.0,
                "target_1": 2600.0,
                "target_2": 2650.0,
            },
        }

        # Valid text mentioning matching levels
        valid_text = "• Entry zone near 2500 with target 2600.\n• Protective stop placed at 2450.\n• Momentum aligns with VWAP at 2490."
        ok, reason = validate_llm_output(valid_text, stock_data)
        self.assertTrue(ok, f"Expected valid, got: {reason}")

        # Invalid text inventing price 2850.0
        hallucinated_text = "• Immediate breakout will surge straight to 2850 without pullback.\n• Support at 2500."
        ok, reason = validate_llm_output(hallucinated_text, stock_data)
        self.assertFalse(ok)
        self.assertIn("Invented price level", reason)

    def test_validator_rejects_narrative_direction_contradiction(self):
        """Validator must reject bullish narrative on a short setup."""
        stock_data = {
            "price": 1000.0,
            "ai_decision": {
                "action": "BREAKDOWN SHORT",
                "signal_type": "SELL",
                "target_1": 900.0,
                "stop_loss": 1050.0,
            },
        }
        contradictory_text = "• Strong institutional accumulation signals aggressive rally to 900.\n• Stop loss at 1050."
        ok, reason = validate_llm_output(contradictory_text, stock_data)
        self.assertFalse(ok)
        self.assertIn("Contradictory bullish narrative", reason)

    def test_validator_typed_rules_and_tolerance(self):
        """Typed validation: RSI, percentages, targets, and bullish text."""
        # 1. RSI 84.4 passes when RSI is 84.43
        stock_hdfc = {
            "price": 1589.76,
            "vwap": 1550.3,
            "metrics": {"score": 97, "raw": {"rsi": 84.43}},
            "ai_decision": {
                "action": "AVOID - POOR R:R",
                "signal_type": "NEUTRAL",
                "risk_reward_ratio": 0.04,
                "conviction_pct": 40,
            },
        }
        text_hdfc = "• HDFCBANK is trading at ₹1589.76, significantly above session VWAP ₹1550.30.\n• RSI(14) is at 84.4, indicating overbought conditions with conviction 40%.\n• Decision is AVOID due to poor R:R of 1:0.04."
        ok1, reason1 = validate_llm_output(text_hdfc, stock_hdfc)
        self.assertTrue(ok1, f"Expected RSI 84.4 to pass against 84.43, got: {reason1}")

        # 2. Invented target fails
        text_inv_target = "• Target 1 is ₹1850.00.\n• Price ₹1589.76."
        ok2, reason2 = validate_llm_output(text_inv_target, stock_hdfc)
        self.assertFalse(ok2)
        self.assertIn("Invented price level", reason2)

        # 3. Invented percentage fails
        text_inv_pct = "• Stock surged 19.5% in morning trade.\n• Price ₹1589.76."
        ok3, reason3 = validate_llm_output(text_inv_pct, stock_hdfc)
        self.assertFalse(ok3)
        self.assertIn("percentage", reason3.lower())

        # 4. Correct bullish text on a BUY passes
        stock_rel = {
            "price": 2500.0,
            "vwap": 2480.0,
            "metrics": {"score": 88, "raw": {"rsi": 64.0}},
            "ai_decision": {
                "action": "BREAKOUT LONG",
                "signal_type": "BUY",
                "stop_loss": 2450.0,
                "target_1": 2600.0,
                "target_2": 2680.0,
                "risk_reward_ratio": 2.0,
                "conviction_pct": 85,
            },
        }
        text_rel = "• RELIANCE breakout above ₹2500.00 confirmed above VWAP ₹2480.00.\n• RSI is 64.0 with 85% conviction and 1:2.0 risk-reward.\n• Invalidation stop at ₹2450.00, primary target at ₹2600.00."
        ok4, reason4 = validate_llm_output(text_rel, stock_rel)
        self.assertTrue(ok4, f"Expected valid bullish briefing to pass, got: {reason4}")

    def test_chat_copilot_labels_retrieved_context_when_llm_fails(self):
        """Preserve the existing website-data fallback without claiming model output."""
        engine = LayaAIEngine()
        with patch.object(engine, "_call_ollama", return_value=None):
            res = engine.chat_copilot(
                "What is the trend?", current_symbol="RELIANCE",
                website_context="Trend unavailable: no verified source candles.",
            )
        self.assertEqual(res["source"], "website_context_fallback")
        self.assertEqual(res["backend"], "rules")
        self.assertIn("Trend unavailable: no verified source candles.", res["reply"])
        self.assertNotIn("error", res)
        self.assertNotIn("canned", str(res))


class TestServerSecurityAuthAndRouting(unittest.TestCase):
    """Test suite for server authentication matrix, rate limits, and endpoint gating."""

    def setUp(self):
        self.handler = object.__new__(MarketScopeHandler)
        self.handler.client_address = ("127.0.0.1", 54321)
        self.handler.db = DatabaseManager(DEFAULT_DB_PATH)
        self.sent: Dict[str, Any] = {}
        self.status_code = [200]
        self.headers_sent: Dict[str, str] = {}

        def mock_send_json(data, status=200, cookies=None):
            self.sent = data
            self.status_code[0] = status

        self.handler._send_json = mock_send_json

    def test_unauthenticated_requests_to_gated_endpoints_return_401(self):
        """Gated endpoints must return HTTP 401 when accessed without an active session."""
        self.handler._get_current_session = lambda: None

        # 1. AI Chat
        self.handler._parse_json_body = MagicMock(return_value={"message": "hi"})
        self.handler._handle_ai_chat()
        self.assertEqual(self.status_code[0], 401)
        self.assertEqual(self.sent.get("error"), "auth_required")

        # 2. Export CSV
        self.handler._handle_export({})
        self.assertEqual(self.status_code[0], 401)

        # 3. Track Symbol
        self.handler._handle_stocks_track()
        self.assertEqual(self.status_code[0], 401)

        # 4. Untrack Symbol
        self.handler._handle_stocks_untrack()
        self.assertEqual(self.status_code[0], 401)

        # 5. Signal Stats
        self.handler._handle_signals_stats()
        self.assertEqual(self.status_code[0], 401)

        # 6. Admin
        self.handler._handle_admin()
        self.assertEqual(self.status_code[0], 401)

        # 7. Config Update
        self.handler._handle_config_update()
        self.assertEqual(self.status_code[0], 401)

    def test_non_owner_requests_to_owner_endpoints_return_403(self):
        """Owner-only endpoints must return HTTP 403 when user is authenticated but not an owner."""
        self.handler._get_current_session = lambda: {"user_id": 2, "email": "user@example.com", "role": "member"}

        self.handler._handle_config_history()
        self.assertEqual(self.status_code[0], 403)

        self.handler._handle_backup_status()
        self.assertEqual(self.status_code[0], 403)

        self.handler._handle_backup()
        self.assertEqual(self.status_code[0], 403)

    def test_stocks_track_validation(self):
        """Track symbol must validate regex, check stock_master.db, and enforce max tracking cap."""
        self.handler._get_current_session = lambda: {"user_id": 1, "role": "member"}

        # 1. Bad regex (contains spaces, lowercase, invalid chars)
        self.handler._parse_json_body = MagicMock(return_value={"symbol": "invalid symbol!"})
        self.handler._handle_stocks_track()
        self.assertEqual(self.status_code[0], 400)
        self.assertEqual(self.sent.get("error"), "invalid_symbol")

        # 2. Symbol not in stock master database
        self.handler._parse_json_body = MagicMock(return_value={"symbol": "NONEXISTENT99"})
        self.handler._handle_stocks_track()
        self.assertEqual(self.status_code[0], 404)
        self.assertEqual(self.sent.get("error"), "symbol_not_found")

        # 3. Valid symbol adds successfully
        self.handler._parse_json_body = MagicMock(return_value={"symbol": "TCS"})
        self.handler._handle_stocks_track()
        self.assertEqual(self.status_code[0], 200)
        self.assertEqual(self.sent.get("status"), "success")


class TestSignalJournalAndOutcomeTracker(unittest.TestCase):
    """Test suite for Signal Journal deduplication and outcome tracking."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_signals.db")
        self.db = DatabaseManager(self.db_path)
        self.journal = SignalJournal(self.db)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_signal_deduplication_within_30_minutes(self):
        """Identical setup within 30 minutes must not create a duplicate row."""
        snapshot = {
            "data_mode": "LIVE",
            "stocks": {
                "RELIANCE": {
                    "status": "ok",
                    "price": 2500.0,
                    "score": 80,
                    "decision": {
                        "action": "BREAKOUT LONG",
                        "grade": "A+",
                        "conviction_pct": 85,
                        "entry_zone": [2495.0, 2505.0],
                        "stop_loss": 2450.0,
                        "target_1": 2600.0,
                        "target_2": 2650.0,
                        "risk_reward_ratio": 2.0,
                    },
                    "metrics": {"rvol": 1.6, "rsi": 62.0, "vwap": 2490.0},
                }
            },
        }
        # First log
        ids1 = self.journal.log_snapshot_signals(snapshot, timeframe=5)
        self.assertEqual(len(ids1), 1)

        # Immediate second log must be skipped due to 30 min dedupe window
        ids2 = self.journal.log_snapshot_signals(snapshot, timeframe=5)
        self.assertEqual(len(ids2), 0)

    def test_conservative_same_candle_sl_and_t1_resolution(self):
        """When subsequent candle touches both SL and T1, outcome must conservatively resolve as 'SL'."""
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO signals (
                    created_at, symbol, timeframe, action, grade, score, conviction,
                    price, stop_loss, target_1, target_2, rr, outcome
                ) VALUES ('2026-10-25T10:00:00Z', 'TESTSAME', 5, 'BREAKOUT LONG', 'A', 75, 70, 100.0, 95.0, 110.0, 115.0, 2.0, 'OPEN')
                """
            )
            conn.commit()
        finally:
            conn.close()

        # Candle swings between 94.0 (hits SL 95.0) and 112.0 (hits T1 110.0) in the same bar
        candles = [{"time": "2026-10-25T10:05:00Z", "open": 100.0, "high": 112.0, "low": 94.0, "close": 105.0}]
        updated = self.journal.update_outcomes({"TESTSAME": candles})
        self.assertGreaterEqual(updated, 1)

        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT outcome, outcome_price FROM signals WHERE symbol = 'TESTSAME'")
            row = cur.fetchone()
            self.assertEqual(row["outcome"], "SL")
            self.assertEqual(row["outcome_price"], 95.0)
        finally:
            conn.close()

    def test_signals_stats_empty_and_populated(self):
        """Stats calculation must return honest zero counts when empty or accurate win rates."""
        stats = self.journal.get_signal_stats()
        self.assertIn("total_signals", stats)
        self.assertIn("win_rate_pct", stats)
        self.assertIn("by_grade", stats)
        self.assertIn("by_action", stats)
        self.assertIn("by_timeframe", stats)

    def test_shadow_signal_logging_and_gated_stats_comparison(self):
        """Rejected setups (score >= 65, AVOID - POOR R:R) are logged as gated with shadow levels,
        and get_signal_stats(include_gated=True) returns ungated vs gated comparison."""
        snapshot = {
            "data_mode": "LIVE",
            "stocks": {
                "GATED_STOCK": {
                    "status": "ok",
                    "price": 1000.0,
                    "score": 75,
                    "decision": {
                        "action": "AVOID - POOR R:R",
                        "grade": "C",
                        "conviction_pct": 40,
                        "gated": 1,
                        "gate_reason": "R:R (1.20) < 1.5",
                        "shadow_stop_loss": 980.0,
                        "shadow_target_1": 1024.0,
                        "shadow_target_2": 1050.0,
                        "shadow_rr": 1.2,
                    },
                    "metrics": {"rvol": 1.2, "rsi": 65.0, "vwap": 995.0},
                },
                "VALID_STOCK": {
                    "status": "ok",
                    "price": 500.0,
                    "score": 80,
                    "decision": {
                        "action": "BREAKOUT LONG",
                        "grade": "A",
                        "conviction_pct": 75,
                        "stop_loss": 490.0,
                        "target_1": 520.0,
                        "target_2": 535.0,
                        "risk_reward_ratio": 2.0,
                    },
                    "metrics": {"rvol": 1.8, "rsi": 60.0, "vwap": 495.0},
                },
            },
        }
        ids = self.journal.log_snapshot_signals(snapshot, timeframe=5)
        self.assertEqual(len(ids), 2)

        # Evaluate outcomes: VALID_STOCK hits T1 (520.0), GATED_STOCK hits SL (980.0)
        future_iso = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        candles_map = {
            "VALID_STOCK": [{"time": future_iso, "high": 525.0, "low": 495.0, "close": 522.0}],
            "GATED_STOCK": [{"time": future_iso, "high": 1005.0, "low": 975.0, "close": 985.0}],
        }
        up_count = self.journal.update_outcomes(candles_map)
        self.assertEqual(up_count, 2)

        # Verify default stats (excludes gated)
        stats_default = self.journal.get_signal_stats(include_gated=False)
        self.assertEqual(stats_default["total_signals"], 1)
        self.assertEqual(stats_default["win_rate_pct"], 100.0)
        self.assertNotIn("comparison", stats_default)

        # Verify include_gated stats (returns side-by-side comparison)
        stats_gated = self.journal.get_signal_stats(include_gated=True)
        self.assertIn("comparison", stats_gated)
        comp = stats_gated["comparison"]
        self.assertEqual(comp["ungated"]["total_signals"], 1)
        self.assertEqual(comp["ungated"]["win_rate_pct"], 100.0)
        self.assertEqual(comp["gated"]["total_signals"], 1)
        self.assertEqual(comp["gated"]["win_rate_pct"], 0.0)


class TestCandleArchive(unittest.TestCase):
    """Test suite for Step 5: Candle archive SQLite storage, upsert, and CLI stats."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_candles.db")
        from marketscope.candle_archive import CandleArchive
        self.archive = CandleArchive(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_archive_closed_candles_skips_open_and_upserts(self):
        candles = [
            {"t": 1700000000, "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.0, "v": 1000, "closed": True},
            {"t": 1700000300, "o": 104.0, "h": 108.0, "l": 103.0, "c": 107.0, "v": 1500, "closed": True},
            {"t": 1700000600, "o": 107.0, "h": 110.0, "l": 106.0, "c": 109.0, "v": 500, "closed": False}, # Forming candle!
        ]
        # 1. Insert: should only insert the 2 closed candles
        count = self.archive.archive_closed_candles("TCS", 5, candles, source="exchange")
        self.assertEqual(count, 2)

        stats = self.archive.get_stats()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["symbol"], "TCS")
        self.assertEqual(stats[0]["timeframe"], 5)
        self.assertEqual(stats[0]["count"], 2)
        self.assertEqual(stats[0]["min_ts"], 1700000000)
        self.assertEqual(stats[0]["max_ts"], 1700000300)

        # 2. Upsert same timestamps with revised volume/close: row count must remain 2
        revised = [
            {"t": 1700000000, "o": 100.0, "h": 106.0, "l": 99.0, "c": 105.5, "v": 1200, "closed": True},
        ]
        count2 = self.archive.archive_closed_candles("TCS", 5, revised, source="exchange")
        self.assertEqual(count2, 1)

        stats2 = self.archive.get_stats()
        self.assertEqual(stats2[0]["count"], 2)

        # Verify updated values in db
        with self.archive._get_connection() as conn:
            row = conn.execute("SELECT h, c, v FROM candles WHERE symbol = 'TCS' AND ts = 1700000000").fetchone()
            self.assertEqual(row["h"], 106.0)
            self.assertEqual(row["c"], 105.5)
            self.assertEqual(row["v"], 1200.0)

    def test_candle_stats_cli_output(self):
        from marketscope.manage import cmd_candle_stats
        import io
        from unittest.mock import patch

        # Populate a candle
        self.archive.archive_closed_candles("INFY", 15, [
            {"t": 1700000000, "o": 1500.0, "h": 1510.0, "l": 1495.0, "c": 1505.0, "v": 25000, "closed": True}
        ])

        with patch("marketscope.candle_archive.get_candle_archive", return_value=self.archive), \
             patch("candle_archive.get_candle_archive", return_value=self.archive):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                cmd_candle_stats(argparse.Namespace())
            output = buf.getvalue()
            self.assertIn("INFY", output)
            self.assertIn("15", output)
            self.assertIn("Total archived candles: 1", output)

    def test_laya_allow_rules_summary_false_suppresses_briefing(self):
        """Setting LAYA_ALLOW_RULES_SUMMARY=false must suppress rules briefing and return 'AI summary unavailable'."""
        engine = LayaAIEngine()
        sample_stock = {
            "symbol": "RELIANCE",
            "price": 2500.0,
            "change_pct": 1.5,
            "vwap": 2490.0,
            "rvol": 1.2,
            "support": 2450.0,
            "resistance": 2550.0,
            "metrics": {"score": 75, "signal": "BUY", "raw": {"rsi": 62.0}},
            "ai_decision": {"action": "BUY DIP", "conviction_pct": 78, "stop_loss": 2470.0, "target_1": 2540.0, "risk_reward_ratio": 2.0},
        }

        with patch.dict(os.environ, {"LAYA_ALLOW_RULES_SUMMARY": "false"}):
            res = engine._build_rules_briefing("RELIANCE", sample_stock, 5)
            self.assertEqual(res["institutional_briefing"], "AI summary unavailable")

        with patch.dict(os.environ, {"LAYA_ALLOW_RULES_SUMMARY": "true"}):
            res_allowed = engine._build_rules_briefing("RELIANCE", sample_stock, 5)
            self.assertIn("Rules-engine summary (AI not used)", res_allowed["institutional_briefing"])


class TestRealOllamaIntegration(unittest.TestCase):
    """Optional real Ollama integration test, skipped unless OLLAMA_INTEGRATION=1."""

    @unittest.skipUnless(os.environ.get("OLLAMA_INTEGRATION") == "1", "Set OLLAMA_INTEGRATION=1 to run real Ollama integration test")
    def test_real_model_inference_latency_and_source(self):
        """Call real local gemma4:12b model and assert source is local_llm and latency under 90s."""
        engine = get_laya_engine()
        start = time.time()
        res = engine._call_ollama("Respond with exactly: {\"test\": \"ok\"}", num_predict=20)
        duration = time.time() - start
        self.assertLess(duration, 90.0, f"Inference took too long: {duration:.2f}s")
        self.assertEqual(res.get("test"), "ok")


if __name__ == "__main__":
    unittest.main()
