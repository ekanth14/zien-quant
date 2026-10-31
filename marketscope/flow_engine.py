"""
MarketScope Flow River & Funnel Engine
Computes synchronized multi-asset flow trajectories, volume-at-price distributions,
hierarchical 4-stage funnel filtering, and conviction setup cards from real candles only.
Real exchange candle data only.
"""

from __future__ import annotations
import math
import logging
from datetime import datetime, date, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from market_schedule import IST
    from market import STOCK_CATALOGUE, DEFAULT_CONFIG
    from candle_archive import CandleArchive, get_candle_archive
    from session_manager import build_session_snapshot, get_session_state, get_session_scorecard, resolve_previous_session_date
    from security import DatabaseManager
except ImportError:
    from marketscope.market_schedule import IST
    from marketscope.market import STOCK_CATALOGUE, DEFAULT_CONFIG
    from marketscope.candle_archive import CandleArchive, get_candle_archive
    from marketscope.session_manager import build_session_snapshot, get_session_state, get_session_scorecard, resolve_previous_session_date
    from marketscope.security import DatabaseManager

logger = logging.getLogger("marketscope.flow_engine")


def generate_session_bars(session_date_str: str) -> List[int]:
    """Generates 75 5-minute bar timestamps (09:15 to 15:25 IST) for the session date."""
    d = datetime.strptime(session_date_str, "%Y-%m-%d").date()
    start_dt = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
    bars = [int((start_dt + timedelta(minutes=5 * i)).timestamp()) for i in range(75)]
    return bars


def compute_volume_profile(candles: List[Dict[str, Any]], num_buckets: int = 14) -> List[Dict[str, Any]]:
    """
    Computes volume-at-price profile across num_buckets price intervals.
    Returns 14 buckets with price_low, price_high, mid, volume, pct, is_poc, is_current.
    """
    if not candles:
        return []
    highs = [float(c.get("h", c.get("high", 0))) for c in candles]
    lows = [float(c.get("l", c.get("low", 0))) for c in candles]
    closes = [float(c.get("c", c.get("close", 0))) for c in candles]
    volumes = [float(c.get("v", c.get("volume", 0))) for c in candles]

    day_min = min(lows)
    day_max = max(highs)
    current_price = closes[-1]

    if day_max <= day_min:
        day_max = day_min + 1.0

    bucket_size = (day_max - day_min) / num_buckets
    bucket_vols = [0.0] * num_buckets

    for c, vol in zip(candles, volumes):
        c_low = float(c.get("l", c.get("low", 0)))
        c_high = float(c.get("h", c.get("high", 0)))
        typical = (c_low + c_high + float(c.get("c", c.get("close", 0)))) / 3.0
        idx = int((typical - day_min) / bucket_size)
        idx = max(0, min(num_buckets - 1, idx))
        bucket_vols[idx] += vol

    max_vol = max(bucket_vols) if bucket_vols else 1.0
    if max_vol == 0:
        max_vol = 1.0
    poc_idx = bucket_vols.index(max(bucket_vols)) if bucket_vols else 0

    curr_idx = int((current_price - day_min) / bucket_size)
    curr_idx = max(0, min(num_buckets - 1, curr_idx))

    buckets = []
    for i in range(num_buckets):
        p_lo = day_min + i * bucket_size
        p_hi = p_lo + bucket_size
        v = bucket_vols[i]
        buckets.append({
            "bucket_index": i,
            "price_low": round(p_lo, 2),
            "price_high": round(p_hi, 2),
            "mid": round((p_lo + p_hi) / 2.0, 2),
            "volume": int(v),
            "pct": round((v / max_vol) * 100.0, 1),
            "is_poc": (i == poc_idx),
            "is_current": (i == curr_idx),
        })
    return buckets


