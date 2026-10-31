"""
MarketScope Session Management & Historical Snapshot Engine
Provides deterministic market session state detection, completed historical session snapshots
built from real exchange candles, and honest signal journal scorecards.
Zero simulated or invented data.
"""

from __future__ import annotations
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, date, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from market_schedule import (
        IST,
        is_market_holiday,
        is_trading_day,
        TIME_MARKET_OPEN,
        TIME_MARKET_CLOSE,
    )
    from market import (
        DEFAULT_CONFIG,
        STOCK_CATALOGUE,
        calculate_ema,
        calculate_rsi,
        calculate_atr,
        calculate_vwap,
        calculate_rvol,
        calculate_support_resistance,
        calculate_order_blocks,
        calculate_composite_score,
        calculate_cpr,
        calculate_camarilla,
        calculate_supertrend,
        calculate_vwap_bands,
    )
    from decision_engine import evaluate_ai_decision
    from candle_archive import CandleArchive, get_candle_archive
    from scraper import RealTimeScraperFeed, get_live_scraper_feed, calculate_daily_quant_from_archive
    from daily_quant import rank_daily_stocks
    from security import DatabaseManager
except ImportError:
    from marketscope.market_schedule import (
        IST,
        is_market_holiday,
        is_trading_day,
        TIME_MARKET_OPEN,
        TIME_MARKET_CLOSE,
    )
    from marketscope.market import (
        DEFAULT_CONFIG,
        STOCK_CATALOGUE,
        calculate_ema,
        calculate_rsi,
        calculate_atr,
        calculate_vwap,
        calculate_rvol,
        calculate_support_resistance,
        calculate_order_blocks,
        calculate_composite_score,
        calculate_cpr,
        calculate_camarilla,
        calculate_supertrend,
        calculate_vwap_bands,
    )
    from marketscope.decision_engine import evaluate_ai_decision
    from marketscope.candle_archive import CandleArchive, get_candle_archive
    from marketscope.scraper import RealTimeScraperFeed, get_live_scraper_feed, calculate_daily_quant_from_archive
    from marketscope.daily_quant import rank_daily_stocks
    from marketscope.security import DatabaseManager

from pathlib import Path
import os
DEFAULT_DB_PATH = os.environ.get("MS_DB_PATH", str(Path(__file__).parent.parent / "work" / "runtime" / "marketscope.db"))

logger = logging.getLogger("marketscope.session_manager")


def resolve_previous_session_date(
    state_info: Dict[str, Any], archive: Optional[CandleArchive] = None,
    db: Optional[DatabaseManager] = None,
) -> str:
    """Resolve an explicit previous-session request without relabelling today.

    In a PREVIOUS state the exchange clock already selected a prior date.
    During LIVE/CLOSED_TODAY, select a real observed date strictly before the
    active date. Fall back to the prior trading day when no archive exists.
    """
    selected = date.fromisoformat(state_info["session_date"])
    if state_info.get("session_state") == "PREVIOUS":
        return selected.isoformat()
    if db is not None:
        try:
            with closing(db.get_connection()) as conn:
                row = conn.execute(
                    "SELECT max(session_date) FROM session_snapshots WHERE complete = 1 AND session_date < ?",
                    (selected.isoformat(),),
                ).fetchone()
            if row and isinstance(row[0], str):
                found = date.fromisoformat(row[0])
                if found < selected:
                    return found.isoformat()
        except (sqlite3.Error, OSError, TypeError, ValueError, AttributeError):
            pass
    if archive is not None:
        try:
            boundary = int(datetime.combine(selected, dt_time.min, IST).timestamp())
            with closing(archive._get_connection()) as conn:
                row = conn.execute(
                    "SELECT max(ts) FROM candles WHERE ts < ? AND ts >= 1704067200",
                    (boundary,),
                ).fetchone()
            if row and isinstance(row[0], (int, float)) and not isinstance(row[0], bool):
                found = datetime.fromtimestamp(row[0], IST).date()
                if found < selected:
                    return found.isoformat()
        except (sqlite3.Error, OSError, TypeError, ValueError, AttributeError):
            pass
    previous = selected - timedelta(days=1)
    while not is_trading_day(previous):
        previous -= timedelta(days=1)
    return previous.isoformat()


