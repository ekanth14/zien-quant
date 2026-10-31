"""
MarketScope Real Intraday Candlestick Archive
Stores verified closed exchange candles in SQLite with upsert semantics.
Never archives ungrounded or stale-reused data.
"""

from __future__ import annotations
import os
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CANDLES_DB = os.environ.get(
    "MS_CANDLES_DB_PATH",
    str(Path(__file__).parent.parent / "work" / "runtime" / "candles.db"),
)


class CandleArchive:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_CANDLES_DB
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with closing(self._get_connection()) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS candles (
                        symbol TEXT NOT NULL,
                        timeframe INTEGER NOT NULL,
                        ts INTEGER NOT NULL,
                        o REAL NOT NULL,
                        h REAL NOT NULL,
                        l REAL NOT NULL,
                        c REAL NOT NULL,
                        v REAL NOT NULL,
                        source TEXT NOT NULL,
                        PRIMARY KEY (symbol, timeframe, ts)
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts 
                    ON candles (symbol, timeframe, ts);
                """)

    def archive_closed_candles(
        self,
        symbol: str,
        timeframe: int,
        candles: List[Dict[str, Any]],
        source: str = "exchange",
    ) -> int:
        """
        Upserts verified closed candles.
        Silently skips open/forming candles (closed == False).
        """
        if not candles or not symbol:
            return 0

        rows = []
        for c in candles:
            if not c.get("closed", False):
                continue
            ts = c.get("t") or c.get("time")
            if ts is None:
                continue
            try:
                ts_int = int(ts)
            except (ValueError, TypeError):
                continue
            if ts_int <= 0:
                continue

            try:
                raw = [c.get(k, c.get(alias)) for k,alias in [('o','open'),('h','high'),('l','low'),('c','close'),('v','volume')]]
                if any(isinstance(value,bool) for value in raw): continue
                o,h,l,close_val,v = map(float,raw)
                if not all(math.isfinite(value) for value in (o,h,l,close_val,v)): continue
                if min(o,h,l,close_val)<=0 or v<0 or not l<=min(o,close_val)<=max(o,close_val)<=h: continue
            except (ValueError,TypeError,OverflowError):
                continue

            rows.append((symbol.upper(), int(timeframe), ts_int, o, h, l, close_val, v, source))

        if not rows:
            return 0

        with closing(self._get_connection()) as conn:
            with conn:
                conn.executemany("""
                    INSERT INTO candles (symbol, timeframe, ts, o, h, l, c, v, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
                        o = excluded.o,
                        h = excluded.h,
                        l = excluded.l,
                        c = excluded.c,
                        v = excluded.v,
                        source = excluded.source;
                """, rows)
            return len(rows)

    def get_prior_session_close(self, symbol: str, timeframe: int, before_ts: int) -> Optional[float]:
        """Returns the close of the most recent candle prior to before_ts (>= 2024-01-01)."""
        with closing(self._get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c FROM candles
                WHERE symbol = ? AND timeframe = ? AND ts < ? AND ts >= 1704067200
                ORDER BY ts DESC LIMIT 1
            """, (symbol.upper(), int(timeframe), int(before_ts)))
            row = cur.fetchone()
            if row and row["c"] is not None:
                return float(row["c"])
            return None

    def get_recent_candles_before(
        self, symbol: str, timeframe: int, before_ts: int, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Bounded closed-bar history strictly before a calculation boundary."""
        with closing(self._get_connection()) as conn:
            rows = conn.execute(
                """SELECT ts, o, h, l, c, v FROM candles
                WHERE symbol = ? AND timeframe = ? AND ts < ? AND ts >= 1704067200
                ORDER BY ts DESC LIMIT ?""",
                (symbol.upper(), int(timeframe), int(before_ts), max(1, min(int(limit), 500))),
            ).fetchall()
        return [
            {"t": r["ts"], "o": r["o"], "h": r["h"], "l": r["l"],
             "c": r["c"], "v": r["v"], "closed": True}
            for r in reversed(rows)
        ]

    def get_prior_session_ohlc(
        self, symbol: str, timeframe: int, before_session_date: str,
    ) -> Optional[Dict[str, Any]]:
        """Prior observed session OHLC, with full regular-session coverage attested.

        A partial archived day must never masquerade as the previous day's high
        and low for pivot calculations. Completeness requires every expected
        timestamp between the regular open and close, with valid OHLC values.
        """
        try:
            from marketscope.market_schedule import IST
        except ImportError:
            from market_schedule import IST
        try:
            boundary = datetime.strptime(before_session_date, "%Y-%m-%d").replace(tzinfo=IST)
            interval = int(timeframe)
            if interval <= 0:
                return None
        except (TypeError, ValueError):
            return None
        with closing(self._get_connection()) as conn:
            row = conn.execute(
                """SELECT max(ts) AS latest FROM candles
                WHERE symbol = ? AND timeframe = ? AND ts < ? AND ts >= 1704067200""",
                (symbol.upper(), interval, int(boundary.timestamp())),
            ).fetchone()
            if row is None or row["latest"] is None:
                return None
            prior_day = datetime.fromtimestamp(row["latest"], IST).date()
            start = datetime.combine(prior_day, datetime.min.time(), tzinfo=IST)
            rows = conn.execute(
                """SELECT ts, o, h, l, c, v, source FROM candles
                WHERE symbol = ? AND timeframe = ? AND ts >= ? AND ts < ? ORDER BY ts""",
                (symbol.upper(), interval, int(start.timestamp()),
                 int((start + timedelta(days=1)).timestamp())),
            ).fetchall()
        market_open = start.replace(hour=9, minute=15)
        market_close = start.replace(hour=15, minute=30)
        expected = set(range(int(market_open.timestamp()), int(market_close.timestamp()), interval * 60))
        regular = [r for r in rows if int(r["ts"]) in expected]
        if not regular:
            return None
        valid = []
        for r in regular:
            try:
                o, h, l, c = (float(r[k]) for k in ("o", "h", "l", "c"))
                if all(math.isfinite(v) and v > 0 for v in (o, h, l, c)) and l <= min(o, c) <= max(o, c) <= h:
                    valid.append(r)
            except (TypeError, ValueError):
                continue
        if not valid:
            return None
        return {
            "session_date": prior_day.isoformat(), "date": prior_day.isoformat(),
            "o": float(valid[0]["o"]), "h": max(float(r["h"]) for r in valid),
            "l": min(float(r["l"]) for r in valid), "c": float(valid[-1]["c"]),
            "volume": sum(float(r["v"]) for r in valid), "bar_count": len(valid),
            "expected_bar_count": len(expected),
            "complete": {int(r["ts"]) for r in valid} == expected,
            "source": ", ".join(sorted({str(r["source"]) for r in valid})),
            "timeframe": interval,
        }

    def get_stats(self) -> List[Dict[str, Any]]:
        with closing(self._get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, timeframe, count(*) as count, min(ts) as min_ts, max(ts) as max_ts
                FROM candles
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe;
            """)
            return [dict(r) for r in cur.fetchall()]

    def get_latest_session_date(self) -> Optional[str]:
        """Returns the latest session date (YYYY-MM-DD in IST) found in archived candles."""
        with closing(self._get_connection()) as conn:
            cur = conn.cursor()
            cur.execute("SELECT max(ts) as max_ts FROM candles WHERE ts > 0")
            row = cur.fetchone()
            if not row or row["max_ts"] is None:
                return None
            try:
                from marketscope.market_schedule import IST
            except ImportError:
                from market_schedule import IST
            return datetime.fromtimestamp(row["max_ts"], IST).strftime("%Y-%m-%d")

    def get_candles_for_session(
        self,
        symbol: str,
        timeframe: int,
        session_date: str,
    ) -> List[Dict[str, Any]]:
        """Returns candles for a specific stock, timeframe, and session_date (YYYY-MM-DD in IST)."""
        try:
            from marketscope.market_schedule import IST
        except ImportError:
            from market_schedule import IST
        if not isinstance(session_date, str):
            return []
        try:
            target_date = datetime.strptime(session_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return []

        start_dt = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=IST)
        end_dt = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=IST)
        start_ts = max(int(start_dt.timestamp()), 1704067200)
        end_ts = int(end_dt.timestamp())

        with closing(self._get_connection()) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT symbol, timeframe, ts, o, h, l, c, v, source
                FROM candles
                WHERE symbol = ? AND timeframe = ? AND ts BETWEEN ? AND ?
                ORDER BY ts ASC
                """,
                (symbol.upper(), int(timeframe), start_ts, end_ts),
            )
            rows = cur.fetchall()
            return [
                {
                    "t": r["ts"],
                    "o": r["o"],
                    "h": r["h"],
                    "l": r["l"],
                    "c": r["c"],
                    "v": int(r["v"]),
                    "closed": True,
                }
                for r in rows
            ]


_archive_instance: Optional[CandleArchive] = None

def get_candle_archive() -> CandleArchive:
    global _archive_instance
    if _archive_instance is None:
        _archive_instance = CandleArchive()
    return _archive_instance

