"""
Unit Tests for PDF Formula Architecture, 0-100 Score Tiers, Best/Worst Stock Selection,
and Laya AI Decision Confirmation.
"""

from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from marketscope.market import (
    calculate_composite_score,
    calculate_cpr,
    calculate_camarilla,
    calculate_supertrend,
    calculate_vwap_bands,
    calculate_bollinger_bandwidth,
)
from marketscope.daily_quant import analyze_daily_stock, rank_daily_stocks, FORMULA_VERSION
from marketscope.decision_engine import evaluate_ai_decision
from marketscope.laya_ai import query_laya_decision


class TestScoreTiers(unittest.TestCase):
    """
    Test exact score classification tiers:
      - Score > 70: BULLISH (BUY)
      - Score < 50: BEARISH (SELL)
      - Score 50 to 70: NORMAL (NEUTRAL)
    """

    def test_composite_score_bullish_tier(self):
        # High fast EMA, positive momentum, price > VWAP, high RSI
        res = calculate_composite_score(
            fast_ema=110.0,
            slow_ema=100.0,
            momentum=3.0,
            close=112.0,
            vwap=100.0,
            rsi=80.0,
        )
        self.assertIsNotNone(res["score"])
        self.assertGreater(res["score"], 70)
        self.assertEqual(res["rank_tier"], "BULLISH")
        self.assertEqual(res["tier"], "BULLISH")
        self.assertEqual(res["signal"], "BUY")

    def test_composite_score_bearish_tier(self):
        # Low fast EMA, negative momentum, price < VWAP, low RSI
        res = calculate_composite_score(
            fast_ema=90.0,
            slow_ema=100.0,
            momentum=-3.0,
            close=88.0,
            vwap=100.0,
            rsi=20.0,
        )
        self.assertIsNotNone(res["score"])
        self.assertLess(res["score"], 50)
        self.assertEqual(res["rank_tier"], "BEARISH")
        self.assertEqual(res["tier"], "BEARISH")
        self.assertEqual(res["signal"], "SELL")

    def test_composite_score_normal_tier(self):
        # Flat / balanced parameters
        res = calculate_composite_score(
            fast_ema=100.0,
            slow_ema=100.0,
            momentum=0.0,
            close=100.0,
            vwap=100.0,
            rsi=50.0,
        )
        self.assertIsNotNone(res["score"])
        self.assertEqual(res["score"], 50)
        self.assertEqual(res["rank_tier"], "NORMAL")
        self.assertEqual(res["tier"], "NORMAL")
        self.assertEqual(res["signal"], "NEUTRAL")


