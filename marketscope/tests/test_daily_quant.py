"""Reference arithmetic and temporal/data-integrity checks for daily rankings."""

import math
import unittest
from datetime import datetime, timedelta, timezone

try:
    from marketscope.daily_quant import analyze_daily_stock, rank_daily_stocks, _pivots, _supertrend, _wilder
except ImportError:
    from daily_quant import analyze_daily_stock, rank_daily_stocks, _pivots, _supertrend, _wilder


IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-10-23"
START = datetime(2026, 10, 23, 9, 15, tzinfo=IST).timestamp()
NOW = datetime(2026, 10, 23, 16, 0, tzinfo=IST)


def bars(count=40, start=START, increment=1, opening=100):
    result = []
    for index in range(count):
        close = opening + index * increment
        result.append({"t": start + index * 300, "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 100 + index, "closed": True})
    return result


def stock(symbol, candles, now=NOW):
    return {"symbol": symbol, "status": "ok", "daily_quant": analyze_daily_stock(candles, DAY, now=now)}


class TestDailyFormulaArithmetic(unittest.TestCase):
    def test_weighted_vwap_and_sigma_match_manual_calculation(self):
        candles = bars()
        quant = analyze_daily_stock(candles, DAY, now=NOW)
        self.assertTrue(quant["eligible"], quant["reason"])
        total_volume = sum(candle["v"] for candle in candles)
        expected = sum(candle["c"] * candle["v"] for candle in candles) / total_volume
        sigma = math.sqrt(sum(candle["v"] * (candle["c"] - expected) ** 2 for candle in candles) / total_volume)
        self.assertAlmostEqual(quant["indicators"]["vwap"]["value"], expected)
        self.assertAlmostEqual(quant["indicators"]["vwap"]["sigma"], sigma)
        self.assertAlmostEqual(quant["indicators"]["rsi14"], 100)
        self.assertAlmostEqual(quant["indicators"]["atr14"], 2)
        self.assertEqual(sum(part["weight"] for part in quant["components"].values()), 100)
        self.assertAlmostEqual(quant["score"], 50 + sum(part["contribution"] for part in quant["components"].values()) / 2, places=6)

    def test_flat_series_is_neutral_and_has_no_divide_by_zero(self):
        candles = bars(increment=0)
        for candle in candles:
            candle["h"] = candle["l"] = candle["c"]
        quant = analyze_daily_stock(candles, DAY, now=NOW)
        self.assertTrue(quant["eligible"])
        self.assertEqual(quant["indicators"]["rsi14"], 50)
        self.assertEqual(quant["score"], 50)
        self.assertIsNone(quant["indicators"]["stochastic"]["k"])
        self.assertIsNone(quant["indicators"]["stochastic"]["d"])

    def test_wilder_smoothing_uses_recursive_seed(self):
        values = [1.0] * 14 + [15.0, 1.0]
        calculated = _wilder(values, 14)
        self.assertEqual(calculated[13], 1)
        self.assertEqual(calculated[14], 2)
        self.assertAlmostEqual(calculated[15], 27 / 14)

    def test_supertrend_flips_and_ratchets(self):
        candles = [{"h": 101, "l": 99, "c": 100}, {"h": 111, "l": 109, "c": 110}, {"h": 112, "l": 110, "c": 111}]
        result = _supertrend(candles, [2, 2, 2])
        self.assertEqual(result["direction"], 1)
        self.assertEqual(result["value"], 105)
        candles.append({"h": 99, "l": 97, "c": 98})
        result = _supertrend(candles, [2, 2, 2, 2])
        self.assertEqual(result["direction"], -1)
        self.assertEqual(result["value"], 104)

    def test_pivots_match_pdf_benchmark_and_cpr_boundaries_are_ordered(self):
        quant = _pivots({"date": "2026-10-22", "o": 24420, "h": 24650, "l": 24350, "c": 24560, "complete": True}, DAY)
        self.assertEqual(quant["cpr"]["pivot"], 24520)
        self.assertEqual(quant["cpr"]["tc"], 24540)
        self.assertEqual(quant["cpr"]["bc"], 24500)
        self.assertEqual(quant["camarilla"]["h3"], 24642.5)
        self.assertEqual(quant["camarilla"]["l4"], 24395)
        self.assertEqual(quant["floor"]["r1"], 24690)
        self.assertAlmostEqual(quant["fibonacci"]["s2"], 24334.6)
        reversed_raw = _pivots({"date": "2026-10-22", "o": 120, "h": 130, "l": 100, "c": 105, "complete": True}, DAY)
        self.assertLessEqual(reversed_raw["cpr"]["bc"], reversed_raw["cpr"]["pivot"])
        self.assertGreaterEqual(reversed_raw["cpr"]["tc"], reversed_raw["cpr"]["pivot"])

    def test_future_or_incomplete_prior_session_is_not_used(self):
        for prior in ({"date": DAY, "complete": True}, {"date": "2026-10-22", "complete": False}):
            quant = analyze_daily_stock(bars(), DAY, now=NOW, prior_session=prior)
            self.assertTrue(quant["eligible"])
            self.assertIsNone(quant["pivots"])

    def test_session_vwap_obv_and_opening_range_reset_with_prior_warmup(self):
        previous_start = datetime(2026, 10, 22, 9, 15, tzinfo=IST).timestamp()
        previous = bars(75, previous_start, increment=2)
        current = bars(3, START, increment=1, opening=300)
        quant = analyze_daily_stock(previous + current, DAY, now=NOW)
        self.assertTrue(quant["eligible"], quant["reason"])
        expected = sum(candle["c"] * candle["v"] for candle in current) / sum(candle["v"] for candle in current)
        self.assertAlmostEqual(quant["indicators"]["vwap"]["value"], expected)
        self.assertEqual(quant["indicators"]["obv"], 203)
        self.assertEqual(quant["opening_range"]["high"], 303)
        self.assertEqual(quant["opening_range"]["low"], 299)
        self.assertEqual(quant["opening_range"]["direction"], "NONE")

    def test_opening_range_confirmation_needs_close_vwap_and_volume(self):
        candles = bars()
        candles[-1]["v"] = 1000
        quant = analyze_daily_stock(candles, DAY, now=NOW)
        self.assertEqual(quant["opening_range"]["direction"], "LONG")
        candles[-1]["v"] = 1
        quant = analyze_daily_stock(candles, DAY, now=NOW)
        self.assertEqual(quant["opening_range"]["direction"], "NONE")


class TestDailyDataIntegrity(unittest.TestCase):
    def test_incomplete_history_and_missing_open_or_gap_are_unranked(self):
        cases = [bars(34), bars()[1:], bars()[:10] + bars()[11:]]
        for candles in cases:
            with self.subTest(count=len(candles)):
                quant = analyze_daily_stock(candles, DAY, now=NOW)
                self.assertFalse(quant["eligible"])
                self.assertIsNone(quant["score"])

    def test_invalid_ohlcv_is_never_filled_with_zeroes(self):
        for key, value in (("c", None), ("c", float("nan")), ("v", 0), ("v", -1), ("h", 1), ("o", False)):
            candles = bars()
            candles[-1][key] = value
            with self.subTest(key=key, value=value):
                quant = analyze_daily_stock(candles, DAY, now=NOW)
                self.assertFalse(quant["eligible"])
                self.assertIsNone(quant["score"])

    def test_duplicate_or_reversed_timestamps_are_not_sorted_away(self):
        for timestamp in (bars()[-2]["t"], bars()[-2]["t"] - 300):
            candles = bars()
            candles[-1]["t"] = timestamp
            self.assertIn("nonmonotonic", analyze_daily_stock(candles, DAY, now=NOW)["reason"])

    def test_forming_candle_does_not_change_closed_calculation(self):
        candles = bars()
        expected = analyze_daily_stock(candles[:-1], DAY, now=NOW)
        candles[-1].update(closed=False, c=900, h=1000)
        actual = analyze_daily_stock(candles, DAY, now=NOW)
        self.assertEqual(actual["score"], expected["score"])
        self.assertEqual(actual["as_of"], expected["as_of"])
        self.assertEqual(actual["price"], candles[-2]["c"])

    def test_future_candle_and_nonmatching_end_reject_observation(self):
        candles = bars()
        self.assertFalse(analyze_daily_stock(candles, DAY, now=START + 600)["eligible"])
        candles[-1]["end"] = candles[-1]["t"] + 60
        self.assertFalse(analyze_daily_stock(candles, DAY, now=NOW)["eligible"])

    def test_later_session_is_ignored_for_historical_observation(self):
        baseline = analyze_daily_stock(bars(), DAY, now=NOW)
        next_day = bars(10, START + 86400, opening=900)
        result = analyze_daily_stock(bars() + next_day, DAY, now=NOW)
        self.assertEqual(result, baseline)


class TestDailyCrossSection(unittest.TestCase):
    def test_daily_ranking_compares_complete_same_cutoff_scores(self):
        stocks = {"UP": stock("UP", bars()), "DOWN": stock("DOWN", bars(increment=-1, opening=200))}
        ranking = rank_daily_stocks(stocks, DAY, "PREVIOUS", now=NOW)
        self.assertEqual(ranking["eligible_count"], 2)
        self.assertEqual(ranking["best"]["symbol"], "UP")
        self.assertEqual(ranking["worst"]["symbol"], "DOWN")
        self.assertGreater(ranking["best"]["score"], ranking["worst"]["score"])
        self.assertEqual(ranking["best"]["price"], stocks["UP"]["daily_quant"]["price"])

    def test_stale_failed_wrong_day_and_old_cutoff_are_excluded(self):
        stocks = {"UP": stock("UP", bars()), "DOWN": stock("DOWN", bars(increment=-1, opening=200)), "OLD": stock("OLD", bars(39)), "FAILED": stock("FAILED", bars()), "WRONG": stock("WRONG", bars())}
        stocks["FAILED"]["status"] = "stale"
        stocks["WRONG"]["daily_quant"]["session_date"] = "2026-10-22"
        ranking = rank_daily_stocks(stocks, DAY, "PREVIOUS", now=NOW)
        self.assertEqual(ranking["eligible_count"], 2)
        self.assertEqual(ranking["excluded_count"], 3)
        live = rank_daily_stocks(stocks, DAY, "LIVE", now=NOW)
        self.assertEqual(live["eligible_count"], 0)
        self.assertIsNone(live["best"])

    def test_no_forced_winner_for_tied_or_single_stock(self):
        for stocks in ([stock("A", bars())], [stock("A", bars()), stock("B", bars())]):
            ranking = rank_daily_stocks(stocks, DAY, "PREVIOUS", now=NOW)
            self.assertIsNone(ranking["best"])
            self.assertIsNone(ranking["worst"])
            self.assertIsNotNone(ranking["reason"])


if __name__ == "__main__":
    unittest.main()