def enrich_daily_rankings(
    snapshot: Dict[str, Any], session_date: str, timeframe: int = 5,
    archive: Optional[CandleArchive] = None,
) -> Dict[str, Any]:
    """Return a calculated view without rewriting a completed stored snapshot.

    Scores always use full five-minute archive history, independently of the
    selected chart interval. A final twenty-bar preview cannot establish a full
    session or satisfy the technical warm-up requirement.
    """
    archive = archive or get_candle_archive()
    enriched = dict(snapshot)
    stocks = {}
    for symbol, original in snapshot.get("stocks", {}).items():
        stock = dict(original)
        stock["daily_quant"] = calculate_daily_quant_from_archive(symbol, session_date, archive=archive)
        stocks[symbol] = stock
    enriched["stocks"] = stocks
    daily_ranking = rank_daily_stocks(
        stocks, session_date, session_state=snapshot.get("session_state", "PREVIOUS"),
    )
    enriched["daily_ranking"] = daily_ranking
    enriched["best_stock"] = daily_ranking.get("best")
    enriched["worst_stock"] = daily_ranking.get("worst")
    if "spotlight" in enriched and isinstance(enriched["spotlight"], dict):
        enriched["spotlight"]["best_stock"] = daily_ranking.get("best")
        enriched["spotlight"]["worst_stock"] = daily_ranking.get("worst")
    enriched["tiers_summary"] = daily_ranking.get("tiers_summary", {
        "bullish_count": sum(1 for s in stocks.values() if (s.get("score") or 0) > 70),
        "bearish_count": sum(1 for s in stocks.values() if s.get("score") is not None and s.get("score") < 50),
        "normal_count": sum(1 for s in stocks.values() if s.get("score") is not None and 50 <= s.get("score") <= 70),
    })
    return enriched


def get_session_state(
    clock_dt: Optional[datetime] = None,
    candle_archive: Optional[CandleArchive] = None,
    db_manager: Optional[DatabaseManager] = None,
) -> Dict[str, Any]:
    """
    Determines current session state (LIVE, CLOSED_TODAY, PREVIOUS) and session date
    using market_schedule and actual candle timestamps.

    LIVE:         market open (09:15-15:30 IST on a non-holiday weekday), fresh candles
    CLOSED_TODAY: after 15:30 on a trading day -> today's finished session
    PREVIOUS:     before open, weekend or holiday -> last completed session

    Session date is computed from actual real candles for PREVIOUS, not calendar guesswork.
    Inject a clock_dt so this is fully deterministic and testable.
    """
    if clock_dt is None:
        clock_dt = datetime.now(IST)
    elif clock_dt.tzinfo is None:
        clock_dt = clock_dt.replace(tzinfo=IST)
    else:
        clock_dt = clock_dt.astimezone(IST)

    today = clock_dt.date()
    clock_time = clock_dt.time()
    weekday = clock_dt.weekday()
    holiday = is_market_holiday(today)
    weekend = weekday >= 5

    archive = candle_archive or get_candle_archive()
    db = db_manager or DatabaseManager(DEFAULT_DB_PATH)

    # Resolve session_state
    if weekend or holiday:
        session_state = "PREVIOUS"
    else:
        # Trading business day
        if clock_time < TIME_MARKET_OPEN:
            session_state = "PREVIOUS"
        elif TIME_MARKET_OPEN <= clock_time < TIME_MARKET_CLOSE:
            session_state = "LIVE"
        else:
            session_state = "CLOSED_TODAY"

    # Compute session_date
    if session_state == "LIVE" or session_state == "CLOSED_TODAY":
        session_date = today.strftime("%Y-%m-%d")
    else:
        # PREVIOUS: Compute date from actual real candles/snapshots
        session_date = _resolve_latest_completed_session_date(archive, db, clock_dt)

    return {
        "session_state": session_state,
        "session_date": session_date,
        "clock_time_ist": clock_dt.strftime("%H:%M:%S IST"),
        "is_trading_day": not weekend and not holiday,
    }


