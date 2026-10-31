"""Optional read-only FYERS history and validated candle-file imports for event studies.

Public candle services have limited intraday retention. This module provides an
explicit path to longer history without filling gaps or placing broker orders.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_URL = "https://api-t1.fyers.in/data/history"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected history redirect")


def valid_candle(row: dict, timeframe: int, now: datetime | None = None) -> dict | None:
    """Validate a real OHLCV observation; never repair or synthesize observations."""
    now = now or datetime.now(timezone.utc)
    try:
        stamp = row.get("t", row.get("ts", row.get("timestamp")))
        if isinstance(stamp, str) and not stamp.isdigit():
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            ts = int(parsed.timestamp())
        else:
            ts = int(stamp)
        o, h, l, c, v = (float(row[k]) for k in ("o", "h", "l", "c", "v"))
        if not all(math.isfinite(n) for n in (o, h, l, c, v)):
            return None
        if min(o, h, l, c) <= 0 or v < 0 or l > min(o, c) or h < max(o, c) or h < l:
            return None
        point = datetime.fromtimestamp(ts, IST)
        if not 2000 <= point.year <= now.year or ts + timeframe * 60 > now.timestamp():
            return None
        if timeframe == 5:
            minute = point.hour * 60 + point.minute
            if point.weekday() >= 5 or not 555 <= minute < 930 or (minute - 555) % 5 or point.second:
                return None
        return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v, "closed": True, "timeframe": timeframe}
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return None


def fyers_configured() -> bool:
    return bool((os.environ.get("FYERS_APP_ID") or os.environ.get("FYERS_CLIENT_ID")) and os.environ.get("FYERS_ACCESS_TOKEN"))


def fetch_fyers_event_candles(symbol: str, session_date: str, *, now: datetime | None = None, opener=None) -> list[dict]:
    """Download a bounded event window using an existing server-side access token.

    Credentials stay in an Authorization header to the fixed official data host.
    Errors intentionally omit response bodies, which can contain account details.
    """
    if not re.fullmatch(r"[A-Z0-9&._-]{1,30}", symbol):
        raise ValueError("Invalid NSE symbol")
    if not fyers_configured():
        return []
    now = now or datetime.now(timezone.utc)
    session = date.fromisoformat(session_date)
    if session > now.astimezone(IST).date():
        return []
    start = session - timedelta(days=10)
    end = min(session + timedelta(days=1), now.astimezone(IST).date())
    params = urllib.parse.urlencode({"symbol": f"NSE:{symbol}-EQ", "resolution": "5", "date_format": "1", "range_from": start.isoformat(), "range_to": end.isoformat(), "cont_flag": "1"})
    app_id = os.environ.get("FYERS_APP_ID") or os.environ.get("FYERS_CLIENT_ID")
    token = os.environ["FYERS_ACCESS_TOKEN"]
    request = urllib.request.Request(HISTORY_URL + "?" + params, headers={"Authorization": f"{app_id}:{token}", "Accept": "application/json"})
    try:
        open_request = opener or urllib.request.build_opener(_NoRedirect()).open
        with open_request(request, timeout=12) as response:
            payload = response.read(8_000_001)
        if len(payload) > 8_000_000:
            raise ValueError("History response too large")
        data = json.loads(payload)
        if data.get("s") != "ok" or not isinstance(data.get("candles"), list):
            raise ValueError("History request unsuccessful")
        result = []
        for values in data["candles"]:
            if not isinstance(values, list) or len(values) < 6:
                continue
            raw = dict(zip(("t", "o", "h", "l", "c", "v"), values[:6]))
            clean = valid_candle(raw, 5, now)
            if clean:
                clean["source"] = "FYERS historical candles"
                result.append(clean)
        return sorted({r["t"]: r for r in result}.values(), key=lambda r: r["t"])
    except Exception:
        raise RuntimeError("FYERS history unavailable; check local credentials, plan access and network.") from None


def import_candle_csv(path: str, archive, *, source: str, timeframe: int = 5, now: datetime | None = None) -> dict:
    """Import a user-sourced historical dataset only after validating every row.

    A rejected file writes nothing. Expected columns: symbol,t,o,h,l,c,v. `t`
    accepts Unix seconds or an ISO timestamp with an explicit time zone.
    """
    if timeframe not in (5, 1440):
        raise ValueError("Use 5-minute or daily (1440) observations.")
    if not source.strip() or len(source) > 160:
        raise ValueError("A short data-source description is required.")
    file = Path(path)
    if file.stat().st_size > 100_000_000:
        raise ValueError("CSV exceeds 100 MB.")
    grouped = {}
    count = 0
    with file.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not {"symbol", "t", "o", "h", "l", "c", "v"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV needs symbol,t,o,h,l,c,v headers.")
        for number, row in enumerate(reader, 2):
            symbol = row.get("symbol", "").strip().upper()
            clean = valid_candle(row, timeframe, now)
            if not re.fullmatch(r"[A-Z0-9&._-]{1,30}", symbol) or clean is None:
                raise ValueError(f"Invalid or incomplete candle on CSV row {number}; no data imported.")
            count += 1
            if count > 500_000:
                raise ValueError("CSV exceeds 500,000 observations.")
            key = (symbol, clean["t"])
            if key in grouped and grouped[key] != clean:
                raise ValueError(f"Conflicting duplicate on CSV row {number}; no data imported.")
            grouped[key] = clean
    by_symbol = {}
    for (symbol, _), candle in grouped.items():
        by_symbol.setdefault(symbol, []).append(candle)
    written = sum(archive.archive_closed_candles(symbol, timeframe, rows, source=source.strip()) for symbol, rows in by_symbol.items())
    return {"rows": written, "symbols": len(by_symbol), "source": source.strip(), "timeframe": timeframe}