def compute_return_histogram(changes: List[float]) -> Dict[str, Any]:
    """
    Computes distribution histogram of % changes in 0.25% buckets from -4.0% to +4.0%.
    Includes outlier buckets '<-4.0%' and '>+4.0%'.
    """
    buckets = []
    # Lower outlier
    lo_count = sum(1 for c in changes if c < -4.0)
    buckets.append({"label": "<-4.0%", "range": [-999.0, -4.0], "count": lo_count, "bullish": False})

    # 32 regular buckets of 0.25%
    step = 0.25
    cur = -4.0
    for _ in range(32):
        nxt = round(cur + step, 2)
        cnt = sum(1 for c in changes if cur <= c < nxt)
        mid = round((cur + nxt) / 2.0, 2)
        buckets.append({
            "label": f"{cur:+.2f}% to {nxt:+.2f}%",
            "range": [cur, nxt],
            "mid": mid,
            "count": cnt,
            "bullish": (mid >= 0),
        })
        cur = nxt

    # Upper outlier
    hi_count = sum(1 for c in changes if c >= 4.0)
    buckets.append({"label": ">+4.0%", "range": [4.0, 999.0], "count": hi_count, "bullish": True})

    return {
        "step": 0.25,
        "min": -4.0,
        "max": 4.0,
        "buckets": buckets,
        "total": len(changes),
    }


