"""
MarketScope - NSE Market Analysis Engine
Deterministic technical indicator calculations, candle aggregation,
and signal scoring.
"""

from __future__ import annotations
import math
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from decision_engine import evaluate_ai_decision
except ImportError:
    from marketscope.decision_engine import evaluate_ai_decision

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# 24 Supported NSE Equity Symbols & Metadata
STOCK_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "RELIANCE": {"name": "Reliance Industries", "sector": "Energy", "base_price": 2450.0},
    "HDFCBANK": {"name": "HDFC Bank", "sector": "Banking", "base_price": 1520.0},
    "INFY": {"name": "Infosys", "sector": "Technology", "base_price": 1480.0},
    "TCS": {"name": "Tata Consultancy Services", "sector": "Technology", "base_price": 3920.0},
    "ICICIBANK": {"name": "ICICI Bank", "sector": "Banking", "base_price": 1150.0},
    "BHARTIARTL": {"name": "Bharti Airtel", "sector": "Telecom", "base_price": 1320.0},
    "ITC": {"name": "ITC Limited", "sector": "Consumer", "base_price": 430.0},
    "LT": {"name": "Larsen & Toubro", "sector": "Industrials", "base_price": 3580.0},
    "SBIN": {"name": "State Bank of India", "sector": "Banking", "base_price": 780.0},
    "AXISBANK": {"name": "Axis Bank", "sector": "Banking", "base_price": 1180.0},
    "SUNPHARMA": {"name": "Sun Pharmaceutical", "sector": "Healthcare", "base_price": 1680.0},
    "TITAN": {"name": "Titan Company", "sector": "Consumer", "base_price": 3420.0},
    "MARUTI": {"name": "Maruti Suzuki", "sector": "Auto", "base_price": 11950.0},
    "BAJFINANCE": {"name": "Bajaj Finance", "sector": "Finance", "base_price": 6850.0},
    "HINDUNILVR": {"name": "Hindustan Unilever", "sector": "Consumer", "base_price": 2380.0},
    "TATASTEEL": {"name": "Tata Steel", "sector": "Metals", "base_price": 150.0},
    "JSWSTEEL": {"name": "JSW Steel", "sector": "Metals", "base_price": 920.0},
    "ONGC": {"name": "Oil & Natural Gas Corp.", "sector": "Energy", "base_price": 260.0},
    "NTPC": {"name": "NTPC Limited", "sector": "Energy", "base_price": 380.0},
    "M&M": {"name": "Mahindra & Mahindra", "sector": "Auto", "base_price": 2750.0},
    "WIPRO": {"name": "Wipro Limited", "sector": "Technology", "base_price": 510.0},
    "DRREDDY": {"name": "Dr. Reddy's Laboratories", "sector": "Healthcare", "base_price": 6600.0},
    "POWERGRID": {"name": "Power Grid Corporation", "sector": "Energy", "base_price": 310.0},
    "ULTRACEMCO": {"name": "UltraTech Cement", "sector": "Industrials", "base_price": 10800.0},
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "brand_name": "MarketScope",
    "tagline": "NSE Flow Terminal & Directional Intelligence",
    "fast_ema_period": 9,
    "slow_ema_period": 21,
    "rsi_period": 14,
    "score_threshold": 65,
    "default_timeframe": 5,
    "refresh_interval_ms": 2000,
    "formula_approved": True,
    "calendar_year": 2026,
    "holidays": ["2026-01-26", "2026-03-25", "2026-04-14", "2026-05-01", "2026-08-15", "2026-10-02", "2026-11-08"],
    "monitored_symbols": list(STOCK_CATALOGUE.keys()),

    # Composite Score Weights (sum = 100)
    "weight_trend": 35.0,
    "weight_momentum": 30.0,
    "weight_vwap": 20.0,
    "weight_rsi": 15.0,

    # Composite Normalizers
    "norm_trend": 0.4,
    "norm_momentum": 1.2,
    "norm_vwap": 0.8,
    "norm_rsi": 30.0,

    # Decision Engine Score Cutoffs
    "score_breakout_long": 70,
    "score_pullback_long": 65,
    "score_breakdown_short": 30,
    "score_pullback_short": 35,

    # Decision Engine RSI Bands
    "rsi_pullback_long_min": 42.0,
    "rsi_pullback_long_max": 68.0,
    "rsi_pullback_short_min": 32.0,
    "rsi_pullback_short_max": 58.0,
    "rsi_overbought": 76.0,

    # Minimum Risk Percentages
    "min_risk_pct_breakout": 0.006,
    "min_risk_pct_pullback": 0.005,

    # Risk-Reward Multipliers and Hurdles
    "min_rr_hurdle": 1.5,
    "conviction_rr_hurdle": 1.8,
    "rr_target1_mult": 1.5,
    "rr_target2_mult": 2.6,

    # Grade Thresholds
    "grade_aplus_rr": 2.2,
    "grade_aplus_rvol": 1.5,
    "grade_aplus_momentum": 1.2,
    "grade_a_rr": 1.8,

    # Flow Terminal & Funnel Defaults
    "flow_movers_count": 8,
    "flow_rvol_min": 1.2,
    "flow_score_threshold": 65,
    "flow_rr_min": 1.5,
}


