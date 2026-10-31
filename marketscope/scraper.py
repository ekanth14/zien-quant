"""
MarketScope Real-Time NSE & BSE Live Scraper Engine
Scrapes real-time tick quotes, intraday candles (1m, 5m, 15m, 60m),
cumulative volume, day high/low, and VWAP directly from NSE and BSE.
Computes technical indicators, order blocks, and trade setups on verified live data.
Never fabricates or invents market data on failure.
"""

from __future__ import annotations
import json
import logging
import math
import os
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from decision_engine import evaluate_ai_decision
from market import (
    calculate_atr,
    calculate_bollinger_bandwidth,
    calculate_camarilla,
    calculate_composite_score,
    calculate_cpr,
    calculate_ema,
    calculate_order_blocks,
    calculate_rsi,
    calculate_rvol,
    calculate_supertrend,
    calculate_support_resistance,
    calculate_vwap,
    calculate_vwap_bands,
)
from stock_database import get_stock_database
from market_schedule import get_market_schedule_info
from market_schedule import IST
from daily_quant import analyze_daily_stock, rank_daily_stocks
from laya_ai import query_laya_decision

logger = logging.getLogger("marketscope.scraper")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Core monitored symbols across liquid NSE/BSE leaders
DEFAULT_MONITORED = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "TITAN",
    "BAJFINANCE", "TATASTEEL", "WIPRO", "HCLTECH", "NTPC",
    "POWERGRID", "SUNPHARMA", "ULTRACEMCO", "JSWSTEEL"
]

SECTOR_MAP = {
    "RELIANCE": ("Reliance Industries", "Energy"),
    "TCS": ("Tata Consultancy Services", "Technology"),
    "HDFCBANK": ("HDFC Bank", "Financials"),
    "INFY": ("Infosys", "Technology"),
    "ICICIBANK": ("ICICI Bank", "Financials"),
    "HINDUNILVR": ("Hindustan Unilever", "FMCG"),
    "ITC": ("ITC Limited", "FMCG"),
    "SBIN": ("State Bank of India", "Financials"),
    "BHARTIARTL": ("Bharti Airtel", "Telecom"),
    "KOTAKBANK": ("Kotak Mahindra Bank", "Financials"),
    "LT": ("Larsen & Toubro", "Capital Goods"),
    "AXISBANK": ("Axis Bank", "Financials"),
    "ASIANPAINT": ("Asian Paints", "Consumer"),
    "MARUTI": ("Maruti Suzuki", "Automobile"),
    "TITAN": ("Titan Company", "Consumer"),
    "BAJFINANCE": ("Bajaj Finance", "Financials"),
    "TATASTEEL": ("Tata Steel", "Metals"),
    "WIPRO": ("Wipro", "Technology"),
    "HCLTECH": ("HCL Technologies", "Technology"),
    "NTPC": ("NTPC Limited", "Utilities"),
    "POWERGRID": ("Power Grid Corp", "Utilities"),
    "SUNPHARMA": ("Sun Pharma", "Healthcare"),
    "ULTRACEMCO": ("UltraTech Cement", "Materials"),
    "JSWSTEEL": ("JSW Steel", "Metals"),
}