class TestBestWorstStockAndTiers(unittest.TestCase):
    """
    Test ranking out of 100, best stock of the day (highest score >= 70),
    worst stock of the day (lowest score < 50), and tier groupings.
    """

    def setUp(self):
        self.session_date = "2026-10-23"

    def _make_stock(self, symbol: str, score: float, as_of_ts: float = 1729677000.0):
        tier = "BULLISH" if score > 70 else ("BEARISH" if score < 50 else "NORMAL")
        return {
            "symbol": symbol,
            "status": "ok",
            "price": 1000.0,
            "daily_quant": {
                "formula_version": FORMULA_VERSION,
                "session_date": self.session_date,
                "eligible": True,
                "score": score,
                "rank_tier": tier,
                "tier": tier,
                "price": 1000.0,
                "timeframe": 5,
                "as_of": "2026-10-23T15:15:00+05:30",
                "as_of_timestamp": as_of_ts,
                "components": {
                    "ema": {"weight": 20, "contribution": 10},
                    "supertrend": {"weight": 20, "contribution": 10},
                },
                "indicators": {
                    "vwap": {"value": 1000.0, "sigma": 5.0},
                    "rsi14": 65.0,
                },
                "window": {"open": "09:15", "close": "15:15", "timezone": "Asia/Kolkata"},
            },
        }

    def test_best_and_worst_selection_with_tiers(self):
        stocks = {
            "TCS": self._make_stock("TCS", 82.5),       # BULLISH (> 70)
            "RELIANCE": self._make_stock("RELIANCE", 62.0), # NORMAL (50-70)
            "INFY": self._make_stock("INFY", 38.0),      # BEARISH (< 50)
        }
        res = rank_daily_stocks(stocks, self.session_date, "PREVIOUS", now=1729680000.0)
        self.assertEqual(res["eligible_count"], 3)
        self.assertEqual(res["excluded_count"], 0)

        # Best stock: TCS (score 82.5, BULLISH)
        self.assertIsNotNone(res["best"])
        self.assertEqual(res["best"]["symbol"], "TCS")
        self.assertEqual(res["best"]["score"], 82.5)
        self.assertEqual(res["best"]["rank_tier"], "BULLISH")

        # Worst stock: INFY (score 38.0, BEARISH)
        self.assertIsNotNone(res["worst"])
        self.assertEqual(res["worst"]["symbol"], "INFY")
        self.assertEqual(res["worst"]["score"], 38.0)
        self.assertEqual(res["worst"]["rank_tier"], "BEARISH")

        # Tiers summary
        ts = res["tiers_summary"]
        self.assertEqual(ts["bullish_count"], 1)
        self.assertEqual(ts["normal_count"], 1)
        self.assertEqual(ts["bearish_count"], 1)

        # Tier lists
        self.assertEqual(len(res["bullish_stocks"]), 1)
        self.assertEqual(res["bullish_stocks"][0]["symbol"], "TCS")
        self.assertEqual(len(res["normal_stocks"]), 1)
        self.assertEqual(res["normal_stocks"][0]["symbol"], "RELIANCE")
        self.assertEqual(len(res["bearish_stocks"]), 1)
        self.assertEqual(res["bearish_stocks"][0]["symbol"], "INFY")

    def test_laya_decision_confirmation_attached(self):
        stocks = {
            "TCS": self._make_stock("TCS", 85.0),
            "INFY": self._make_stock("INFY", 32.0),
        }

        mock_decision = {
            "model": "laya",
            "choice": "bullish",
            "confidence": 0.82,
            "probabilities": {"bullish": 0.82, "bearish": 0.08, "neutral": 0.10},
        }

        with patch("marketscope.daily_quant.query_laya_decision", return_value=mock_decision):
            res = rank_daily_stocks(stocks, self.session_date, "PREVIOUS", now=1729680000.0)
            best = res["best"]
            self.assertIsNotNone(best)
            self.assertIsNotNone(best.get("laya_decision"))
            self.assertEqual(best["laya_decision"]["choice"], "bullish")
            self.assertTrue(best["laya_confirmed"])