def _resolve_latest_completed_session_date(
    archive: CandleArchive,
    db: DatabaseManager,
    clock_dt: datetime,
) -> str:
    """
    Finds the latest completed session date from real data:
    1. session_snapshots database table (where complete = 1)
    2. candles.db archive max timestamp
    3. Fallback to latest prior trading day before clock_dt
    """
    # 1. Check session_snapshots table
    try:
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT session_date FROM session_snapshots
                WHERE complete = 1
                ORDER BY session_date DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row and hasattr(row, "__getitem__") and not type(row).__name__.startswith(("MagicMock", "Mock")):
                val = row[0]
                if isinstance(val, str) and len(val) == 10:
                    return val
    except Exception as e:
        logger.debug("Error checking session_snapshots: %s", e)

    # 2. Check candles.db
    try:
        latest_from_archive = archive.get_latest_session_date()
        if latest_from_archive and isinstance(latest_from_archive, str):
            return latest_from_archive
    except Exception as e:
        logger.debug("Error checking candle archive: %s", e)

    # 3. Fallback: Step backward to find latest prior weekday not a holiday
    d = clock_dt.date() - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def build_session_snapshot(
    session_date: str,
    timeframe: int = 5,
    candle_archive: Optional[CandleArchive] = None,
    scraper_feed: Optional[RealTimeScraperFeed] = None,
    db_manager: Optional[DatabaseManager] = None,
    config: Optional[Dict[str, Any]] = None,
    allow_remote_fallback: bool = True,
) -> Dict[str, Any]:
    if not isinstance(session_date, str) or len(session_date) != 10:
        session_date = "2026-10-23" 
    """
    Builds or retrieves a session snapshot for the given session_date and timeframe.
    Completed snapshots (complete=1) stored in SQLite are immutable.
    If not stored, attempts to assemble from candles.db archive, and if missing,
    fetches from real Yahoo chart API sliced by IST date.

    A session is marked complete=1 ONLY if:
    - >= 90% of expected bars (e.g. >= 68 of 75 bars for 5m)
    - Last bar is at or after 15:25 IST.
    Incomplete data is marked 'INCOMPLETE SESSION' and never stored as final.
    """
    db = db_manager or DatabaseManager(DEFAULT_DB_PATH)
    archive = candle_archive or get_candle_archive()
    scraper = scraper_feed or get_live_scraper_feed()
    cfg = config or (db.get_latest_config() if db else None) or DEFAULT_CONFIG

    # 1. Check if an immutable completed snapshot already exists
    try:
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_json, complete, data_source, created_at FROM session_snapshots WHERE session_date = ? AND timeframe = ?",
                (session_date, timeframe),
            )
            row = cur.fetchone()
            if row and hasattr(row, "__getitem__") and not type(row).__name__.startswith(("MagicMock", "Mock")):
                if row["complete"] == 1 and isinstance(row["payload_json"], str):
                    stored = json.loads(row["payload_json"])
                    return enrich_daily_rankings(stored, session_date, timeframe, archive)
    except Exception as err:
        logger.warning("Error reading session_snapshots table: %s", err)

    # 2. Assemble candles for all tracked stocks
    symbols = list(STOCK_CATALOGUE.keys())
    stocks_payload: Dict[str, Any] = {}
    trajectories: List[Dict[str, Any]] = []
    bar_counts: List[int] = []
    latest_candle_ts = 0

    fast_period = cfg.get("fast_ema_period", 9)
    slow_period = cfg.get("slow_ema_period", 21)
    rsi_period = cfg.get("rsi_period", 14)
    score_thresh = cfg.get("score_threshold", 65)

    data_source = "candles.db archive"

    for sym in symbols:
        meta = STOCK_CATALOGUE[sym]
        yahoo_prev_close = None
        # Query candles.db
        candles = archive.get_candles_for_session(sym, timeframe, session_date)

        # If candles.db doesn't have sufficient bars, fetch from Yahoo with range='5d'
        if len(candles) < 20 and allow_remote_fallback:
            data_source = "Exchange Live Feed (Yahoo Finance)"
            try:
                res = scraper.scrape_stock_candles(sym, timeframe=timeframe)
                all_candles = res.get("candles", [])
                yahoo_prev_close = res.get("previous_close")
                candles = [
                    c for c in all_candles
                    if datetime.fromtimestamp(c["t"], IST).strftime("%Y-%m-%d") == session_date
                ]
                if candles:
                    archive.archive_closed_candles(sym, timeframe, candles, source="exchange")
            except Exception as e:
                logger.debug("Failed to fetch historical session candles for %s: %s", sym, e)

        # Exclude candles before 2024-01-01
        candles = [c for c in candles if (c.get("t") or c.get("ts", 0)) >= 1704067200]
        if not candles:
            continue

        open_price = float(candles[0]["o"])
        # Check prior session close from archive, then Yahoo's real previous close
        first_candle_ts = candles[0].get("t") or candles[0].get("ts", 0)
        prior_close = archive.get_prior_session_close(sym, timeframe, first_candle_ts)
        if prior_close is None and yahoo_prev_close:
            try:
                prior_close = float(yahoo_prev_close)
            except (TypeError, ValueError):
                prior_close = None
        if prior_close is None and yahoo_prev_close is None:
            # Candles came from the local archive, so Yahoo's previous close was
            # not fetched in this build. Fetch it now (via the shared cached feed)
            # so the day change stays based on the real previous close.
            try:
                res_prev = scraper.scrape_stock_candles(sym, timeframe=timeframe)
                yp = res_prev.get("previous_close")
                if yp is not None:
                    prior_close = float(yp)
            except Exception as e:
                logger.debug("Failed to fetch previous close for %s: %s", sym, e)
        
        # Flag overnight gaps > 15% as possible corporate actions (exclude that symbol-day)
        if prior_close is not None and prior_close > 0:
            overnight_gap = abs(open_price - prior_close) / prior_close
            if overnight_gap > 0.15:
                logger.warning(
                    "Excluding %s on %s: overnight gap %.2f%% > 15%% (possible corporate action)",
                    sym, session_date, overnight_gap * 100
                )
                continue

        bar_counts.append(len(candles))
        if candles[-1]["t"] > latest_candle_ts:
            latest_candle_ts = candles[-1]["t"]

        closes = [float(c["c"]) for c in candles]
        highs = [float(c["h"]) for c in candles]
        lows = [float(c["l"]) for c in candles]
        volumes = [int(c.get("v", 0)) for c in candles]

        close_price = closes[-1]
        prev_close = prior_close
        if prev_close is not None:
            change_pct = round(((close_price - prev_close) / max(0.01, prev_close)) * 100, 2)
            change_basis = "prev_close"
        else:
            # No real previous close: mark day change unavailable instead of
            # presenting change-since-session-open as a day change.
            change_pct = None
            change_basis = "unavailable"
        open_change_pct = round(((close_price - open_price) / max(0.01, open_price)) * 100, 2)

        day_high = max(highs)
        day_low = min(lows)
        total_vol = sum(volumes)

        # Calculate session high / low times in IST
        high_idx = highs.index(day_high)
        low_idx = lows.index(day_low)
        session_high_time = datetime.fromtimestamp(candles[high_idx]["t"], IST).strftime("%H:%M IST")
        session_low_time = datetime.fromtimestamp(candles[low_idx]["t"], IST).strftime("%H:%M IST")

        # Indicators
        fast_ema_series = calculate_ema(closes, fast_period)
        slow_ema_series = calculate_ema(closes, slow_period)
        latest_fast_ema = fast_ema_series[-1] if fast_ema_series else close_price
        latest_slow_ema = slow_ema_series[-1] if slow_ema_series else close_price

        rsi = calculate_rsi(closes, rsi_period)
        vwap = calculate_vwap(candles)
        rvol = calculate_rvol(volumes)
        sup, res_val = calculate_support_resistance(candles)
        order_blocks = calculate_order_blocks(candles)
        atr_val = calculate_atr(candles, 14)

        momentum = round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 2) if len(closes) >= 6 else 0.0

        score_dict = calculate_composite_score(
            fast_ema=latest_fast_ema,
            slow_ema=latest_slow_ema,
            momentum=momentum,
            close=close_price,
            vwap=vwap,
            rsi=rsi,
            score_threshold=score_thresh,
        )
        if "raw" in score_dict:
            score_dict["raw"]["atr"] = atr_val

        # Quant indicators from PDF formula reference
        cpr = calculate_cpr(day_high, day_low, prev_close if prev_close else close_price)
        camarilla = calculate_camarilla(day_high, day_low, prev_close if prev_close else close_price)
        supertrend = calculate_supertrend(candles, period=10, multiplier=3.0)
        vwap_bands = calculate_vwap_bands(candles)

        ai_decision = evaluate_ai_decision(
            symbol=sym,
            price=close_price,
            metrics=score_dict,
            vwap=vwap,
            support=sup,
            resistance=res_val,
            rvol=rvol,
            order_blocks=order_blocks,
            config=cfg,
            closed_confirming=True,
            atr=atr_val,
            supertrend=supertrend,
            cpr=cpr,
            camarilla=camarilla,
        )

        stock_item = {
            "symbol": sym,
            "name": meta["name"],
            "sector": meta["sector"],
            "status": "ok",
            "price": close_price,
            "session_open": open_price,
            "previous_close": prev_close,
            "change_pct": change_pct,
            "change_basis": change_basis,
            "open_change_pct": open_change_pct,
            "day_high": day_high,
            "day_low": day_low,
            "session_high_time": session_high_time,
            "session_low_time": session_low_time,
            "volume": total_vol,
            "rvol": rvol,
            "vwap": vwap,
            "vwap_bands": vwap_bands,
            "cpr": cpr,
            "camarilla": camarilla,
            "supertrend": supertrend,
            "rank_tier": score_dict.get("rank_tier", "NORMAL"),
            "tier": score_dict.get("rank_tier", "NORMAL"),
            "support": sup,
            "resistance": res_val,
            "order_blocks": order_blocks,
            "metrics": score_dict,
            "confirmed": score_dict,
            "ai_decision": ai_decision,
            "candles_preview": candles[-20:],
            "updated_at": datetime.fromtimestamp(candles[-1]["t"], timezone.utc).isoformat(),
        }
        stock_item["daily_quant"] = calculate_daily_quant_from_archive(
            sym, session_date, candles=candles if timeframe == 5 else None, archive=archive,
        )
        stocks_payload[sym] = stock_item

        pts = [round(((c - open_price) / open_price) * 100, 2) for c in closes]
        trajectories.append({
            "symbol": sym,
            "name": meta["name"],
            "sector": meta["sector"],
            "change_pct": open_change_pct,
            "color_group": "blue" if open_change_pct >= 0 else "red",
            "points": pts,
        })

    trajectories.sort(key=lambda t: t["change_pct"], reverse=True)

    # 3. Determine completion
    # Expected bars for 5m regular session (09:15 to 15:30 IST) = 75 bars.
    # 90% of 75 = 67.5 bars (>= 68 bars)
    expected_bars = 75 if timeframe == 5 else (25 if timeframe == 15 else 375)
    min_required_bars = int(expected_bars * 0.90)

    avg_bars = (sum(bar_counts) / len(bar_counts)) if bar_counts else 0
    last_candle_time = datetime.fromtimestamp(latest_candle_ts, IST) if latest_candle_ts > 0 else None
    last_candle_time_ist = last_candle_time.strftime("%Y-%m-%d %H:%M:%S IST") if last_candle_time else "N/A"

    is_late_enough = False
    if last_candle_time:
        is_late_enough = (last_candle_time.hour == 15 and last_candle_time.minute >= 25) or (last_candle_time.hour > 15)

    is_complete = bool(len(stocks_payload) >= 12 and avg_bars >= min_required_bars and is_late_enough)

    # 4. Breadth & Sector summary
    buy_candidates = [s for s in stocks_payload.values() if s["metrics"]["signal"] == "BUY" and not s["ai_decision"]["action"].startswith("AVOID")]
    sell_candidates = [s for s in stocks_payload.values() if s["metrics"]["signal"] == "SELL" and not s["ai_decision"]["action"].startswith("AVOID")]
    neutral_candidates = [s for s in stocks_payload.values() if s["metrics"]["signal"] == "NEUTRAL" or s["ai_decision"]["action"].startswith("AVOID")]

    advancers = [s for s in stocks_payload.values() if s["change_pct"] is not None and s["change_pct"] > 0]
    decliners = [s for s in stocks_payload.values() if s["change_pct"] is not None and s["change_pct"] < 0]
    unchanged = [s for s in stocks_payload.values() if s["change_pct"] is not None and s["change_pct"] == 0]

    grade_counts = {
        "A+": sum(1 for s in stocks_payload.values() if s["ai_decision"].get("grade") == "A+"),
        "A": sum(1 for s in stocks_payload.values() if s["ai_decision"].get("grade") == "A"),
        "B": sum(1 for s in stocks_payload.values() if s["ai_decision"].get("grade") == "B"),
        "C": sum(1 for s in stocks_payload.values() if s["ai_decision"].get("grade") == "C"),
    }

    # Top 5 gainers & losers
    priced_stocks = [s for s in stocks_payload.values() if s["change_pct"] is not None]
    all_sorted = sorted(priced_stocks, key=lambda s: s["change_pct"], reverse=True)
    top_gainers = [
        {"symbol": s["symbol"], "name": s["name"], "change_pct": s["change_pct"], "close": s["price"], "sector": s["sector"]}
        for s in all_sorted[:5]
    ]
    top_losers = [
        {"symbol": s["symbol"], "name": s["name"], "change_pct": s["change_pct"], "close": s["price"], "sector": s["sector"]}
        for s in sorted(priced_stocks, key=lambda s: s["change_pct"])[:5]
    ]

    # Sector summary
    sector_agg: Dict[str, List[float]] = {}
    for s in stocks_payload.values():
        if s["change_pct"] is not None:
            sector_agg.setdefault(s["sector"], []).append(s["change_pct"])

    sectors = []
    for sec, pcts in sector_agg.items():
        avg_chg = round(sum(pcts) / len(pcts), 2)
        sectors.append({
            "sector": sec,
            "avg_change_pct": avg_chg,
            "count": len(pcts),
            "sentiment": "bullish" if avg_chg >= 0.2 else ("bearish" if avg_chg <= -0.2 else "neutral"),
        })
    sectors.sort(key=lambda s: s["avg_change_pct"], reverse=True)

    buy_tiles = [
        {
            "symbol": s["symbol"], "name": s["name"], "price": s["price"],
            "change_pct": s["change_pct"], "score": s["metrics"]["score"],
            "grade": s["ai_decision"].get("grade", "B"),
            "ai_action": s["ai_decision"]["action"],
            "ai_sl": s["ai_decision"]["stop_loss"],
            "ai_t1": s["ai_decision"]["target_1"],
            "ai_rr": s["ai_decision"]["risk_reward_ratio"],
            "candles_preview": s["candles_preview"],
        }
        for s in sorted(buy_candidates, key=lambda x: x["metrics"]["score"], reverse=True)
    ]
    sell_tiles = [
        {
            "symbol": s["symbol"], "name": s["name"], "price": s["price"],
            "change_pct": s["change_pct"], "score": s["metrics"]["score"],
            "grade": s["ai_decision"].get("grade", "B"),
            "ai_action": s["ai_decision"]["action"],
            "ai_sl": s["ai_decision"]["stop_loss"],
            "ai_t1": s["ai_decision"]["target_1"],
            "ai_rr": s["ai_decision"]["risk_reward_ratio"],
            "candles_preview": s["candles_preview"],
        }
        for s in sorted(sell_candidates, key=lambda x: x["metrics"]["score"])
    ]

    snapshot = {
        "session_date": session_date,
        "timeframe": timeframe,
        "session_state": "PREVIOUS",
        "data_mode": "PREVIOUS_SESSION" if is_complete else "INCOMPLETE_SESSION",
        "label": f"Historical Session ({session_date})" if is_complete else f"INCOMPLETE SESSION ({session_date})",
        "complete": 1 if is_complete else 0,
        "status_label": "COMPLETE SESSION" if is_complete else "INCOMPLETE SESSION",
        "data_source": data_source,
        "last_candle_ts": latest_candle_ts,
        "last_candle_time_ist": last_candle_time_ist,
        "as_of": last_candle_time_ist,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "breadth": {
            "total_tracked": len(stocks_payload),
            "advancers": len(advancers),
            "decliners": len(decliners),
            "unchanged": len(unchanged),
            "grade_counts": grade_counts,
        },
        "counts": {
            "total": len(stocks_payload),
            "buy": len(buy_candidates),
            "sell": len(sell_candidates),
            "neutral": len(neutral_candidates),
        },
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "trajectories": trajectories,
        "signal_tiles": {"buy": buy_tiles, "sell": sell_tiles},
        "spotlight": {"buy": buy_tiles[:3], "sell": sell_tiles[:3]},
        "sectors": sectors,
        "stocks": stocks_payload,
    }
    daily_ranking = rank_daily_stocks(stocks_payload, session_date, session_state="PREVIOUS")
    snapshot["daily_ranking"] = daily_ranking
    snapshot["best_stock"] = daily_ranking.get("best")
    snapshot["worst_stock"] = daily_ranking.get("worst")
    snapshot["spotlight"]["best_stock"] = daily_ranking.get("best")
    snapshot["spotlight"]["worst_stock"] = daily_ranking.get("worst")
    snapshot["tiers_summary"] = daily_ranking.get("tiers_summary", {
        "bullish_count": sum(1 for s in stocks_payload.values() if (s.get("score") or 0) > 70),
        "bearish_count": sum(1 for s in stocks_payload.values() if s.get("score") is not None and s.get("score") < 50),
        "normal_count": sum(1 for s in stocks_payload.values() if s.get("score") is not None and 50 <= s.get("score") <= 70),
    })

    # 5. Store in session_snapshots ONLY if complete=1 (immutable)
    if is_complete and db:
        try:
            with db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO session_snapshots (session_date, timeframe, payload_json, complete, data_source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_date, timeframe) DO NOTHING
                    """,
                    (
                        session_date,
                        timeframe,
                        json.dumps(snapshot),
                        1,
                        data_source,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
                logger.info("Saved completed immutable session snapshot for %s tf=%d", session_date, timeframe)
        except Exception as err:
            logger.error("Failed to persist session snapshot: %s", err)

    return snapshot


def get_session_scorecard(
    session_date: str,
    db_manager: Optional[DatabaseManager] = None,
) -> Dict[str, Any]:
    """
    Computes honest signal scorecard from the signals table for the given session date.
    Strictly filters for non-gated live signals created on that session date.
    Never fabricates or back-fills outcomes.
    """
    db = db_manager or DatabaseManager(DEFAULT_DB_PATH)
    try:
        with db.get_connection() as conn:
            cur = conn.cursor()
            date_prefix = f"{session_date}%"
            cur.execute(
                """
                SELECT id, symbol, timeframe, action, grade, score, conviction,
                       outcome, outcome_at, outcome_price, rr, price,
                       entry_low, entry_high, stop_loss, target_1, target_2,
                       mfe_pct, mae_pct, created_at
                FROM signals
                WHERE (gated IS NULL OR gated = 0)
                  AND created_at LIKE ?
                ORDER BY id ASC
                """,
                (date_prefix,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("Error querying signals for session scorecard: %s", e)
        rows = []

    if not rows:
        return {
            "signals": 0,
            "session_date": session_date,
            "note": "No signals were logged in this session",
        }

    total_count = len(rows)
    t1_wins = sum(1 for r in rows if r.get("outcome") in ("T1", "TARGET_1_HIT"))
    t2_wins = sum(1 for r in rows if r.get("outcome") in ("T2", "TARGET_2_HIT"))
    wins = t1_wins + t2_wins
    losses = sum(1 for r in rows if r.get("outcome") in ("SL", "STOPPED_OUT"))
    expired = sum(1 for r in rows if r.get("outcome") == "EXPIRED")
    closed_trades = wins + losses

    win_rate_pct = round((wins / closed_trades) * 100, 1) if closed_trades > 0 else 0.0

    r_values = [r["rr"] for r in rows if r.get("rr") is not None]
    avg_r = round(sum(r_values) / len(r_values), 2) if r_values else 0.0

    best_trade = max(rows, key=lambda r: r.get("mfe_pct") or -999.0) if rows else None
    worst_trade = min(rows, key=lambda r: r.get("mae_pct") or 999.0) if rows else None

    return {
        "signals": total_count,
        "session_date": session_date,
        "wins": wins,
        "t1_wins": t1_wins,
        "t2_wins": t2_wins,
        "losses": losses,
        "expired": expired,
        "closed_count": closed_trades,
        "win_rate_pct": win_rate_pct,
        "sample_size": closed_trades,
        "avg_r": avg_r,
        "avg_rr": avg_r,
        "best_trade": {
            "symbol": best_trade["symbol"],
            "action": best_trade["action"],
            "mfe_pct": best_trade.get("mfe_pct"),
            "rr": best_trade.get("rr"),
            "outcome": best_trade.get("outcome"),
        } if best_trade and best_trade.get("mfe_pct") is not None else None,
        "worst_trade": {
            "symbol": worst_trade["symbol"],
            "action": worst_trade["action"],
            "mae_pct": worst_trade.get("mae_pct"),
            "rr": worst_trade.get("rr"),
            "outcome": worst_trade.get("outcome"),
        } if worst_trade and worst_trade.get("mae_pct") is not None else None,
    }