def calculate_daily_quant_from_archive(
    symbol: str, session_date: str, candles: Optional[List[Dict[str, Any]]] = None,
    archive: Any = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Calculate the daily score from fixed five-minute bars without extra requests.

    Chart intervals never change the daily ranking. Existing closed archive bars
    provide indicator warm-up; only the selected date supplies session inputs.
    """
    from candle_archive import get_candle_archive
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    boundary = datetime.strptime(session_date, "%Y-%m-%d").replace(tzinfo=IST)
    session_candles = candles
    history, prior = [], None
    try:
        archive = archive or get_candle_archive()
        if session_candles is None:
            session_candles = archive.get_candles_for_session(symbol, 5, session_date)
        history = list(archive.get_recent_candles_before(symbol, 5, int(boundary.timestamp()), limit=100) or [])
        prior = archive.get_prior_session_ohlc(symbol, 5, session_date)
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error) as exc:
        logger.warning("Daily formula archive inputs unavailable for %s: %s", symbol, exc)
        history, prior = [], None
    # Raw chart results can contain several dates. Only this date supplies
    # session observations; archived bars strictly before it supply warm-up.
    selected = []
    for candle in session_candles or []:
        try:
            raw_timestamp = candle.get("t", candle.get("ts"))
            if isinstance(raw_timestamp, bool):
                selected.append(candle)
                continue
            timestamp = float(raw_timestamp)
            if timestamp <= 0:
                selected.append(candle)
                continue
            if datetime.fromtimestamp(timestamp, IST).date() == boundary.date():
                selected.append(candle)
        except (TypeError, ValueError, OverflowError, OSError, AttributeError):
            # Preserve malformed selected input for the analyzer's explicit
            # rejection rather than silently removing a missing observation.
            selected.append(candle)
    # Warm-up may start partway through an older session, but it may not bridge
    # an unobserved in-session gap or an incomplete overnight boundary. Use only
    # the latest contiguous suffix ending at a regular session close. A partial
    # old archive must not invalidate an otherwise complete current session.
    regular_history = []
    for candle in history:
        stamp = int(candle["t"])
        dt = datetime.fromtimestamp(stamp, IST)
        market_open = dt.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = dt.replace(hour=15, minute=15, second=0, microsecond=0)
        if market_open.timestamp() <= stamp < market_close.timestamp() and (stamp - int(market_open.timestamp())) % 300 == 0:
            regular_history.append(candle)
    history = []
    def valid_warmup(candle):
        try:
            values = [candle[k] for k in ("o", "h", "l", "c", "v")]
            if any(isinstance(value, bool) for value in values):
                return False
            o, h, l, c, v = map(float, values)
            return all(math.isfinite(value) and value > 0 for value in (o,h,l,c,v)) and l <= min(o,c) <= max(o,c) <= h
        except (KeyError, TypeError, ValueError):
            return False
    if regular_history:
        last = datetime.fromtimestamp(regular_history[-1]["t"], IST)
        if last.hour == 15 and last.minute == 10 and valid_warmup(regular_history[-1]):
            history = [regular_history[-1]]
            for candle in reversed(regular_history[:-1]):
                if not valid_warmup(candle):
                    break
                left = datetime.fromtimestamp(candle["t"], IST)
                right = datetime.fromtimestamp(history[0]["t"], IST)
                adjacent = right.timestamp() - left.timestamp() == 300
                overnight = left.date() < right.date() and left.hour == 15 and left.minute == 10 and right.hour == 9 and right.minute == 15
                if not adjacent and not overnight:
                    break
                history.insert(0, candle)
    confirmed = []
    for original in history + selected:
        if not isinstance(original, dict):
            confirmed.append(original)
            continue
        candle = dict(original)
        try:
            timestamp = float(candle.get("t", candle.get("ts", 0)))
        except (TypeError, ValueError):
            confirmed.append(candle)
            continue
        # The upstream chart API marks its last bar open even after that session
        # has ended. Confirm it only after its five-minute interval elapsed.
        if timestamp > 0 and timestamp + 300 <= clock.timestamp():
            candle["closed"] = True
            if "is_closed" in candle:
                candle["is_closed"] = True
        confirmed.append(candle)
    result = analyze_daily_stock(
        confirmed, session_date, timeframe=5, now=clock,
        prior_session=prior if isinstance(prior, dict) else None,
        window_close="15:15",
    )
    result["warmup_candle_count"] = len(history)
    return result


class RealTimeScraperFeed:
    """
    High-speed multi-threaded scraper engine feeding live market data
    from the National Stock Exchange (NSE) and Bombay Stock Exchange (BSE).
    Strict data integrity: never fabricates dummy prices on failure.
    """

    def __init__(self, monitored_symbols: Optional[List[str]] = None) -> None:
        self.monitored_symbols = list(monitored_symbols or DEFAULT_MONITORED)
        self.db = get_stock_database()
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0.0
        self._cache_ttl_sec: float = 2.0
        self._candle_cache: Dict[str, Dict[str, Any]] = {}
        self._last_good_cache: Dict[str, Dict[str, Any]] = {}
        self._symbol_failures: Dict[str, Dict[str, Any]] = {}
        self._last_good_snapshot: Optional[Dict[str, Any]] = None

    def add_symbol_to_track(self, symbol: str) -> bool:
        clean = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        with self._lock:
            if clean not in self.monitored_symbols:
                self.monitored_symbols.append(clean)
                self._cache_time = 0.0  # Force immediate refresh
                return True
        return False

    def remove_symbol_to_track(self, symbol: str) -> bool:
        clean = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        with self._lock:
            if clean in self.monitored_symbols:
                self.monitored_symbols.remove(clean)
                self._cache_time = 0.0
                return True
        return False

    def _resolve_ticker(self, symbol: str, exchange: str = "NSE") -> str:
        clean = symbol.strip().upper()
        if clean.endswith(".NS") or clean.endswith(".BO"):
            return clean
        if exchange.upper() == "BSE":
            return f"{clean}.BO"
        return f"{clean}.NS"

    def _get_cached_or_failed(
        self,
        ticker: str,
        clean_symbol: str,
        exchange: str,
        timeframe: int,
        error_reason: str,
    ) -> Dict[str, Any]:
        """
        Evaluates last-good cache against market schedule.
        Under open market: allows cache up to 5 min old marked status='stale'.
        Under closed market: last session data is normal, not stale.
        Otherwise marks status='failed' with None price fields.
        """
        cache_key = f"{ticker}_{timeframe}"
        now = time.time()
        sched = get_market_schedule_info()
        is_mkt_open = sched.get("is_open", False)

        cached = self._last_good_cache.get(cache_key)
        if cached:
            age_s = now - cached["fetched_at"]
            if is_mkt_open:
                if age_s <= 300.0:  # Under 5 minutes old
                    data = dict(cached["data"])
                    data["status"] = "stale"
                    data["age_s"] = round(age_s, 1)
                    data["error"] = error_reason
                    return data
            else:
                # Closed market showing last session is normal, not stale
                data = dict(cached["data"])
                data["status"] = "ok"
                data["error"] = None
                return data

        # Older or no last-good data -> status="failed", price fields None
        return {
            "status": "failed",
            "error": error_reason,
            "fetched_at": now,
            "symbol": clean_symbol,
            "ticker": ticker,
            "exchange": exchange,
            "currency": "INR",
            "price": None,
            "previous_close": None,
            "day_high": None,
            "day_low": None,
            "timeframe": timeframe,
            "candles": [],
        }

    def scrape_stock_candles(self, symbol: str, timeframe: int = 5, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Scrapes real intraday candles for a stock directly from the exchange.
        Timeframe options: 1, 5, 15, 60.
        Implements retries with backoff on 429/5xx/timeout and circuit breaker pausing.
        Never fabricates dummy prices on failure.
        """
        if os.environ.get('MS_MARKET_PROVIDER', 'public').lower() == 'fyers':
            from fyers_live import fetch_stock
            return fetch_stock(symbol, timeframe, exchange)
        ticker = self._resolve_ticker(symbol, exchange)
        clean_symbol = ticker.split(".")[0]

        tf_map = {1: ("1m", "1d"), 5: ("5m", "1d"), 15: ("15m", "5d"), 60: ("60m", "1mo")}
        interval, range_str = tf_map.get(timeframe, ("5m", "1d"))

        cache_key = f"{ticker}_{timeframe}"
        now = time.time()

        cached_entry = self._candle_cache.get(cache_key)
        if cached_entry and (now - cached_entry["cached_at"]) < 2.0:
            return cached_entry["data"]

        # Circuit breaker: check if symbol paused due to repeated failures
        fail_info = self._symbol_failures.get(ticker, {"consecutive_failures": 0, "paused_until": 0.0})
        if now < fail_info.get("paused_until", 0.0):
            wait_rem = int(fail_info["paused_until"] - now)
            return self._get_cached_or_failed(
                ticker, clean_symbol, exchange, timeframe, f"Paused ({wait_rem}s remaining) due to consecutive failures"
            )

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_str}"
        delays = [0.5, 1.5]
        max_attempts = 3
        last_error = ""

        for attempt in range(max_attempts):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    raw_json = json.loads(resp.read().decode("utf-8"))
                    result_list = raw_json.get("chart", {}).get("result")
                    if not result_list:
                        err_obj = raw_json.get("chart", {}).get("error", {})
                        raise ValueError(err_obj.get("description") or "Empty chart result in response")

                    result_obj = result_list[0]
                    meta = result_obj.get("meta", {})
                    timestamps = result_obj.get("timestamp", [])
                    quote_indicators = result_obj.get("indicators", {}).get("quote", [{}])[0]

                    opens = quote_indicators.get("open", [])
                    highs = quote_indicators.get("high", [])
                    lows = quote_indicators.get("low", [])
                    closes = quote_indicators.get("close", [])
                    volumes = quote_indicators.get("volume", [])

                    candles: List[Dict[str, Any]] = []
                    observed_now = time.time()
                    for i in range(len(timestamps)):
                        o = opens[i] if i < len(opens) else None
                        h = highs[i] if i < len(highs) else None
                        l = lows[i] if i < len(lows) else None
                        c = closes[i] if i < len(closes) else None
                        v = volumes[i] if i < len(volumes) else 0

                        if None in (o, h, l, c):
                            continue

                        candles.append({
                            "t": timestamps[i],
                            "o": round(float(o), 2),
                            "h": round(float(h), 2),
                            "l": round(float(l), 2),
                            "c": round(float(c), 2),
                            "v": int(v or 0),
                            "closed": timestamps[i] + timeframe * 60 <= observed_now,
                        })

                    # If no valid candles were extracted, we do NOT invent any!
                    if not candles:
                        raise ValueError("No valid candlestick data available for ticker")

                    real_price = float(meta.get("regularMarketPrice") or candles[-1]["c"])
                    prev_meta = meta.get("chartPreviousClose") or meta.get("previousClose")
                    # Never fall back to the session open here: a missing previous
                    # close must stay None so day change is marked unavailable.
                    real_prev = float(prev_meta) if prev_meta is not None else None
                    real_high = float(meta.get("regularMarketDayHigh") or max(c["h"] for c in candles))
                    real_low = float(meta.get("regularMarketDayLow") or min(c["l"] for c in candles))

                    meta_data = {
                        "status": "ok",
                        "error": None,
                        "fetched_at": now,
                        "symbol": clean_symbol,
                        "ticker": ticker,
                        "exchange": meta.get("exchangeName", exchange),
                        "currency": meta.get("currency", "INR"),
                        "price": real_price,
                        "previous_close": real_prev,
                        "day_high": real_high,
                        "day_low": real_low,
                        "timeframe": timeframe,
                        "candles": candles,
                    }

                    # Reset circuit breaker on success
                    self._symbol_failures[ticker] = {"consecutive_failures": 0, "paused_until": 0.0}
                    self._last_good_cache[cache_key] = {"data": meta_data, "fetched_at": now}
                    self._candle_cache[cache_key] = {"cached_at": now, "data": meta_data}

                    # Archive real closed candles to candles.db (never stale)
                    try:
                        try:
                            from marketscope.candle_archive import get_candle_archive
                        except ImportError:
                            from candle_archive import get_candle_archive
                        get_candle_archive().archive_closed_candles(
                            clean_symbol, timeframe, candles, source="exchange"
                        )
                    except Exception as arc_err:
                        logger.warning("Failed to archive closed candles for %s: %s", clean_symbol, arc_err)

                    return meta_data

            except urllib.error.HTTPError as err:
                last_error = f"HTTP Error {err.code}: {err.reason}"
                is_retryable = err.code == 429 or (500 <= err.code < 600)
                if not is_retryable:
                    logger.warning("Non-retryable scraper HTTP error for %s: %s", ticker, last_error)
                    break
                logger.warning("Scraper HTTP error for %s: %s (attempt %d/%d)", ticker, last_error, attempt + 1, max_attempts)
                retry_after = err.headers.get("Retry-After") if err.headers else None
                wait_time = float(retry_after) if (retry_after and retry_after.isdigit()) else delays[min(attempt, len(delays) - 1)]
                time.sleep(wait_time)

            except (urllib.error.URLError, TimeoutError, socket.timeout) as err:
                last_error = f"Timeout or network error: {err}"
                logger.warning("Scraper network error for %s: %s (attempt %d/%d)", ticker, last_error, attempt + 1, max_attempts)
                if attempt < max_attempts - 1:
                    time.sleep(delays[attempt])

            except Exception as err:
                last_error = f"Parse/data error: {err}"
                logger.warning("Scraper candle error for %s: %s", ticker, last_error)
                break

        # All attempts failed: update consecutive failure count and check breaker
        consec = fail_info.get("consecutive_failures", 0) + 1
        paused = (now + 60.0) if consec >= 3 else 0.0
        self._symbol_failures[ticker] = {"consecutive_failures": consec, "paused_until": paused}

        return self._get_cached_or_failed(ticker, clean_symbol, exchange, timeframe, last_error)

    def _process_stock(
        self,
        symbol: str,
        timeframe: int = 5,
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[str, Dict[str, Any], List[float]]]:
        """
        Scrapes and calculates indicators only when data is valid ('ok').
        When data is failed or stale, excludes from scoring and generates an unavailable payload.
        """
        cfg = config or {}
        fast_period = cfg.get("fast_ema_period", 9)
        slow_period = cfg.get("slow_ema_period", 21)
        rsi_period = cfg.get("rsi_period", 14)
        score_thresh = cfg.get("score_threshold", 65)

        raw_data = self.scrape_stock_candles(symbol, timeframe=timeframe)
        status = raw_data.get("status", "ok" if raw_data.get("candles") else "failed")
        candles = raw_data.get("candles", [])

        db_info = self.db.get_stock(symbol)
        if db_info:
            name = db_info["name"]
            sector = db_info["sector"]
        else:
            name, sector = SECTOR_MAP.get(symbol, (symbol, "General"))

        # Exclude stocks with status != "ok" from composite scoring, decision engine, etc.
        if status != "ok" or not candles or raw_data.get("price") is None:
            err_msg = raw_data.get("error") or "DATA UNAVAILABLE"
            unavailable_payload = {
                "symbol": symbol,
                "name": name,
                "sector": sector,
                "exchange": raw_data.get("exchange", "NSE/BSE"),
                "status": status,
                "error": err_msg,
                "price": None,
                "score": None,
                "signal": "UNAVAILABLE",
                "action": "DATA UNAVAILABLE",
                "previous_close": None,
                "session_open": None,
                "change_pct": None,
                "open_change_pct": None,
                "day_high": None,
                "day_low": None,
                "volume": None,
                "rvol": None,
                "vwap": None,
                "support": None,
                "resistance": None,
                "order_blocks": [],
                "metrics": {"score": None, "signal": "UNAVAILABLE", "components": {}, "raw": {}},
                "confirmed": {"score": None, "signal": "UNAVAILABLE", "components": {}, "raw": {}},
                "ai_decision": {
                    "action": "DATA UNAVAILABLE",
                    "signal_type": "NEUTRAL",
                    "conviction_pct": 0,
                    "grade": "C",
                    "rationale": f"Data unavailable: {err_msg}",
                    "factors": [f"Status: {status}", f"Reason: {err_msg}"],
                },
                "candles_preview": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            return symbol, unavailable_payload, []

        closes = [float(c.get("c", c.get("close", 0.0))) for c in candles]
        current_price = raw_data["price"]
        # Only a real prior close counts; never pose the first session candle as "previous close".
        prev_close = raw_data.get("previous_close")
        session_open = candles[0].get("o", candles[0].get("open", current_price)) if candles else current_price

        if prev_close:
            change_pct = round(((current_price - prev_close) / max(0.01, prev_close)) * 100, 2)
            change_basis = "prev_close"
        else:
            prev_close = None
            change_pct = None
            change_basis = "unavailable"
        open_change_pct = round(((current_price - session_open) / max(0.01, session_open)) * 100, 2)
        volume = sum(c.get("v", c.get("volume", 0)) for c in candles)

        fast_ema_series = calculate_ema(closes, fast_period)
        slow_ema_series = calculate_ema(closes, slow_period)
        latest_fast_ema = fast_ema_series[-1] if fast_ema_series else current_price
        latest_slow_ema = slow_ema_series[-1] if slow_ema_series else current_price
        rsi = calculate_rsi(closes, rsi_period)
        vwap = calculate_vwap(candles)
        candle_volumes = [float(c.get("v", c.get("volume", 0))) for c in candles]
        rvol = calculate_rvol(candle_volumes)
        sup, res = calculate_support_resistance(candles)
        order_blocks = calculate_order_blocks(candles)

        if len(closes) >= 6:
            momentum = round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 2)
        else:
            momentum = 0.0

        score_dict = calculate_composite_score(
            fast_ema=latest_fast_ema,
            slow_ema=latest_slow_ema,
            momentum=momentum,
            close=current_price,
            vwap=vwap,
            rsi=rsi,
            score_threshold=score_thresh,
        )

        atr_val = calculate_atr(candles, 14)
        if "raw" in score_dict:
            score_dict["raw"]["atr"] = atr_val

        ai_decision = evaluate_ai_decision(
            symbol=symbol,
            price=current_price,
            metrics=score_dict,
            vwap=vwap,
            support=sup,
            resistance=res,
            rvol=rvol,
            order_blocks=order_blocks,
            config=cfg,
            atr=atr_val,
        )

        trajectory_pts: List[float] = []
        for c in candles:
            close_val = float(c.get("c", c.get("close", session_open)))
            pct = round(((close_val - session_open) / max(0.01, session_open)) * 100, 2)
            trajectory_pts.append(pct)

        day_high = raw_data.get("day_high") or (max(float(c.get("h", c.get("high", current_price))) for c in candles) if candles else current_price)
        day_low = raw_data.get("day_low") or (min(float(c.get("l", c.get("low", current_price))) for c in candles) if candles else current_price)

        # Quant indicator suite from PDF formula reference
        cpr = calculate_cpr(day_high, day_low, prev_close if prev_close else current_price)
        camarilla = calculate_camarilla(day_high, day_low, prev_close if prev_close else current_price)
        supertrend = calculate_supertrend(candles, period=10, multiplier=3.0)
        vwap_bands = calculate_vwap_bands(candles)

        # Laya AI System 1 decision query
        laya_decision = query_laya_decision(
            symbol=symbol,
            stock_data={
                "price": current_price,
                "score": score_dict.get("score"),
                "vwap": vwap,
                "rsi": rsi,
                "rvol": rvol,
                "change_pct": change_pct or 0.0,
            },
            timeout=1.0,
        )

        ai_decision = evaluate_ai_decision(
            symbol=symbol,
            price=current_price,
            metrics=score_dict,
            vwap=vwap,
            support=sup,
            resistance=res,
            rvol=rvol,
            order_blocks=order_blocks,
            config=cfg,
            atr=atr_val,
            supertrend=supertrend,
            cpr=cpr,
            camarilla=camarilla,
            laya_decision=laya_decision,
        )

        trajectory_pts: List[float] = []
        for c in candles:
            close_val = float(c.get("c", c.get("close", session_open)))
            pct = round(((close_val - session_open) / max(0.01, session_open)) * 100, 2)
            trajectory_pts.append(pct)

        stock_payload = {
            "symbol": symbol,
            "name": name,
            "sector": sector,
            "exchange": raw_data.get("exchange", "NSE/BSE"),
            "status": "ok",
            "source": raw_data.get("source", "yahoo"),
            "quote_timestamp": raw_data.get("quote_timestamp"),
            "delay_status": raw_data.get("delay_status", "Provider delay unknown"),
            "price": current_price,
            "score": score_dict.get("score"),
            "signal": score_dict.get("signal"),
            "rank_tier": score_dict.get("rank_tier", "NORMAL"),
            "tier": score_dict.get("rank_tier", "NORMAL"),
            "previous_close": prev_close,
            "session_open": session_open,
            "change_pct": change_pct,
            "change_basis": change_basis,
            "open_change_pct": open_change_pct,
            "day_high": day_high,
            "day_low": day_low,
            "volume": volume,
            "rvol": rvol,
            "vwap": vwap,
            "vwap_bands": vwap_bands,
            "cpr": cpr,
            "camarilla": camarilla,
            "supertrend": supertrend,
            "support": sup,
            "resistance": res,
            "order_blocks": order_blocks,
            "metrics": score_dict,
            "confirmed": score_dict,
            "ai_decision": ai_decision,
            "laya_decision": laya_decision,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        session_date = datetime.now(IST).date().isoformat()
        stock_payload["daily_quant"] = calculate_daily_quant_from_archive(
            symbol, session_date, candles=candles if timeframe == 5 else None,
        )

        return symbol, stock_payload, trajectory_pts

    def get_snapshot(
        self,
        timeframe: int = 5,
        config: Optional[Dict[str, Any]] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetches snapshot across all monitored stocks concurrently.
        Caches results for 2.0s. Excludes status!='ok' stocks from rankings and breadth.
        """
        now = time.time()
        with self._lock:
            if not force_refresh and self._cache and (now - self._cache_time) < self._cache_ttl_sec:
                return self._cache

        symbols_to_scrape = list(self.monitored_symbols)
        stocks: Dict[str, Any] = {}
        trajectories: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=min(12, len(symbols_to_scrape))) as executor:
            futures = [executor.submit(self._process_stock, sym, timeframe, config) for sym in symbols_to_scrape]
            for fut in futures:
                try:
                    res = fut.result()
                    if res:
                        sym, stock_payload, traj_pts = res
                        stocks[sym] = stock_payload
                        if stock_payload.get("status") == "ok":
                            trajectories.append({
                                "symbol": sym,
                                "name": stock_payload["name"],
                                "sector": stock_payload["sector"],
                                "change_pct": stock_payload.get("open_change_pct", 0.0),
                                "color_group": "blue" if (stock_payload.get("open_change_pct") or 0) >= 0 else "red",
                                "points": traj_pts,
                            })
                except Exception as err:
                    logger.error("Error processing stock scraper worker: %s", err)

        trajectories.sort(key=lambda t: t["change_pct"], reverse=True)

        ok_stocks = {s["symbol"]: s for s in stocks.values() if s.get("status") == "ok"}
        stale_stocks = {s["symbol"]: s for s in stocks.values() if s.get("status") == "stale"}
        failed_stocks = {s["symbol"]: s for s in stocks.values() if s.get("status") == "failed"}

        ok_count = len(ok_stocks)
        stale_count = len(stale_stocks)
        failed_count = len(failed_stocks)

        # Breadth and ranking from ok_stocks only
        buy_count = sum(1 for s in ok_stocks.values() if s["metrics"].get("signal") == "BUY")
        sell_count = sum(1 for s in ok_stocks.values() if s["metrics"].get("signal") == "SELL")
        neutral_count = ok_count - buy_count - sell_count

        # Build-up lists excluding poor R:R / AVOID setups (Phase 2 gate)
        buy_tiles = [
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "sector": s["sector"],
                "price": s["price"],
                "change_pct": s["change_pct"],
                "score": s["metrics"]["score"],
                "grade": s["ai_decision"].get("grade", "B"),
                "ai_action": s["ai_decision"]["action"],
                "ai_sl": s["ai_decision"]["stop_loss"],
                "ai_t1": s["ai_decision"]["target_1"],
                "ai_rr": s["ai_decision"]["risk_reward_ratio"],
            }
            for s in sorted(ok_stocks.values(), key=lambda x: x["metrics"]["score"] or 0, reverse=True)
            if s["metrics"].get("signal") == "BUY" and not s["ai_decision"]["action"].startswith("AVOID")
        ]

        sell_tiles = [
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "sector": s["sector"],
                "price": s["price"],
                "change_pct": s["change_pct"],
                "score": s["metrics"]["score"],
                "grade": s["ai_decision"].get("grade", "B"),
                "ai_action": s["ai_decision"]["action"],
                "ai_sl": s["ai_decision"]["stop_loss"],
                "ai_t1": s["ai_decision"]["target_1"],
                "ai_rr": s["ai_decision"]["risk_reward_ratio"],
            }
            for s in sorted(ok_stocks.values(), key=lambda x: x["metrics"]["score"] or 100)
            if s["metrics"].get("signal") == "SELL" and not s["ai_decision"]["action"].startswith("AVOID")
        ]

        spotlight = {
            "buy": buy_tiles[:3],
            "sell": sell_tiles[:3],
        }

        # Sector rollup from ok_stocks only
        sector_agg: Dict[str, List[float]] = {}
        for s in ok_stocks.values():
            sec = s["sector"]
            if s.get("change_pct") is not None:
                sector_agg.setdefault(sec, []).append(s["change_pct"])

        sectors = []
        for sec, pcts in sector_agg.items():
            avg_chg = round(sum(pcts) / len(pcts), 2)
            sectors.append({
                "sector": sec,
                "avg_change_pct": avg_chg,
                "count": len(pcts),
                "sentiment": "bullish" if avg_chg >= 0.2 else "bearish" if avg_chg <= -0.2 else "neutral",
            })
        sectors.sort(key=lambda s: s["avg_change_pct"], reverse=True)

        if failed_count == 0 and stale_count == 0 and ok_count > 0:
            feed_status = "LIVE"
        elif ok_count > 0:
            feed_status = "DEGRADED"
        else:
            feed_status = "FEED DOWN"

        feed_block = {
            "ok_count": ok_count,
            "stale_count": stale_count,
            "failed_count": failed_count,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "status": feed_status,
        }

        now_iso = datetime.now(timezone.utc).isoformat()
        snapshot = {
            "timestamp": now_iso,
            "timeframe": timeframe,
            "mode": "live",
            "data_mode": "LIVE",
            "provider": "FYERS REST polling" if os.environ.get('MS_MARKET_PROVIDER', 'public').lower() == 'fyers' else "Yahoo public candle feed",
            "feed": feed_block,
            "counts": {
                "total": len(stocks),
                "ok": ok_count,
                "stale": stale_count,
                "failed": failed_count,
                "buy": buy_count,
                "sell": sell_count,
                "neutral": neutral_count,
            },
            "breadth": {
                "tracked": ok_count,
                "advances": buy_count,
                "declines": sell_count,
                "neutral": neutral_count,
            },
            "market_schedule": get_market_schedule_info(),
            "trajectories": trajectories,
            "signal_tiles": {
                "buy": buy_tiles,
                "sell": sell_tiles,
            },
            "spotlight": spotlight,
            "sectors": sectors,
            "stocks": stocks,
        }
        session_date = datetime.now(IST).date().isoformat()
        daily_ranking = rank_daily_stocks(stocks, session_date, session_state="LIVE")
        snapshot["daily_ranking"] = daily_ranking
        snapshot["best_stock"] = daily_ranking.get("best")
        snapshot["worst_stock"] = daily_ranking.get("worst")
        snapshot["spotlight"]["best_stock"] = daily_ranking.get("best")
        snapshot["spotlight"]["worst_stock"] = daily_ranking.get("worst")
        snapshot["tiers_summary"] = daily_ranking.get("tiers_summary", {
            "bullish_count": sum(1 for s in ok_stocks.values() if (s.get("score") or 0) > 70),
            "bearish_count": sum(1 for s in ok_stocks.values() if s.get("score") is not None and s.get("score") < 50),
            "normal_count": sum(1 for s in ok_stocks.values() if s.get("score") is not None and 50 <= s.get("score") <= 70),
        })

        with self._lock:
            self._cache = snapshot
            self._cache_time = now
            if ok_count > 0:
                self._last_good_snapshot = {"data": snapshot, "saved_at": now}

        return snapshot

    def get_full_stock_candles(self, symbol: str, timeframe: int = 5, exchange: str = "NSE") -> List[Dict[str, Any]]:
        """Returns verified real candlesticks for chart rendering. Never fake."""
        res = self.scrape_stock_candles(symbol, timeframe=timeframe, exchange=exchange)
        return res.get("candles", [])


# Global real-time scraper instance
_live_scraper: Optional[RealTimeScraperFeed] = None


def get_live_scraper_feed() -> RealTimeScraperFeed:
    global _live_scraper
    if _live_scraper is None:
        _live_scraper = RealTimeScraperFeed()
    return _live_scraper