class TestPDFIndicatorFormulas(unittest.TestCase):
    """
    Test mathematical correctness of CPR, Camarilla, Supertrend, VWAP bands,
    and Bollinger bandwidth from zien_quant-1.pdf.
    """

    def test_cpr_benchmark_and_narrow_detection(self):
        # CPR benchmark from PDF
        # High = 24650, Low = 24350, Close = 24560
        # Pivot = (24650 + 24350 + 24560)/3 = 24520
        # BC = (24650 + 24350)/2 = 24500
        # TC = 2*24520 - 24500 = 24540
        # Width = 40, Width% = 40 / 24520 * 100 = 0.1631% (< 0.25% -> Narrow CPR)
        cpr = calculate_cpr(24650, 24350, 24560)
        self.assertIsNotNone(cpr)
        self.assertEqual(cpr["pivot"], 24520.0)
        self.assertEqual(cpr["bc"], 24500.0)
        self.assertEqual(cpr["tc"], 24540.0)
        self.assertEqual(cpr["width"], 40.0)
        self.assertAlmostEqual(cpr["width_pct"], 0.1631, places=3)
        self.assertTrue(cpr["is_narrow"])

    def test_camarilla_levels(self):
        # High = 24650, Low = 24350, Close = 24560, Spread = 300
        # H3 = 24560 + 300 * 1.1 / 4 = 24642.5
        # L3 = 24560 - 300 * 1.1 / 4 = 24477.5
        # H4 = 24560 + 300 * 1.1 / 2 = 24725.0
        # L4 = 24560 - 300 * 1.1 / 2 = 24395.0
        cam = calculate_camarilla(24650, 24350, 24560)
        self.assertIsNotNone(cam)
        self.assertEqual(cam["h3"], 24642.5)
        self.assertEqual(cam["l3"], 24477.5)
        self.assertEqual(cam["h4"], 24725.0)
        self.assertEqual(cam["l4"], 24395.0)
        self.assertGreater(cam["h4"], cam["h3"])
        self.assertLess(cam["l4"], cam["l3"])

    def test_supertrend_uptrend_and_downtrend(self):
        # Initial 10 bars establish baseline, followed by breakout candle breaching upper band
        uptrend_candles = [
            {"h": 101, "l": 99, "c": 100} for _ in range(10)
        ] + [
            {"h": 115, "l": 113, "c": 114},  # breaks above upper band (100 + 3*2 = 106)
            {"h": 117, "l": 115, "c": 116},
        ]
        st = calculate_supertrend(uptrend_candles, period=10, multiplier=3.0)
        self.assertIsNotNone(st)
        self.assertEqual(st["direction"], 1)
        self.assertLess(st["value"], uptrend_candles[-1]["c"])

    def test_vwap_bands(self):
        candles = [
            {"h": 102, "l": 98, "c": 100, "v": 1000},
            {"h": 104, "l": 100, "c": 102, "v": 1500},
            {"h": 106, "l": 101, "c": 105, "v": 2000},
        ]
        bands = calculate_vwap_bands(candles)
        self.assertIsNotNone(bands)
        self.assertGreater(bands["upper1"], bands["vwap"])
        self.assertLess(bands["lower1"], bands["vwap"])
        self.assertGreater(bands["upper2"], bands["upper1"])
        self.assertLess(bands["lower2"], bands["lower1"])

    def test_bollinger_bandwidth(self):
        closes = [100.0 + (i % 3) for i in range(25)]
        bw = calculate_bollinger_bandwidth(closes, period=20, num_std=2.0)
        self.assertIsNotNone(bw)
        self.assertGreater(bw, 0.0)


class TestLayaDecisionEngineIntegration(unittest.TestCase):
    """
    Test that Laya AI decision directly impacts setup evaluation,
    boosting conviction and elevating Grade A setups to Grade A+.
    """

    def test_laya_bullish_confirmation_boosts_grade(self):
        # Set up a strong long setup with R:R >= 1.8, RVOL >= 1.0, score >= 65
        metrics = {
            "score": 75,
            "signal": "BUY",
            "is_closed": True,
            "raw": {"rsi": 62.0, "momentum_pct": 1.5, "trend_pct": 0.8, "vwap_dist_pct": 0.5},
        }
        laya_decision = {
            "model": "laya",
            "choice": "bullish",
            "confidence": 0.85,
            "probabilities": {"bullish": 0.85, "bearish": 0.05, "neutral": 0.10},
        }

        # Without Laya: Grade A (R:R=2.25 >= 1.8, RVOL=1.2 < 1.5)
        dec_without = evaluate_ai_decision(
            symbol="RELIANCE",
            price=2500.0,
            metrics=metrics,
            vwap=2490.0,
            support=2490.0,
            resistance=2545.0,
            rvol=1.2,
            order_blocks=[],
            closed_confirming=True,
        )
        self.assertEqual(dec_without["grade"], "A")

        # With Laya confirming Bullish with 85% confidence: elevated to Grade A+
        dec_with = evaluate_ai_decision(
            symbol="RELIANCE",
            price=2500.0,
            metrics=metrics,
            vwap=2490.0,
            support=2490.0,
            resistance=2545.0,
            rvol=1.2,
            order_blocks=[],
            closed_confirming=True,
            laya_decision=laya_decision,
        )
        self.assertEqual(dec_with["grade"], "A+")
        self.assertGreater(dec_with["conviction_pct"], dec_without["conviction_pct"])
        self.assertTrue(any("Laya AI decision confirmed" in f for f in dec_with["factors"]))


if __name__ == "__main__":
    unittest.main()