def clamp(val: float, low: float = -1.0, high: float = 1.0) -> float:
    """Clamp value between [low, high]."""
    return max(low, min(high, val))


def calculate_ema(closes: List[float], period: int) -> List[Optional[float]]:
    """
    Calculate Exponential Moving Average.
    alpha = 2 / (period + 1)
    EMA[0] = closes[0]
    EMA[t] = alpha * closes[t] + (1 - alpha) * EMA[t-1]
    """
    if not closes or period <= 0:
        return []
    alpha = 2.0 / (period + 1.0)
    ema_list: List[Optional[float]] = []
    ema = closes[0]
    ema_list.append(ema)
    for price in closes[1:]:
        ema = alpha * price + (1.0 - alpha) * ema
        ema_list.append(ema)
    return ema_list


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Welles Wilder smoothed RSI.
    Seed with the simple average of the first `period` changes, then smooth:
      avg_gain = (prev_avg_gain * (period - 1) + gain) / period
      avg_loss = (prev_avg_loss * (period - 1) + loss) / period
    Returns None when fewer than period + 1 closes are available.
    """
    if len(closes) < period + 1 or period <= 0:
        return None

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    seed_changes = changes[:period]

    avg_gain = sum(max(c, 0.0) for c in seed_changes) / float(period)
    avg_loss = sum(max(-c, 0.0) for c in seed_changes) / float(period)

    for c in changes[period:]:
        gain = max(c, 0.0)
        loss = max(-c, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / float(period)
        avg_loss = (avg_loss * (period - 1) + loss) / float(period)

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_vwap(candles: List[Dict[str, Any]]) -> Optional[float]:
    """
    Calculate candle-based VWAP:
    typical_price = (high + low + close) / 3
    VWAP = sum(typical_price * volume) / sum(volume)
    """
    if not candles:
        return None
    tot_vol = 0.0
    tot_tp_vol = 0.0
    for c in candles:
        v = float(c.get("v", 0))
        if v <= 0:
            continue
        tp = (float(c["h"]) + float(c["l"]) + float(c["c"])) / 3.0
        tot_vol += v
        tot_tp_vol += tp * v

    if tot_vol <= 0:
        return None
    return tot_tp_vol / tot_vol


def calculate_momentum(closes: List[float], period: int = 5) -> Optional[float]:
    """
    Calculate five-bar momentum percentage:
    100 * (latest_close / close_five_bars_earlier - 1)
    """
    if len(closes) <= period:
        return None
    past = closes[-1 - period]
    if past <= 0:
        return None
    return 100.0 * (closes[-1] / past - 1.0)


def calculate_rvol(volumes: List[float], period: int = 20) -> float:
    """
    Relative volume: latest bar volume / mean of up to 20 preceding bar volumes.
    Excludes the latest bar from the denominator.
    """
    if not volumes:
        return 1.0
    latest = volumes[-1]
    preceding = volumes[:-1][-period:]
    if not preceding:
        return 1.0
    avg = sum(preceding) / len(preceding)
    if avg <= 0:
        return 1.0
    return latest / avg


def calculate_support_resistance(candles: List[Dict[str, Any]], period: int = 20) -> Tuple[float, float]:
    """
    Rolling support (lowest low) and resistance (highest high) of up to `period` latest bars.
    """
    if not candles:
        return 0.0, 0.0
    subset = candles[-period:]
    support = min(float(c["l"]) for c in subset)
    resistance = max(float(c["h"]) for c in subset)
    return support, resistance


def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """
    Calculate Welles Wilder Average True Range (ATR).
    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    Seed with the simple average of the first `period` TRs, then smooth:
      atr = (prev_atr * (period - 1) + tr) / period
    Returns None when fewer than 2 candles are available.
    """
    if not candles or len(candles) < 2 or period <= 0:
        return None

    tr_list: List[float] = []
    for i in range(1, len(candles)):
        h = float(candles[i].get("h", candles[i].get("high", 0.0)))
        l = float(candles[i].get("l", candles[i].get("low", 0.0)))
        prev_c = float(candles[i - 1].get("c", candles[i - 1].get("close", 0.0)))
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    if not tr_list:
        return None
    if len(tr_list) < period:
        return round(sum(tr_list) / len(tr_list), 4)

    atr = sum(tr_list[:period]) / float(period)
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / float(period)
    return round(atr, 4)


def calculate_composite_score(
    fast_ema: Optional[float],
    slow_ema: Optional[float],
    momentum: Optional[float],
    close: float,
    vwap: Optional[float],
    rsi: Optional[float],
    score_threshold: int = 65,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Calculate the MarketScope Composite Directional Score (0 to 100):
    - EMA trend: 100 * (fast_EMA / slow_EMA - 1) -> w_trend * clamp(trend / norm_trend)
    - Momentum: 5-bar % change -> w_mom * clamp(momentum / norm_mom)
    - VWAP dist: 100 * (close / VWAP - 1) -> w_vwap * clamp(dist / norm_vwap)
    - RSI direction: RSI - 50 -> w_rsi * clamp((RSI - 50) / norm_rsi)
    score = round(50 + weighted_sum / 2)
    """
    if fast_ema is None or slow_ema is None or slow_ema <= 0 or momentum is None or rsi is None:
        return {
            "score": None,
            "signal": "UNAVAILABLE",
            "rank_tier": "UNAVAILABLE",
            "tier": "UNAVAILABLE",
            "components": {},
            "raw": {},
        }

    cfg = config or DEFAULT_CONFIG
    w_trend = float(cfg.get("weight_trend", 35.0))
    w_mom = float(cfg.get("weight_momentum", 30.0))
    w_vwap = float(cfg.get("weight_vwap", 20.0))
    w_rsi = float(cfg.get("weight_rsi", 15.0))

    norm_trend = float(cfg.get("norm_trend", 0.4))
    norm_mom = float(cfg.get("norm_momentum", 1.2))
    norm_vwap = float(cfg.get("norm_vwap", 0.8))
    norm_rsi = float(cfg.get("norm_rsi", 30.0))

    thresh = int(cfg.get("score_threshold", score_threshold))

    trend_raw = 100.0 * (fast_ema / slow_ema - 1.0)
    trend_contrib = w_trend * clamp(trend_raw / norm_trend)

    momentum_raw = momentum
    momentum_contrib = w_mom * clamp(momentum_raw / norm_mom)

    if vwap is not None and vwap > 0:
        vwap_dist_raw = 100.0 * (close / vwap - 1.0)
        vwap_contrib = w_vwap * clamp(vwap_dist_raw / norm_vwap)
    else:
        vwap_dist_raw = 0.0
        vwap_contrib = 0.0

    rsi_raw = rsi
    rsi_contrib = w_rsi * clamp((rsi - 50.0) / norm_rsi)

    weighted_sum = trend_contrib + momentum_contrib + vwap_contrib + rsi_contrib
    score = round(50.0 + weighted_sum / 2.0)
    score = max(0, min(100, int(score)))

    # Strict quant ranking tiers from PDF formula engine:
    # Score > 70: BULLISH, Score < 50: BEARISH, 50-70: NORMAL
    if score > 70:
        rank_tier = "BULLISH"
    elif score < 50:
        rank_tier = "BEARISH"
    else:
        rank_tier = "NORMAL"

    if score >= thresh:
        signal = "BUY"
    elif score <= (100 - thresh):
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    return {
        "score": score,
        "signal": signal,
        "rank_tier": rank_tier,
        "tier": rank_tier,
        "components": {
            "trend": round(trend_contrib, 2),
            "momentum": round(momentum_contrib, 2),
            "vwap": round(vwap_contrib, 2),
            "rsi": round(rsi_contrib, 2),
        },
        "raw": {
            "trend_pct": round(trend_raw, 3),
            "momentum_pct": round(momentum_raw, 3),
            "vwap_dist_pct": round(vwap_dist_raw, 3),
            "rsi": round(rsi_raw, 2),
        },
    }


