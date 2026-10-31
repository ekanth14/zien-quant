"""Build a small, request-time view of data already stored by the website."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError, OverflowError):
        return None


def _show(value: Any, digits: int = 2, suffix: str = "") -> str:
    number = _number(value)
    return f"{number:.{digits}f}{suffix}" if number is not None else "not recorded"


def _session_candle_summary(symbol: str, session_date: str, candles: list[dict]) -> str:
    if not candles:
        return ""
    first, last = candles[0], candles[-1]
    high = max(candles, key=lambda candle: candle["h"])
    low = min(candles, key=lambda candle: candle["l"])
    high_time = datetime.fromtimestamp(high["t"], IST).strftime("%H:%M")
    low_time = datetime.fromtimestamp(low["t"], IST).strftime("%H:%M")
    segments = []
    periods = ((555, 570, "09:15–09:30"), (570, 580, "09:30–09:40"),
               (580, 600, "09:40–10:00"), (600, 720, "10:00–12:00"),
               (720, 930, "12:00–15:30"))
    for start, end, label in periods:
        rows = [candle for candle in candles if start <= datetime.fromtimestamp(candle["t"], IST).hour * 60 + datetime.fromtimestamp(candle["t"], IST).minute < end]
        if not rows:
            continue
        opening, closing = rows[0]["o"], rows[-1]["c"]
        change = (closing / opening - 1) * 100 if opening else 0
        segments.append(f"{label} {len(rows)} bars O {_show(opening)} H {_show(max(c['h'] for c in rows))} L {_show(min(c['l'] for c in rows))} C {_show(closing)} Δ {change:+.2f}%")
    path = "; ".join(segments) or "no intraday windows recorded"
    return (f"RECORDED 5-MINUTE SESSION FOR {symbol} ({session_date}): {len(candles)} archived candles; "
            f"first open ₹{_show(first['o'])}; last close ₹{_show(last['c'])}; "
            f"high ₹{_show(high['h'])} at {high_time} IST; low ₹{_show(low['l'])} at {low_time} IST; "
            f"volume sum {sum(candle.get('v', 0) for candle in candles)}. Path: {path}. "
            "This is the recorded candle archive, not a tick-by-tick tape.")


def _archived_market_summary(session_date: str, catalogue: list[tuple[str, str]]) -> str:
    """Summarize all recorded NSE website symbols for a completed session."""
    try:
        day = date.fromisoformat(session_date)
        start = int(datetime.combine(day, time.min, IST).timestamp())
        end = int(datetime.combine(day + timedelta(days=1), time.min, IST).timestamp())
        from candle_archive import get_candle_archive
        archive = get_candle_archive()
        with archive._get_connection() as connection:
            rows = connection.execute("""
                WITH ranked AS (
                    SELECT symbol,ts,o,h,l,c,v,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY ts ASC) AS first_bar,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY ts DESC) AS last_bar
                    FROM candles WHERE timeframe=5 AND ts>=? AND ts<?
                )
                SELECT symbol,COUNT(*) AS bars,MIN(ts) AS first_ts,MAX(ts) AS last_ts,
                       MAX(h) AS high,MIN(l) AS low,SUM(v) AS volume,
                       MAX(CASE WHEN first_bar=1 THEN o END) AS open,
                       MAX(CASE WHEN last_bar=1 THEN c END) AS close
                FROM ranked GROUP BY symbol
            """, (start, end)).fetchall()
        allowed = {str(symbol).upper() for symbol, _ in catalogue}
        records = []
        for row in rows:
            if str(row["symbol"]).upper() not in allowed or not row["open"] or not row["close"]:
                continue
            change = (float(row["close"]) / float(row["open"]) - 1) * 100
            records.append({**dict(row), "change": change})
        if not records:
            return f"FULL CATALOGUE CANDLE COVERAGE: no 5-minute archive rows were recorded for website companies on {session_date}."
        gainers = sorted(records, key=lambda row: row["change"], reverse=True)[:5]
        losers = sorted(records, key=lambda row: row["change"])[:5]
        return (f"FULL CATALOGUE 5-MINUTE ARCHIVE ({session_date}): {len(records)} of {len(allowed)} catalogue companies had recorded bars; "
                f"change is measured from that session's first open to last close. Top: "
                + "; ".join(f"{row['symbol']} {row['change']:+.2f}% ({row['bars']} bars, high ₹{_show(row['high'])}, low ₹{_show(row['low'])})" for row in gainers)
                + ". Lowest: "
                + "; ".join(f"{row['symbol']} {row['change']:+.2f}% ({row['bars']} bars)" for row in losers)
                + ". This is only the site's archived 5-minute data, not a tick-by-tick tape.")
    except Exception:
        return ""


def _resolve_symbols(query: str, selected: str | None, catalogue: list[tuple[str, str]]) -> list[str]:
    query_words = set(re.findall(r"[a-z0-9]+", query.casefold()))
    query_norm = " ".join(re.findall(r"[a-z0-9]+", query.casefold()))
    ticker_words = {word.upper() for word in re.findall(r"(?<![A-Za-z0-9])[A-Za-z0-9&._-]{2,24}(?![A-Za-z0-9])", query)}
    ranked = []
    for symbol, company in catalogue:
        symbol = str(symbol).strip().upper()
        company = str(company or symbol).strip()
        if symbol in ticker_words:
            ranked.append((0, symbol))
            continue
        words = [word for word in re.findall(r"[a-z0-9]+", company.casefold()) if len(word) > 2 and word not in {"limited", "ltd", "company", "corporation", "industries", "india"}]
        matched = [word for word in words if word in query_words]
        if company.casefold() in query_norm or (len(words) > 1 and len(matched) >= 2):
            ranked.append((1 - len(matched) / max(1, len(words)), symbol))
        elif len(matched) == 1 and len(matched[0]) >= 5:
            ranked.append((1.5, symbol))
    ranked.sort()
    candidates = [symbol for _, symbol in ranked[:5]]
    if selected:
        symbol = str(selected).strip().upper()
        if re.fullmatch(r"[A-Z0-9&._-]{1,30}", symbol):
            candidates = [symbol] + [item for item in candidates if item != symbol]
    return candidates[:5]


def build_laya_website_context(
    *, query: str, snapshot: dict | None, selected_symbol: str | None,
    session_state: str, session_date: str | None, timeframe: int,
    config: dict, db_manager: Any, watchlist: list[str] | None = None,
) -> dict:
    """Retrieve focused market, journal, results and site-methodology context.

    Reads local application stores only. It does not expand the website's stock
    universe or fetch a new quote on behalf of a chat request.
    """
    lines = [
        "WEBSITE: Zien Quant personal NSE/BSE research terminal.",
        f"MARKET SESSION: {session_state}; session date {session_date or 'not available'}; requested timeframe {timeframe} minutes.",
        "The market session can be closed while research chat remains available. Never call a stored close a live quote.",
        "INTRADAY COVERAGE: Laya reads the candles and signal-journal rows that this website actually recorded. The journal stores qualified, deduplicated setups; candle history is bar data, not every exchange tick. Missing bars or periods stay missing and must not be filled by guesswork.",
        "SITE AREAS: market overview, tracked-company scanner and watchlist, chart/Flow River, signal methodology, NSE results and reactions, and Zerodha MIS leverage reference.",
        "SIGNAL METHOD: deterministic EMA trend, momentum, VWAP distance and RSI components form a configurable weighted score; RSI period, EMA periods, score cutoffs and minimum reward/risk are in the owner configuration. Signals are research labels, not instructions to place orders.",
        "FORMULAS: EMA update uses alpha=2/(period+1); momentum is 100*(latest close/close five bars earlier-1); RSI uses Wilder smoothing; candle VWAP is sum(((H+L+C)/3)*volume)/sum(volume); RVOL is latest bar volume divided by the mean of up to 20 prior bars. The composite score is round(50 + weighted, normalized and clamped component sum/2); default component weights are trend 35, momentum 30, VWAP 20, RSI 15. The configured score threshold sets BUY at/above threshold and SELL at/below 100-threshold.",
        "RESULTS METHOD: the results ledger uses company filings and available financial comparisons from 6 May 2023 onward; reaction findings are shown only where sourced candle observations exist.",
    ]
    for key, label in (("fast_ema_period", "Fast EMA"), ("slow_ema_period", "Slow EMA"), ("rsi_period", "RSI period"),
                       ("score_threshold", "Score threshold"), ("score_breakout_long", "Breakout-long score"),
                       ("score_pullback_long", "Pullback-long score"), ("score_breakdown_short", "Breakdown-short score"),
                       ("score_pullback_short", "Pullback-short score"), ("min_rr_hurdle", "Minimum reward/risk"),
                       ("grade_aplus_rr", "A+ reward/risk minimum"), ("grade_aplus_rvol", "A+ relative-volume minimum")):
        if config.get(key) is not None:
            lines.append(f"CONFIG {label}: {config[key]}.")
    weights = {key.removeprefix("weight_"): value for key, value in config.items() if key.startswith("weight_")}
    if weights:
        lines.append("CONFIG score weights: " + ", ".join(f"{key} {value}" for key, value in sorted(weights.items())) + ".")
    normalizers = {key.removeprefix("norm_"): value for key, value in config.items() if key.startswith("norm_")}
    if normalizers:
        lines.append("CONFIG score normalizers: " + ", ".join(f"{key} {value}" for key, value in sorted(normalizers.items())) + ".")

    stocks = snapshot.get("stocks", {}) if isinstance(snapshot, dict) else {}
    if not isinstance(stocks, dict):
        stocks = {}
    if snapshot:
        lines.append(
            f"MARKET SNAPSHOT: source {snapshot.get('data_source') or snapshot.get('source') or 'not labelled'}; "
            f"data mode {snapshot.get('data_mode') or 'not labelled'}; as of {snapshot.get('last_candle_time_ist') or snapshot.get('as_of') or snapshot.get('timestamp') or 'not supplied'}; "
            f"complete-session flag {snapshot.get('complete', 'not supplied')}; {len(stocks)} stocks in this snapshot."
        )
    else:
        lines.append("MARKET SNAPSHOT: no usable quote snapshot was returned for this request.")

    valid_rows = []
    for key, row in stocks.items():
        if isinstance(row, dict) and row.get("status", "ok") in ("ok", "stale", None):
            change = _number(row.get("change_pct", row.get("open_change_pct")))
            if change is not None:
                valid_rows.append((str(key).upper(), row, change))
    if valid_rows:
        advancers = sum(change > 0 for _, _, change in valid_rows)
        decliners = sum(change < 0 for _, _, change in valid_rows)
        unchanged = len(valid_rows) - advancers - decliners
        lines.append(f"SNAPSHOT BREADTH: {advancers} up, {decliners} down, {unchanged} unchanged of {len(valid_rows)} stocks with a usable change value.")
        gainers = sorted(valid_rows, key=lambda item: item[2], reverse=True)[:5]
        losers = sorted(valid_rows, key=lambda item: item[2])[:5]
        lines.append("TOP MOVERS BY SNAPSHOT CHANGE: " + "; ".join(f"{s} {_show(r.get('change_pct', r.get('open_change_pct')), suffix='%')} at ₹{_show(r.get('price'))}" for s, r, _ in gainers) + ".")
        lines.append("LOWEST MOVERS BY SNAPSHOT CHANGE: " + "; ".join(f"{s} {_show(r.get('change_pct', r.get('open_change_pct')), suffix='%')} at ₹{_show(r.get('price'))}" for s, r, _ in losers) + ".")
        signal_rows = []
        for sym, row, _ in valid_rows:
            decision = row.get("ai_decision") or row.get("decision") or {}
            action = str(decision.get("action") or "")
            if action and "AVOID" not in action.upper():
                signal_rows.append((sym, row, decision))
        if signal_rows:
            lines.append("NON-AVOID SNAPSHOT SETUPS: " + "; ".join(f"{sym} {str((row.get('ai_decision') or row.get('decision') or {}).get('action'))} grade {str((row.get('ai_decision') or row.get('decision') or {}).get('grade', '—'))} score {_show((row.get('metrics') or {}).get('score', row.get('score')), 0)}" for sym, row, _ in sorted(signal_rows, key=lambda item: _number((item[1].get('metrics') or {}).get('score', item[1].get('score'))) or 0, reverse=True)[:8]) + ".")

    try:
        from stock_database import get_stock_database
        catalogue = get_stock_database().get_company_symbols()
    except Exception:
        catalogue = []
    mentioned_symbols = _resolve_symbols(query, None, catalogue)
    symbols = _resolve_symbols(query, selected_symbol, catalogue)
    query_lower = query.casefold()
    if session_date and session_state != "LIVE" and not mentioned_symbols and any(term in query_lower for term in ("market", "whole", "overall", "entire", "all", "today", "day")):
        market_summary = _archived_market_summary(session_date, catalogue)
        if market_summary:
            lines.append(market_summary)
    if "watchlist" in query.casefold() or "my stocks" in query.casefold():
        for raw in (watchlist if isinstance(watchlist, list) else [])[:10]:
            watched = str(raw).strip().upper()
            if re.fullmatch(r"[A-Z0-9&._-]{1,30}", watched) and watched not in symbols:
                symbols.append(watched)
    symbols = symbols[:10]
    company_names = {str(symbol).upper(): str(name) for symbol, name in catalogue}
    if symbols:
        for symbol in symbols:
            row = stocks.get(symbol)
            if not isinstance(row, dict):
                row = next((value for key, value in stocks.items() if str(key).upper() == symbol and isinstance(value, dict)), None)
            if not row:
                lines.append(f"COMPANY CATALOGUE: {symbol} is {company_names.get(symbol, 'in the website catalogue')}; no current-session snapshot row was returned for it.")
                if session_date:
                    try:
                        from candle_archive import get_candle_archive
                        candles = get_candle_archive().get_candles_for_session(symbol, 5, session_date)
                        if candles:
                            lines.append(_session_candle_summary(symbol, session_date, candles))
                    except Exception:
                        pass
                continue
            metrics = row.get("metrics") or {}
            raw = metrics.get("raw") or {}
            decision = row.get("ai_decision") or row.get("decision") or {}
            lines.append(
                f"STOCK {symbol} ({row.get('name') or company_names.get(symbol, 'name unavailable')}): "
                f"status {row.get('status', 'not labelled')}; price ₹{_show(row.get('price'))}; "
                f"change {_show(row.get('change_pct', row.get('open_change_pct')), suffix='%')}; "
                f"day range ₹{_show(row.get('day_low'))}–₹{_show(row.get('day_high'))}; "
                f"open ₹{_show(row.get('session_open'))}; VWAP ₹{_show(row.get('vwap'))}; "
                f"RSI {_show(raw.get('rsi', metrics.get('rsi', row.get('rsi'))), 1)}; "
                f"relative volume {_show(row.get('rvol'), 2)}×; score {_show(metrics.get('score', row.get('score')), 1)}; "
                f"signal {decision.get('action', metrics.get('signal', 'not recorded'))}, grade {decision.get('grade', 'not recorded')}; "
                f"source {row.get('source') or snapshot.get('data_source', 'not supplied') if snapshot else row.get('source', 'not supplied')}."
            )
            if session_date:
                try:
                    from candle_archive import get_candle_archive
                    candles = get_candle_archive().get_candles_for_session(symbol, 5, session_date)
                    if candles:
                        lines.append(_session_candle_summary(symbol, session_date, candles))
                except Exception:
                    pass

    if db_manager is not None and session_date:
        try:
            day = date.fromisoformat(session_date)
            start = datetime.combine(day, time.min, IST).astimezone(timezone.utc).isoformat()
            end = datetime.combine(day + timedelta(days=1), time.min, IST).astimezone(timezone.utc).isoformat()
            with db_manager.get_connection() as connection:
                counts = connection.execute("SELECT outcome, COUNT(*) AS n FROM signals WHERE created_at>=? AND created_at<? GROUP BY outcome", (start, end)).fetchall()
                total = sum(int(row["n"]) for row in counts)
                if total:
                    lines.append(f"SIGNAL JOURNAL FOR {session_date}: {total} recorded setups; outcomes " + ", ".join(f"{row['outcome']} {row['n']}" for row in counts) + ".")
                    sql = "SELECT created_at,symbol,timeframe,action,grade,score,price,outcome,stop_loss,target_1 FROM signals WHERE created_at>=? AND created_at<?"
                    params: list[Any] = [start, end]
                    signal_symbols = mentioned_symbols or (symbols if "watchlist" in query.casefold() or "my stocks" in query.casefold() else [])
                    if signal_symbols:
                        sql += " AND symbol IN (" + ",".join("?" for _ in signal_symbols) + ")"
                        params.extend(signal_symbols)
                    sql += " ORDER BY created_at DESC LIMIT 25"
                    recorded = connection.execute(sql, params).fetchall()
                    if recorded:
                        lines.append("RECORDED SETUPS: " + "; ".join(f"{row['symbol']} {row['action']} grade {row['grade']} score {row['score']} at ₹{_show(row['price'])}; outcome {row['outcome']}; SL ₹{_show(row['stop_loss'])}; T1 ₹{_show(row['target_1'])}" for row in recorded) + ".")
                else:
                    lines.append(f"SIGNAL JOURNAL FOR {session_date}: no signal rows were recorded in the site's local journal for this date.")
        except Exception:
            pass

    try:
        from results_service import get_results_service
        service = get_results_service()
        if "result" in query.casefold() or "earning" in query.casefold() or "filing" in query.casefold() or mentioned_symbols:
            date_match = re.search(r"\b(20\d{2})\b", query)
            date_from = f"{date_match.group(1)}-01-01" if date_match else (date.fromisoformat(session_date) - timedelta(days=30)).isoformat() if session_date else None
            date_to = f"{date_match.group(1)}-12-31" if date_match else session_date
            events = service.list_events(date_from=date_from, date_to=date_to, symbol=mentioned_symbols[0] if mentioned_symbols else "", limit=5, search="").get("events", [])
            for event in events[:5]:
                detail = service.get_event(event.get("id")) or event
                growth = event.get("growth") or {}
                lines.append(f"PUBLISHED RESULT {event.get('symbol')} ({event.get('company')}), announced {event.get('announced_at')}, period {event.get('period_end')}, financial status {event.get('financial_status', 'not supplied')}; revenue QoQ {_show(growth.get('revenue_qoq'), suffix='%')}; profit QoQ {_show(growth.get('profit_qoq'), suffix='%')}; source {event.get('source_url') or event.get('document_url') or 'filing record' }.")
                news = detail.get("news") or []
                for item in news[:3]:
                    lines.append(f"COMPANY DISCLOSURE {event.get('symbol')}: {item.get('published_at', 'date not supplied')} · {str(item.get('title') or 'untitled disclosure')[:240]} · source {item.get('source', 'NSE company disclosure')}.")
        status = service.status()
        if any(word in query.casefold() for word in ("result", "earning", "news", "filing")):
            lines.append(f"RESULTS SERVICE: {status.get('state', 'not available')}; {status.get('event_count', 0)} current catalogue events; latest received {status.get('last_success_at') or 'not recorded'}; coverage note: {status.get('coverage_note', 'not supplied')}")
    except Exception:
        pass

    if any(word in query.casefold() for word in ("margin", "leverage", "zerodha", "mis")):
        try:
            from intraday_margins import get_intraday_margins
            margins = get_intraday_margins().payload()
            lines.append(f"ZERODHA MARGIN REFERENCE: source list date {margins.get('source_updated_at') or 'not supplied'}; checked {margins.get('checked_at') or 'not supplied'}; website matches {margins.get('record_count', 0)}; above 3× {margins.get('eligible_count', 0)}; stale {bool(margins.get('stale'))}. Published MIS leverage can change and is not an order-time quote.")
            for symbol in mentioned_symbols or symbols[:1]:
                record = margins.get("records", {}).get(symbol)
                if record and not margins.get("stale"):
                    lines.append(f"ZERODHA MIS REFERENCE {symbol}: {record['leverage']}× at {record['margin_percent']}% margin; checked {margins.get('checked_at')}; published list date {margins.get('source_updated_at')}. This is not an order-time quote.")
                else:
                    lines.append(f"ZERODHA MIS REFERENCE {symbol}: no fresh row available in the stored website reference.")
        except Exception:
            pass

    return {"text": "\n".join(lines)[:9000], "symbols": symbols, "mentioned_symbols": mentioned_symbols, "session_date": session_date,
            "session_state": session_state, "snapshot_stock_count": len(stocks)}