def build_flow_payload(
    session_param: str = "auto",
    date_param: Optional[str] = None,
    candle_archive: Optional[CandleArchive] = None,
    scraper_feed: Optional[Any] = None,
    db_manager: Optional[DatabaseManager] = None,
    clock_dt: Optional[datetime] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Assembles the complete /api/flow payload strictly conforming to the specification:
    session_state, session_date, data_mode, as_of, bars, stocks, sectors, histogram,
    counts, stages, cards, profiles, feed.
    """
    archive = candle_archive or get_candle_archive()
    db = db_manager
    cfg = config or (db.get_latest_config() if db else None) or DEFAULT_CONFIG

    # Resolve state
    state_info = get_session_state(clock_dt=clock_dt, candle_archive=archive, db_manager=db)
    session_state = state_info["session_state"]
    target_date = date_param or state_info["session_date"]

    if session_param == "previous":
        session_state = "PREVIOUS"
        if not date_param:
            target_date = resolve_previous_session_date(state_info, archive, db)

    # Build or retrieve real session snapshot
    snapshot = build_session_snapshot(
        session_date=target_date,
        timeframe=5,
        candle_archive=archive,
        scraper_feed=scraper_feed,
        db_manager=db,
        config=cfg,
    )

    if not snapshot or not snapshot.get("stocks"):
        # Honest empty state / unavailable
        return {
            "session_state": session_state,
            "session_date": target_date,
            "timeframe": 5,
            "data_mode": "UNAVAILABLE",
            "as_of": "N/A",
            "bars": [],
            "stocks": {},
            "sectors": {},
            "histogram": {"step": 0.25, "buckets": [], "total": 0},
            "counts": {"bullish": 0, "bearish": 0},
            "stages": {},
            "cards": [],
            "profiles": {},
            "feed": {"ok_count": 0, "stale_count": 0, "failed_count": len(STOCK_CATALOGUE)},
            "empty": True,
            "message": "Market data unavailable. No real session data exists for this period.",
        }

    data_mode = snapshot.get("data_mode", "PREVIOUS_SESSION" if session_state != "LIVE" else "LIVE")
    as_of = snapshot.get("as_of", "15:30 IST")
    bars = generate_session_bars(target_date)

    # Configurable funnel thresholds
    movers_count = int(cfg.get("flow_movers_count", 8))
    rvol_min = float(cfg.get("flow_rvol_min", 1.2))
    score_thresh = float(cfg.get("flow_score_threshold", 65))
    rr_min = float(cfg.get("flow_rr_min", 1.5))

    snap_stocks = snapshot.get("stocks", {})
    ok_stocks_data: Dict[str, Any] = {}
    changes_list: List[float] = []
    sector_sums: Dict[str, float] = {}
    sector_counts: Dict[str, int] = {}
    raw_candles_map: Dict[str, List[Dict[str, Any]]] = {}

    for sym, sdata in snap_stocks.items():
        if sdata.get("status") != "ok":
            continue

        c_list = archive.get_candles_for_session(sym, 5, target_date)
        if not c_list:
            c_list = sdata.get("candles_preview", [])
        raw_candles_map[sym] = c_list

        if not c_list:
            continue

        first_open = float(c_list[0]["o"])
        avg_vol = (sum(float(c.get("v", 0)) for c in c_list) / max(1, len(c_list))) if c_list else 1.0

        # Create 75-bar aligned series for pct and vol_rel
        bar_candle_map = {int(c["t"]): c for c in c_list}
        pct_series: List[Optional[float]] = []
        vol_rel_series: List[Optional[float]] = []

        for b_ts in bars:
            c = bar_candle_map.get(b_ts)
            if c:
                pct = round(((float(c["c"]) - first_open) / max(0.01, first_open)) * 100.0, 2)
                v_rel = round(float(c.get("v", 0)) / max(1.0, avg_vol), 2)
                pct_series.append(pct)
                vol_rel_series.append(v_rel)
            else:
                pct_series.append(None)
                vol_rel_series.append(None)

        raw_change_pct = sdata.get("change_pct")
        if raw_change_pct is None:
            # No real previous close for this symbol-day: exclude it from
            # day-change aggregations instead of inventing a 0.0% change.
            continue
        change_pct = float(raw_change_pct)
        changes_list.append(change_pct)

        sec = sdata.get("sector", "General")
        sector_sums[sec] = sector_sums.get(sec, 0.0) + change_pct
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

        ai_dec = sdata.get("ai_decision", {})
        metrics = sdata.get("metrics", {})

        entry_low = ai_dec.get("entry_low") or ai_dec.get("entry_price") or sdata.get("price")
        entry_high = ai_dec.get("entry_high") or ai_dec.get("entry_price") or sdata.get("price")
        sl_val = ai_dec.get("stop_loss")
        t1_val = ai_dec.get("target_1")
        t2_val = ai_dec.get("target_2")
        rr_val = float(ai_dec.get("risk_reward_ratio", 0.0))
        cur_price = float(sdata.get("price", 0.0))

        risk_pct = round(abs(cur_price - sl_val) / cur_price * 100.0, 2) if sl_val and cur_price > 0 else 0.0

        ok_stocks_data[sym] = {
            "symbol": sym,
            "name": sdata.get("name", sym),
            "sector": sec,
            "pct": pct_series,
            "vol_rel": vol_rel_series,
            "last": cur_price,
            "change_pct": change_pct,
            "rvol": float(sdata.get("rvol", 1.0)),
            "rsi": float(metrics.get("rsi") or metrics.get("raw", {}).get("rsi") or 50.0),
            "vwap": float(sdata.get("vwap", cur_price)),
            "score": int(metrics.get("score") if metrics.get("score") is not None else sdata.get("score", 50)),
            "action": ai_dec.get("action", "AVOID"),
            "grade": ai_dec.get("grade", "C"),
            "conviction": int(ai_dec.get("conviction_pct", 50)),
            "rr": rr_val,
            "zone": [round(float(entry_low), 2), round(float(entry_high), 2)],
            "sl": round(float(sl_val), 2) if sl_val else None,
            "t1": round(float(t1_val), 2) if t1_val else None,
            "t2": round(float(t2_val), 2) if t2_val else None,
            "risk_pct": risk_pct,
            "atr": float(metrics.get("raw", {}).get("atr") or sdata.get("atr") or 0.0),
        }

    # Sectors average % change
    sectors_avg = {sec: round(sector_sums[sec] / max(1, sector_counts[sec]), 2) for sec in sector_sums}

    # Histogram & Counts
    histogram = compute_return_histogram(changes_list)
    bullish_count = sum(1 for c in changes_list if c >= 0)
    bearish_count = sum(1 for c in changes_list if c < 0)

    # Funnel Stages Execution
    # Stage 1: MOVERS
    # Separate bullish and bearish
    bull_pool = [s for s in ok_stocks_data.values() if s["change_pct"] >= 0]
    bear_pool = [s for s in ok_stocks_data.values() if s["change_pct"] < 0]

    bull_pool.sort(key=lambda x: x["change_pct"], reverse=True)
    bear_pool.sort(key=lambda x: x["change_pct"], reverse=False) # Most negative first

    s1_bull = bull_pool[:movers_count]
    s1_bear = bear_pool[:movers_count]
    s1_active_syms = set([s["symbol"] for s in s1_bull + s1_bear])

    s1_dropped = []
    for s in bull_pool[movers_count:] + bear_pool[movers_count:]:
        s1_dropped.append({
            "symbol": s["symbol"],
            "reason": f"Ranked outside top/bottom {movers_count} movers (change: {s['change_pct']:+.2f}%)",
        })

    # Stage 2: VOLUME (RVOL >= rvol_min)
    s2_bull = []
    s2_bear = []
    s2_dropped = []

    for s in s1_bull:
        if s["rvol"] >= rvol_min:
            s2_bull.append(s)
        else:
            s2_dropped.append({
                "symbol": s["symbol"],
                "reason": f"RVOL {s['rvol']:.2f} < {rvol_min:.2f} threshold",
            })
    for s in s1_bear:
        if s["rvol"] >= rvol_min:
            s2_bear.append(s)
        else:
            s2_dropped.append({
                "symbol": s["symbol"],
                "reason": f"RVOL {s['rvol']:.2f} < {rvol_min:.2f} threshold",
            })

    # Stage 3: PRESSURE (Score >= score_thresh for bull, Score <= 100 - score_thresh for bear)
    s3_bull = []
    s3_bear = []
    s3_dropped = []

    for s in s2_bull:
        if s["score"] >= score_thresh:
            s3_bull.append(s)
        else:
            s3_dropped.append({
                "symbol": s["symbol"],
                "reason": f"Score {s['score']} < {score_thresh} buying pressure hurdle",
            })
    for s in s2_bear:
        if s["score"] <= (100.0 - score_thresh):
            s3_bear.append(s)
        else:
            s3_dropped.append({
                "symbol": s["symbol"],
                "reason": f"Score {s['score']} > {100.0 - score_thresh} selling pressure hurdle",
            })

    # Stage 4: SETUPS (Action not AVOID/HOLD, grade in A+, A, B, RR >= rr_min)
    s4_bull = []
    s4_bear = []
    s4_dropped = []

    for s in s3_bull:
        if s["action"] in ("AVOID", "HOLD", "NO_TRADE"):
            s4_dropped.append({"symbol": s["symbol"], "reason": f"Action is {s['action']}"})
        elif s["grade"] not in ("A+", "A", "B"):
            s4_dropped.append({"symbol": s["symbol"], "reason": f"Grade {s['grade']} is below B"})
        elif s["rr"] < rr_min:
            s4_dropped.append({"symbol": s["symbol"], "reason": f"R:R {s['rr']:.2f} < {rr_min:.2f} minimum hurdle"})
        else:
            s4_bull.append(s)

    for s in s3_bear:
        if s["action"] in ("AVOID", "HOLD", "NO_TRADE"):
            s4_dropped.append({"symbol": s["symbol"], "reason": f"Action is {s['action']}"})
        elif s["grade"] not in ("A+", "A", "B"):
            s4_dropped.append({"symbol": s["symbol"], "reason": f"Grade {s['grade']} is below B"})
        elif s["rr"] < rr_min:
            s4_dropped.append({"symbol": s["symbol"], "reason": f"R:R {s['rr']:.2f} < {rr_min:.2f} minimum hurdle"})
        else:
            s4_bear.append(s)

    stages_payload = {
        "stage1_movers": {
            "name": "MOVERS",
            "threshold": movers_count,
            "bullish": [s["symbol"] for s in s1_bull],
            "bearish": [s["symbol"] for s in s1_bear],
            "dropped": s1_dropped,
        },
        "stage2_volume": {
            "name": "VOLUME",
            "threshold": rvol_min,
            "bullish": [s["symbol"] for s in s2_bull],
            "bearish": [s["symbol"] for s in s2_bear],
            "dropped": s2_dropped,
        },
        "stage3_pressure": {
            "name": "PRESSURE",
            "threshold": score_thresh,
            "bullish": [s["symbol"] for s in s3_bull],
            "bearish": [s["symbol"] for s in s3_bear],
            "dropped": s3_dropped,
        },
        "stage4_setups": {
            "name": "SETUPS",
            "threshold": rr_min,
            "bullish": [s["symbol"] for s in s4_bull],
            "bearish": [s["symbol"] for s in s4_bear],
            "dropped": s4_dropped,
        },
    }

    # Volume Profiles for Stages 3 and 4 stocks only
    stage3_4_syms = set(
        [s["symbol"] for s in s3_bull + s3_bear + s4_bull + s4_bear]
    )
    profiles_payload: Dict[str, List[Dict[str, Any]]] = {}
    for sym in stage3_4_syms:
        c_list = raw_candles_map.get(sym, [])
        if c_list:
            profiles_payload[sym] = compute_volume_profile(c_list, num_buckets=14)

    # Setup Cards: up to 3 bullish, up to 3 bearish from stage 4, sorted by conviction
    s4_bull.sort(key=lambda x: x["conviction"], reverse=True)
    s4_bear.sort(key=lambda x: x["conviction"], reverse=True)

    scorecard = get_session_scorecard(target_date, db_manager=db)
    # Check if signals exists in scorecard
    signal_outcomes_by_symbol: Dict[str, str] = {}
    if db:
        try:
            with db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT symbol, outcome FROM signals WHERE session_date = ? AND (gated = 0 OR gated IS NULL)",
                    (target_date,)
                )
                for row in cur.fetchall():
                    signal_outcomes_by_symbol[row[0]] = row[1]
        except Exception as e:
            logger.debug("Error checking signal outcomes for cards: %s", e)

    cards_payload: List[Dict[str, Any]] = []
    for s in s4_bull[:3]:
        c_list = raw_candles_map.get(s["symbol"], [])
        closes_series = [float(c["c"]) for c in c_list]
        cards_payload.append({
            "type": "BULL",
            "symbol": s["symbol"],
            "name": s["name"],
            "sector": s["sector"],
            "last": s["last"],
            "change_pct": s["change_pct"],
            "grade": s["grade"],
            "conviction": s["conviction"],
            "action": s["action"],
            "zone": s["zone"],
            "sl": s["sl"],
            "t1": s["t1"],
            "t2": s["t2"],
            "rr": s["rr"],
            "risk_pct": s["risk_pct"],
            "rvol": s["rvol"],
            "rsi": s["rsi"],
            "mini_series": {
                "closes": closes_series,
                "vwap": s["vwap"],
            },
            "outcome": signal_outcomes_by_symbol.get(s["symbol"]),
        })

    for s in s4_bear[:3]:
        c_list = raw_candles_map.get(s["symbol"], [])
        closes_series = [float(c["c"]) for c in c_list]
        cards_payload.append({
            "type": "BEAR",
            "symbol": s["symbol"],
            "name": s["name"],
            "sector": s["sector"],
            "last": s["last"],
            "change_pct": s["change_pct"],
            "grade": s["grade"],
            "conviction": s["conviction"],
            "action": s["action"],
            "zone": s["zone"],
            "sl": s["sl"],
            "t1": s["t1"],
            "t2": s["t2"],
            "rr": s["rr"],
            "risk_pct": s["risk_pct"],
            "rvol": s["rvol"],
            "rsi": s["rsi"],
            "mini_series": {
                "closes": closes_series,
                "vwap": s["vwap"],
            },
            "outcome": signal_outcomes_by_symbol.get(s["symbol"]),
        })

    ok_cnt = len(ok_stocks_data)
    total_tracked = len(STOCK_CATALOGUE)
    failed_cnt = max(0, total_tracked - ok_cnt)
    is_empty = (ok_cnt == 0)

    return {
        "session_state": session_state,
        "session_date": target_date,
        "timeframe": 5,
        "data_mode": "UNAVAILABLE" if is_empty else data_mode,
        "as_of": as_of,
        "bars": bars,
        "stocks": ok_stocks_data,
        "sectors": sectors_avg,
        "histogram": histogram,
        "counts": {
            "bullish": bullish_count,
            "bearish": bearish_count,
        },
        "stages": stages_payload,
        "cards": cards_payload,
        "profiles": profiles_payload,
        "feed": {
            "ok_count": ok_cnt,
            "stale_count": 0,
            "failed_count": failed_cnt,
        },
        "empty": is_empty,
    }