def calculate_order_blocks(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Order-block zone heuristic:
    For each candidate candle starting at index 6 (must be closed):
    - Bullish breakout: closes strictly above highest high of previous 5 candles.
      Origin: search backwards in those 5 candles for the most recent down candle.
      Zone: low to high range of that opposing candle.
    - Bearish breakout: closes strictly below lowest low of previous 5 candles.
      Origin: search backwards in those 5 candles for the most recent up candle.
      Zone: low to high range of that opposing candle.
    - Invalidation:
      Bullish zone invalidated if any subsequent closed candle closes below its low.
      Bearish zone invalidated if any subsequent closed candle closes above its high.
    Returns up to the last 4 surviving active zones.
    """
    if len(candles) < 7:
        return []

    active_zones: List[Dict[str, Any]] = []

    for i in range(6, len(candles)):
        curr = candles[i]
        if not curr.get("closed", True):
            continue

        c_close = float(curr["c"])
        prev_5 = candles[i - 5:i]
        high_prev_5 = max(float(c["h"]) for c in prev_5)
        low_prev_5 = min(float(c["l"]) for c in prev_5)

        # Check for Bullish Breakout
        if c_close > high_prev_5:
            # Find most recent opposing (down) candle in prev 5: close < open
            found = False
            for j in range(i - 1, i - 6, -1):
                cand = candles[j]
                if float(cand["c"]) < float(cand["o"]):
                    active_zones.append({
                        "type": "bullish",
                        "low": round(float(cand["l"]), 2),
                        "high": round(float(cand["h"]), 2),
                        "index": j,
                        "time": cand.get("t"),
                        "breakout_index": i,
                    })
                    found = True
                    break
            if not found:
                # Fallback to candle with close <= open
                for j in range(i - 1, i - 6, -1):
                    cand = candles[j]
                    if float(cand["c"]) <= float(cand["o"]):
                        active_zones.append({
                            "type": "bullish",
                            "low": round(float(cand["l"]), 2),
                            "high": round(float(cand["h"]), 2),
                            "index": j,
                            "time": cand.get("t"),
                            "breakout_index": i,
                        })
                        break

        # Check for Bearish Breakout
        elif c_close < low_prev_5:
            # Find most recent opposing (up) candle in prev 5: close > open
            found = False
            for j in range(i - 1, i - 6, -1):
                cand = candles[j]
                if float(cand["c"]) > float(cand["o"]):
                    active_zones.append({
                        "type": "bearish",
                        "low": round(float(cand["l"]), 2),
                        "high": round(float(cand["h"]), 2),
                        "index": j,
                        "time": cand.get("t"),
                        "breakout_index": i,
                    })
                    found = True
                    break
            if not found:
                # Fallback to candle with close >= open
                for j in range(i - 1, i - 6, -1):
                    cand = candles[j]
                    if float(cand["c"]) >= float(cand["o"]):
                        active_zones.append({
                            "type": "bearish",
                            "low": round(float(cand["l"]), 2),
                            "high": round(float(cand["h"]), 2),
                            "index": j,
                            "time": cand.get("t"),
                            "breakout_index": i,
                        })
                        break

        # Check invalidation of existing zones by current candle close
        surviving: List[Dict[str, Any]] = []
        for zone in active_zones:
            if zone["breakout_index"] >= i:
                surviving.append(zone)
                continue
            if zone["type"] == "bullish" and c_close < zone["low"]:
                continue  # Invalidated
            if zone["type"] == "bearish" and c_close > zone["high"]:
                continue  # Invalidated
            surviving.append(zone)
        active_zones = surviving

    # Return up to last 4 surviving zones
    return active_zones[-4:]


def calculate_cpr(high: float, low: float, close: float) -> Optional[Dict[str, Any]]:
    """
    Central Pivot Range (CPR) from prior session high, low, close:
      Pivot (P) = (High + Low + Close) / 3
      Bottom Central (BC) = (High + Low) / 2
      Top Central (TC) = 2 * Pivot - BC
    Ordered bounds: bc = min(BC, TC), tc = max(BC, TC).
    width_pct = 100 * abs(TC - BC) / Pivot.
    Narrow CPR (width_pct < 0.25) indicates high trending/breakout potential.
    """
    if high <= 0 or low <= 0 or close <= 0 or high < low:
        return None
    pivot = (high + low + close) / 3.0
    bc_raw = (high + low) / 2.0
    tc_raw = 2.0 * pivot - bc_raw
    bc = min(bc_raw, tc_raw)
    tc = max(bc_raw, tc_raw)
    width = abs(tc - bc)
    width_pct = (width / pivot) * 100.0 if pivot > 0 else 0.0
    return {
        "pivot": round(pivot, 2),
        "bc": round(bc, 2),
        "tc": round(tc, 2),
        "width": round(width, 2),
        "width_pct": round(width_pct, 4),
        "is_narrow": width_pct < 0.25,
    }


def calculate_camarilla(high: float, low: float, close: float) -> Optional[Dict[str, float]]:
    """
    Camarilla Pivots from prior session high, low, close:
      Spread = High - Low
      H1..H4 = Close + Spread * 1.1 / (12, 6, 4, 2)
      L1..L4 = Close - Spread * 1.1 / (12, 6, 4, 2)
      H5 = (High / Low) * Close
      L5 = 2 * Close - H5
    H3 / L3 are mean-reversion reversal boundaries.
    H4 / L4 are breakout / breakdown acceleration levels.
    """
    if high <= 0 or low <= 0 or close <= 0 or high < low:
        return None
    spread = high - low
    h1 = close + spread * 1.1 / 12.0
    h2 = close + spread * 1.1 / 6.0
    h3 = close + spread * 1.1 / 4.0
    h4 = close + spread * 1.1 / 2.0
    l1 = close - spread * 1.1 / 12.0
    l2 = close - spread * 1.1 / 6.0
    l3 = close - spread * 1.1 / 4.0
    l4 = close - spread * 1.1 / 2.0
    h5 = (high / low) * close
    l5 = 2.0 * close - h5
    return {
        "h1": round(h1, 2), "h2": round(h2, 2), "h3": round(h3, 2), "h4": round(h4, 2), "h5": round(h5, 2),
        "l1": round(l1, 2), "l2": round(l2, 2), "l3": round(l3, 2), "l4": round(l4, 2), "l5": round(l5, 2),
    }


def calculate_supertrend(
    candles: List[Dict[str, Any]],
    period: int = 10,
    multiplier: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """
    Supertrend indicator with Welles Wilder ATR and ratcheting bands:
      Basic Upper Band (BUB) = (High + Low) / 2 + multiplier * ATR
      Basic Lower Band (BLB) = (High + Low) / 2 - multiplier * ATR
      Final Upper Band ratchets DOWN unless previous close broke it.
      Final Lower Band ratchets UP unless previous close broke it.
      Direction switches to +1 (bullish) when Close > Final Upper Band.
      Direction switches to -1 (bearish) when Close < Final Lower Band.
    """
    if not candles or len(candles) < period + 1:
        return None

    tr_list: List[float] = [float(candles[0].get("h", candles[0].get("high", 0.0))) - float(candles[0].get("l", candles[0].get("low", 0.0)))]
    for i in range(1, len(candles)):
        h = float(candles[i].get("h", candles[i].get("high", 0.0)))
        l = float(candles[i].get("l", candles[i].get("low", 0.0)))
        prev_c = float(candles[i - 1].get("c", candles[i - 1].get("close", 0.0)))
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    atr_series: List[Optional[float]] = [None] * len(candles)
    if len(tr_list) >= period:
        atr_series[period - 1] = sum(tr_list[:period]) / float(period)
        for i in range(period, len(tr_list)):
            atr_series[i] = (atr_series[i - 1] * (period - 1) + tr_list[i]) / float(period)

    upper = lower = line = None
    direction = 0
    for i, candle in enumerate(candles):
        curr_atr = atr_series[i]
        if curr_atr is None:
            continue
        h = float(candle.get("h", candle.get("high", 0.0)))
        l = float(candle.get("l", candle.get("low", 0.0)))
        c = float(candle.get("c", candle.get("close", 0.0)))
        midpoint = (h + l) / 2.0
        basic_upper = midpoint + multiplier * curr_atr
        basic_lower = midpoint - multiplier * curr_atr

        if upper is None:
            upper, lower, line = basic_upper, basic_lower, basic_upper
            direction = -1
        else:
            prev_upper, prev_lower = upper, lower
            prev_c = float(candles[i - 1].get("c", candles[i - 1].get("close", 0.0)))
            upper = basic_upper if basic_upper < prev_upper or prev_c > prev_upper else prev_upper
            lower = basic_lower if basic_lower > prev_lower or prev_c < prev_lower else prev_lower
            if line == prev_upper:
                direction = 1 if c > upper else -1
            else:
                direction = -1 if c < lower else 1
            line = lower if direction == 1 else upper

    if line is None:
        return None
    return {
        "value": round(line, 2),
        "direction": direction,
        "upper": round(upper, 2) if upper is not None else None,
        "lower": round(lower, 2) if lower is not None else None,
        "period": period,
        "multiplier": multiplier,
    }


def calculate_vwap_bands(candles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Intraday Volume Weighted Average Price (VWAP) with Standard Deviation Bands (+/- 1 sigma, +/- 2 sigma):
      Typical Price = (High + Low + Close) / 3
      VWAP = sum(Typical Price * Volume) / sum(Volume)
      Variance = sum(Volume * (Typical Price - VWAP)^2) / sum(Volume)
      Sigma = sqrt(Variance)
    """
    if not candles:
        return None

    cum_vol = 0.0
    cum_tp_vol = 0.0
    typical_prices: List[float] = []
    volumes: List[float] = []

    for c in candles:
        h = float(c.get("h", c.get("high", 0.0)))
        l = float(c.get("l", c.get("low", 0.0)))
        cl = float(c.get("c", c.get("close", 0.0)))
        v = float(c.get("v", c.get("volume", 0.0)))
        tp = (h + l + cl) / 3.0
        typical_prices.append(tp)
        volumes.append(v)
        cum_vol += v
        cum_tp_vol += tp * v

    if cum_vol <= 0:
        return None

    vwap = cum_tp_vol / cum_vol
    variance = sum(v * (tp - vwap) ** 2 for tp, v in zip(typical_prices, volumes)) / cum_vol
    sigma = math.sqrt(max(0.0, variance))

    return {
        "vwap": round(vwap, 2),
        "sigma": round(sigma, 4),
        "upper1": round(vwap + sigma, 2),
        "lower1": round(vwap - sigma, 2),
        "upper2": round(vwap + 2.0 * sigma, 2),
        "lower2": round(vwap - 2.0 * sigma, 2),
    }


def calculate_bollinger_bandwidth(closes: List[float], period: int = 20, num_std: float = 2.0) -> Optional[float]:
    """
    Bollinger Bandwidth = (Upper Band - Lower Band) / Middle Band
    where Middle = SMA(period), Upper = Middle + num_std * std, Lower = Middle - num_std * std.
    """
    if not closes or len(closes) < period or period <= 0:
        return None
    window = closes[-period:]
    mean = sum(window) / float(period)
    if mean <= 0:
        return None
    variance = sum((x - mean) ** 2 for x in window) / float(period)
    std = math.sqrt(variance)
    bandwidth = (2.0 * num_std * std) / mean
    return round(bandwidth, 4)


def aggregate_candles(candles_1m: List[Dict[str, Any]], timeframe: int = 5) -> List[Dict[str, Any]]:
    """
    Aggregate 1-minute candles into timeframe buckets (1m, 5m, 15m, 60m),
    anchored at 09:15 IST each session day.
    """
    if timeframe <= 1 or not candles_1m:
        return candles_1m

    buckets: List[Dict[str, Any]] = []
    current_bucket: Optional[Dict[str, Any]] = None
    current_bucket_idx: Optional[int] = None

    for c in candles_1m:
        # Parse timestamp or minute index
        dt_str = c.get("t", "")
        try:
            dt = datetime.fromisoformat(dt_str)
            # Minute of session from 09:15
            minutes_from_open = (dt.hour - 9) * 60 + (dt.minute - 15)
        except Exception:
            minutes_from_open = len(buckets) * timeframe

        bucket_idx = max(0, minutes_from_open // timeframe)

        if current_bucket_idx != bucket_idx:
            if current_bucket is not None:
                current_bucket["closed"] = True
                buckets.append(current_bucket)
            current_bucket = {
                "t": c["t"],
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
                "v": int(c.get("v", 0)),
                "closed": bool(c.get("closed", True)),
            }
            current_bucket_idx = bucket_idx
        else:
            current_bucket["h"] = max(current_bucket["h"], float(c["h"]))
            current_bucket["l"] = min(current_bucket["l"], float(c["l"]))
            current_bucket["c"] = float(c["c"])
            current_bucket["v"] += int(c.get("v", 0))
            if not c.get("closed", True):
                current_bucket["closed"] = False

    if current_bucket is not None:
        buckets.append(current_bucket)

    return buckets



