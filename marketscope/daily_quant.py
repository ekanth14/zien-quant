"""Auditable OHLCV formulas from the supplied intraday reference (pages 7-14).

The six-factor directional score and its weights are an application policy, not
a formula or calibrated probability supplied by the document. Models explain
these calculations; they do not calculate prices or select the ranking.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from marketscope.laya_ai import query_laya_decision
except ImportError:
    try:
        from laya_ai import query_laya_decision
    except ImportError:
        query_laya_decision = None

IST = timezone(timedelta(hours=5, minutes=30))
FORMULA_VERSION = "pdf-quant-1"
WEIGHTS = {"ema": 20, "supertrend": 20, "vwap": 20, "rsi": 15, "macd": 15, "obv": 10}
SCORE_DEFINITION = (
    "50 + 0.5 * sum(weight * signed_value). Signed values are bounded [-1,1]: "
    "EMA(9)-EMA(21) / ATR(14); Supertrend(10,3) direction; "
    "(close-session VWAP) / ATR(14); (Wilder RSI(14)-50)/20; "
    "MACD(12,26,9) histogram / ATR(14); session OBV / session volume. "
    "Weights are application policy, not learned probabilities or PDF-prescribed weights."
)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("missing or nonnumeric OHLCV value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite OHLCV value")
    return result


def _now_timestamp(now: Any) -> float:
    if now is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        return now.timestamp()
    return _number(now)


def _bounded(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _ema(values: List[float], period: int) -> List[Optional[float]]:
    """SMA seed, then alpha=2/(n+1), without invented warmup values."""
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    result[period - 1] = _mean(values[:period])
    alpha = 2.0 / (period + 1)
    for index in range(period, len(values)):
        result[index] = alpha * values[index] + (1 - alpha) * result[index - 1]
    return result


def _wilder(values: List[float], period: int) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    result[period - 1] = _mean(values[:period])
    for index in range(period, len(values)):
        result[index] = ((period - 1) * result[index - 1] + values[index]) / period
    return result


def _wma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum((index + 1) * value for index, value in enumerate(values[-period:])) / (period * (period + 1) / 2)


def _supertrend(bars: List[Dict[str, float]], atr: List[Optional[float]]) -> Dict[str, Any]:
    upper = lower = line = None
    direction = 0
    for index, bar in enumerate(bars):
        if atr[index] is None:
            continue
        midpoint = (bar["h"] + bar["l"]) / 2
        basic_upper, basic_lower = midpoint + 3 * atr[index], midpoint - 3 * atr[index]
        if upper is None:
            upper, lower, line = basic_upper, basic_lower, basic_upper
            direction = -1
        else:
            previous_upper, previous_lower = upper, lower
            previous_close = bars[index - 1]["c"]
            upper = basic_upper if basic_upper < upper or previous_close > upper else upper
            lower = basic_lower if basic_lower > lower or previous_close < lower else lower
            if line == previous_upper:
                direction = 1 if bar["c"] > upper else -1
            else:
                direction = -1 if bar["c"] < lower else 1
            line = lower if direction == 1 else upper
    if atr[-1] == 0:
        direction = 0
    return {"value": line, "direction": direction, "upper": upper, "lower": lower, "period": 10, "multiplier": 3}


def _pivots(prior: Any, session_date: str) -> Optional[Dict[str, Any]]:
    if not isinstance(prior, dict) or prior.get("complete") is not True:
        return None
    prior_date = prior.get("session_date", prior.get("date"))
    try:
        if date.fromisoformat(prior_date) >= date.fromisoformat(session_date):
            return None
        high = _number(prior.get("h", prior.get("high")))
        low = _number(prior.get("l", prior.get("low")))
        close = _number(prior.get("c", prior.get("close")))
        opening = _number(prior.get("o", prior.get("open")))
        if low <= 0 or high < max(opening, close, low) or low > min(opening, close):
            return None
    except (ValueError, TypeError):
        return None
    spread = high - low
    pivot = (high + low + close) / 3
    bc_raw = (high + low) / 2
    tc_raw = 2 * pivot - bc_raw
    camarilla = {}
    for level, divisor in ((1, 12), (2, 6), (3, 4), (4, 2)):
        camarilla[f"h{level}"] = close + spread * 1.1 / divisor
        camarilla[f"l{level}"] = close - spread * 1.1 / divisor
    camarilla["h5"] = high / low * close
    camarilla["l5"] = 2 * close - camarilla["h5"]
    demark_x = 2 * high + low + close if close > opening else high + 2 * low + close if close < opening else high + low + 2 * close
    return {
        "prior_session_date": prior_date,
        "cpr": {"pivot": pivot, "bc": min(bc_raw, tc_raw), "tc": max(bc_raw, tc_raw), "width_pct": abs(tc_raw - bc_raw) / pivot * 100},
        "camarilla": camarilla,
        "floor": {"pivot": pivot, "r1": 2 * pivot - low, "s1": 2 * pivot - high, "r2": pivot + spread, "s2": pivot - spread, "r3": high + 2 * (pivot - low), "s3": low - 2 * (high - pivot)},
        "fibonacci": {"pivot": pivot, **{f"{side}{index}": pivot + sign * factor * spread for side, sign in (("r", 1), ("s", -1)) for index, factor in enumerate((0.382, 0.618, 1), 1)}},
        "woodie": {"pivot": (high + low + 2 * close) / 4},
        "demark": {"pivot": demark_x / 4, "r1": demark_x / 2 - low, "s1": demark_x / 2 - high},
    }


def analyze_daily_stock(
    candles: List[Dict[str, Any]], session_date: str, timeframe: int = 5,
    now: Any = None, prior_session: Optional[Dict[str, Any]] = None,
    window_close: str = "15:30",
) -> Dict[str, Any]:
    """Compute a closed-candle daily observation; never use future session data.

    Trend formulas may warm up on prior regular sessions. Session VWAP, OBV,
    ADL and the opening range reset every IST session. All six ranking factors
    must be available under identical weights; optional pivots do not affect it.
    """
    output: Dict[str, Any] = {
        "formula_version": FORMULA_VERSION, "source_document": "zien_quant-1.pdf",
        "eligible": False, "reason": None, "session_date": session_date,
        "timeframe": timeframe, "as_of": None, "as_of_timestamp": None,
        "candle_count": 0, "session_candle_count": 0, "score": None, "price": None,
        "components": {}, "indicators": {}, "opening_range": None,
        "pivots": None, "score_definition": SCORE_DEFINITION,
        "window": {"open": "09:15", "close": window_close, "timezone": "Asia/Kolkata"},
    }

    def unavailable(reason: str) -> Dict[str, Any]:
        output["reason"] = reason
        return output

    try:
        selected_date = date.fromisoformat(session_date)
        now_ts = _now_timestamp(now)
        end_time = time.fromisoformat(window_close)
        if end_time not in (time(15, 15), time(15, 30)):
            raise ValueError("Unsupported calculation window")
    except (ValueError, TypeError):
        return unavailable("Invalid session date or observation timestamp")
    if isinstance(timeframe, bool) or timeframe not in (1, 3, 5, 15):
        return unavailable("Supported daily formula intervals are 1, 3, 5 and 15 minutes")
    seconds = int(timeframe * 60)
    opening_ts = datetime.combine(selected_date, time(9, 15), IST).timestamp()
    close_ts = datetime.combine(selected_date, end_time, IST).timestamp()
    bars: List[Dict[str, float]] = []
    previous_input_ts = None
    for candle in candles or []:
        if not isinstance(candle, dict):
            return unavailable("Invalid candle record")
        try:
            timestamp = _number(candle.get("t", candle.get("ts", candle.get("start", candle.get("time")))))
            candle_date = datetime.fromtimestamp(timestamp, IST).date()
        except (ValueError, TypeError, OverflowError, OSError):
            return unavailable("Missing or invalid candle timestamp")
        if candle_date > selected_date:
            continue
        day_open = datetime.combine(candle_date, time(9, 15), IST).timestamp()
        day_close = datetime.combine(candle_date, end_time, IST).timestamp()
        if not day_open <= timestamp < day_close:
            continue
        if previous_input_ts is not None and timestamp <= previous_input_ts:
            return unavailable("Duplicate or nonmonotonic candle timestamps")
        previous_input_ts = timestamp
        if timestamp > now_ts:
            return unavailable("Future candle timestamp in selected history")
        if candle.get("closed", candle.get("is_closed", True)) is False:
            continue
        end = timestamp + seconds
        if end > now_ts:
            continue
        if end > day_close or (timestamp - day_open) % seconds:
            return unavailable("Candle interval does not match the selected timeframe")
        supplied_end = candle.get("end")
        if supplied_end is not None:
            try:
                if _number(supplied_end) != end:
                    return unavailable("Candle end does not match the selected timeframe")
            except ValueError:
                return unavailable("Invalid candle end timestamp")
        try:
            bar = {key: _number(candle.get(key, candle.get(full))) for key, full in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"))}
        except ValueError as exc:
            return unavailable(str(exc))
        if min(bar["o"], bar["h"], bar["l"], bar["c"]) <= 0:
            return unavailable("Nonpositive OHLC price")
        if bar["h"] < max(bar["o"], bar["c"], bar["l"]) or bar["l"] > min(bar["o"], bar["c"]):
            return unavailable("Inconsistent OHLC high/low bounds")
        if bar["v"] <= 0:
            return unavailable("Missing or nonpositive traded candle volume")
        bar.update(t=timestamp, end=end)
        bars.append(bar)
    session = [bar for bar in bars if opening_ts <= bar["t"] < close_ts]
    output["candle_count"] = len(bars)
    output["session_candle_count"] = len(session)
    if session:
        output["as_of_timestamp"] = session[-1]["end"]
        output["as_of"] = datetime.fromtimestamp(session[-1]["end"], timezone.utc).isoformat()
    if len(session) < 3:
        return unavailable("At least three closed candles from the selected session are required")
    if session[0]["t"] != opening_ts:
        return unavailable("Selected session is missing its opening candle")
    if any(b["t"] - a["t"] != seconds for a, b in zip(session, session[1:])):
        return unavailable("Selected session contains a missing candle interval")
    if len(bars) < 35:
        return unavailable("At least 35 closed candles are required for indicator warmup")
    # Only regular market closures can bridge dates; in-session gaps cannot be
    # compressed into adjacent indicator observations.
    for left, right in zip(bars, bars[1:]):
        left_date = datetime.fromtimestamp(left["t"], IST).date()
        right_date = datetime.fromtimestamp(right["t"], IST).date()
        if left_date == right_date:
            if right["t"] - left["t"] != seconds:
                return unavailable("Indicator history contains a missing candle interval")
        elif left["end"] != datetime.combine(left_date, end_time, IST).timestamp() or right["t"] != datetime.combine(right_date, time(9, 15), IST).timestamp():
            return unavailable("Incomplete overnight boundary in indicator history")

    closes = [bar["c"] for bar in bars]
    true_ranges = [bars[0]["h"] - bars[0]["l"]]
    true_ranges.extend(max(bar["h"] - bar["l"], abs(bar["h"] - previous["c"]), abs(bar["l"] - previous["c"])) for previous, bar in zip(bars, bars[1:]))
    atr14 = _wilder(true_ranges, 14)[-1]
    ema9, ema21 = _ema(closes, 9)[-1], _ema(closes, 21)[-1]
    gains = [max(0, current - previous) for previous, current in zip(closes, closes[1:])]
    losses = [max(0, previous - current) for previous, current in zip(closes, closes[1:])]
    avg_gain, avg_loss = _wilder(gains, 14)[-1], _wilder(losses, 14)[-1]
    rsi = 50.0 if avg_gain == avg_loss == 0 else 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    fast_macd, slow_macd = _ema(closes, 12), _ema(closes, 26)
    macd_values = [fast - slow for fast, slow in zip(fast_macd, slow_macd) if fast is not None and slow is not None]
    macd_signal = _ema(macd_values, 9)[-1]
    macd_line = macd_values[-1]
    histogram = macd_line - macd_signal
    supertrend = _supertrend(bars, _wilder(true_ranges, 10))
    session_volume = sum(bar["v"] for bar in session)
    typical = [(bar["h"] + bar["l"] + bar["c"]) / 3 for bar in session]
    vwap = sum(value * bar["v"] for value, bar in zip(typical, session)) / session_volume
    sigma = math.sqrt(max(0, sum(bar["v"] * (value - vwap) ** 2 for value, bar in zip(typical, session)) / session_volume))
    obv = sum((1 if current["c"] > previous["c"] else -1 if current["c"] < previous["c"] else 0) * current["v"] for previous, current in zip(session, session[1:]))
    adl = sum(((2 * bar["c"] - bar["l"] - bar["h"]) / (bar["h"] - bar["l"]) * bar["v"]) if bar["h"] != bar["l"] else 0 for bar in session)
    middle = _mean(closes[-20:])
    deviation = math.sqrt(_mean([(value - middle) ** 2 for value in closes[-20:]]))
    stochastic = []
    for end_index in range(len(bars) - 2, len(bars) + 1):
        window = bars[end_index - 14:end_index]
        high, low = max(bar["h"] for bar in window), min(bar["l"] for bar in window)
        stochastic.append(None if high == low else 100 * (window[-1]["c"] - low) / (high - low))
    hma_period = 20
    hma_diff = [2 * _wma(closes[:end_index], hma_period // 2) - _wma(closes[:end_index], hma_period) for end_index in range(hma_period, len(closes) + 1)]
    output["indicators"] = {
        "ema9": ema9, "ema21": ema21, "sma20": middle, "wma20": _wma(closes, 20),
        "hma20": _wma(hma_diff, int(math.sqrt(hma_period))),
        "vwma20": sum(bar["c"] * bar["v"] for bar in bars[-20:]) / sum(bar["v"] for bar in bars[-20:]),
        "atr14": atr14, "rsi14": rsi, "supertrend": supertrend,
        "vwap": {"value": vwap, "sigma": sigma, "upper1": vwap + sigma, "lower1": vwap - sigma, "upper2": vwap + 2 * sigma, "lower2": vwap - 2 * sigma},
        "macd": {"line": macd_line, "signal": macd_signal, "histogram": histogram},
        "bollinger": {"middle": middle, "upper": middle + 2 * deviation, "lower": middle - 2 * deviation, "bandwidth": 4 * deviation / middle},
        "stochastic": {"k": stochastic[-1], "d": _mean(stochastic) if all(value is not None for value in stochastic) else None},
        "obv": obv, "adl": adl, "session_volume": session_volume,
    }
    divisor = atr14 if atr14 > 0 else closes[-1] * 1e-9
    signed = {"ema": _bounded((ema9 - ema21) / divisor), "supertrend": supertrend["direction"], "vwap": _bounded((closes[-1] - vwap) / divisor), "rsi": _bounded((rsi - 50) / 20), "macd": _bounded(histogram / divisor), "obv": _bounded(obv / session_volume)}
    output["components"] = {key: {"weight": weight, "signed_value": signed[key], "contribution": signed[key] * weight} for key, weight in WEIGHTS.items()}
    output["score"] = round(50 + 0.5 * sum(part["contribution"] for part in output["components"].values()), 6)
    score_val = output["score"]
    rank_tier = "BULLISH" if score_val > 70 else ("BEARISH" if score_val < 50 else "NORMAL")
    output["rank_tier"] = rank_tier
    output["tier"] = rank_tier
    output["price"] = closes[-1]
    output["pivots"] = _pivots(prior_session, session_date)
    or_end = opening_ts + 15 * 60
    opening_bars = [bar for bar in session if bar["t"] < or_end]
    if 15 % timeframe == 0 and len(opening_bars) == 15 // timeframe and opening_bars[-1]["end"] == or_end:
        high, low = max(bar["h"] for bar in opening_bars), min(bar["l"] for bar in opening_bars)
        last = session[-1]
        after_range = last["t"] >= or_end
        volume_mean = _mean([bar["v"] for bar in opening_bars])
        direction = "LONG" if after_range and last["c"] > high and last["c"] > vwap and last["v"] > volume_mean else "SHORT" if after_range and last["c"] < low and last["c"] < vwap and last["v"] > volume_mean else "NONE"
        cpr = (output["pivots"] or {}).get("cpr")
        output["opening_range"] = {"high": high, "low": low, "midpoint": (high + low) / 2, "height": high - low, "mean_volume": volume_mean, "direction": direction, "narrow_cpr": cpr["width_pct"] < 0.25 if cpr else None, "status": "PRICE_VOLUME_ALIGNMENT" if direction != "NONE" else "NO_BREAKOUT", "note": "Price/volume alignment only; CPR and overhead structure need separate confirmation."}
    output["eligible"] = True
    return output


def _attach_laya_decision_to_candidate(candidate: Dict[str, Any], is_best: bool = True) -> None:
    fn = query_laya_decision
    if fn is None:
        candidate["laya_decision"] = None
        candidate["laya_confirmed"] = None
        return

    try:
        decision = fn(
            symbol=candidate["symbol"],
            stock_data={
                "price": candidate.get("price"),
                "score": candidate.get("score"),
                "vwap": (candidate.get("indicators") or {}).get("vwap", {}).get("value"),
                "rsi": (candidate.get("indicators") or {}).get("rsi14"),
                "rvol": 1.0,
                "change_pct": 0.0,
            },
            timeout=1.5,
        )
        candidate["laya_decision"] = decision
        if decision:
            choice = str(decision.get("choice", "")).lower()
            if is_best and choice == "bullish":
                candidate["laya_confirmed"] = True
            elif not is_best and choice == "bearish":
                candidate["laya_confirmed"] = True
            else:
                candidate["laya_confirmed"] = False
        else:
            candidate["laya_confirmed"] = None
    except Exception:
        candidate["laya_decision"] = None
        candidate["laya_confirmed"] = None


def rank_daily_stocks(
    stocks: Any, session_date: str, session_state: Optional[str] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """Rank comparable observed directions, with no future-return claim."""
    result = {"formula_version": FORMULA_VERSION, "session_date": session_date, "session_state": session_state, "as_of": None, "eligible_count": 0, "excluded_count": 0, "excluded": [], "best": None, "worst": None, "reason": None, "scope": "eligible monitored equities", "score_definition": SCORE_DEFINITION, "weights": dict(WEIGHTS), "labels": {"best": "Strongest observed stock", "worst": "Weakest observed stock"}}
    candidates = []
    now_ts = _now_timestamp(now)
    items = stocks.items() if isinstance(stocks, dict) else ((stock.get("symbol", ""), stock) for stock in stocks or [])
    seen = set()
    for key, stock in items:
        symbol = str(stock.get("symbol", key)).upper()
        quant = stock.get("daily_quant") or {}
        reason = None
        if not symbol or symbol in seen:
            reason = "Missing or duplicate stock symbol"
        elif stock.get("status", "ok") != "ok" or stock.get("data_quality") in ("stale", "failed", "unavailable"):
            reason = "Stock feed is stale or unavailable"
        elif not quant.get("eligible"):
            reason = quant.get("reason") or "Daily formula inputs unavailable"
        elif quant.get("formula_version") != FORMULA_VERSION or quant.get("session_date") != session_date:
            reason = "Formula version or session mismatch"
        else:
            try:
                score = _number(quant.get("score"))
                end_ts = _number(quant.get("as_of_timestamp"))
                tf = _number(quant.get("timeframe"))
                freshness_clock = now_ts
                window = quant.get("window") or {}
                if window.get("close") in ("15:15", "15:30"):
                    window_end = datetime.combine(date.fromisoformat(session_date), time.fromisoformat(window["close"]), IST).timestamp()
                    freshness_clock = min(now_ts, window_end)
                if not 0 <= score <= 100 or tf not in (1, 3, 5, 15):
                    reason = "Invalid daily score or timeframe"
                elif end_ts > now_ts:
                    reason = "Future daily observation"
                elif session_state == "LIVE" and freshness_clock - end_ts > 2 * tf * 60 + 60:
                    reason = "Closed-candle daily observation is stale"
            except ValueError:
                reason = "Missing daily score or observation timestamp"
        if reason:
            result["excluded"].append({"symbol": symbol, "reason": reason})
            continue
        seen.add(symbol)
        cand_tier = "BULLISH" if score > 70 else ("BEARISH" if score < 50 else "NORMAL")
        candidates.append({
            "symbol": symbol,
            "name": stock.get("name", symbol),
            "score": score,
            "rank_tier": cand_tier,
            "tier": cand_tier,
            "as_of": quant.get("as_of"),
            "as_of_timestamp": end_ts,
            "timeframe": tf,
            "session_date": session_date,
            "components": quant.get("components", {}),
            "indicators": quant.get("indicators", {}),
            "price": quant.get("price"),
            "window": quant.get("window"),
        })
    # One cutoff and one interval make the cross-sectional comparison auditable.
    if candidates:
        newest = max(candidate["as_of_timestamp"] for candidate in candidates)
        intervals = {candidate["timeframe"] for candidate in candidates}
        windows = {str(candidate.get("window")) for candidate in candidates}
        if len(intervals) > 1 or len(windows) > 1:
            for candidate in candidates:
                result["excluded"].append({"symbol": candidate["symbol"], "reason": "Mixed ranking timeframes or session windows"})
            candidates = []
        else:
            aligned = []
            for candidate in candidates:
                if candidate["as_of_timestamp"] != newest:
                    result["excluded"].append({"symbol": candidate["symbol"], "reason": "Observation does not match the latest ranking cutoff"})
                else:
                    aligned.append(candidate)
            candidates = aligned
    result["eligible_count"] = len(candidates)
    result["window"] = candidates[0].get("window") if candidates else None
    result["excluded_count"] = len(result["excluded"])

    bullish_stocks = [c for c in candidates if c["score"] > 70]
    bearish_stocks = [c for c in candidates if c["score"] < 50]
    normal_stocks = [c for c in candidates if 50 <= c["score"] <= 70]
    result["tiers_summary"] = {
        "bullish_count": len(bullish_stocks),
        "bearish_count": len(bearish_stocks),
        "normal_count": len(normal_stocks),
    }
    result["bullish_stocks"] = bullish_stocks
    result["bearish_stocks"] = bearish_stocks
    result["normal_stocks"] = normal_stocks

    if len(candidates) < 2:
        result["reason"] = "At least two comparable stocks with complete closed-candle formulas are required"
    elif max(candidate["score"] for candidate in candidates) - min(candidate["score"] for candidate in candidates) < 1e-6:
        result["reason"] = "All eligible stocks have the same directional score"
    else:
        candidates.sort(key=lambda candidate: (-candidate["score"], candidate["symbol"]))
        best_candidate = candidates[0]
        worst_candidate = sorted(candidates, key=lambda candidate: (candidate["score"], candidate["symbol"]))[0]
        _attach_laya_decision_to_candidate(best_candidate, is_best=True)
        _attach_laya_decision_to_candidate(worst_candidate, is_best=False)
        result["best"], result["worst"] = best_candidate, worst_candidate
        result["as_of"] = candidates[0]["as_of"]
    return result

