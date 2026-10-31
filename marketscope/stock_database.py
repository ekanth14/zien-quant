"""
MarketScope Stock Master Database
Downloads, indexes, and caches the complete database of 2,600+ equities
actively trading on the National Stock Exchange of India (NSE) and Bombay Stock Exchange (BSE).
"""

from __future__ import annotations
from contextlib import contextmanager
import csv
import io
import logging
import os
import sqlite3
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("marketscope.stock_database")

NSE_MASTER_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
DB_DIR = Path(__file__).parent.parent / "work" / "runtime"
DB_PATH = Path(os.environ.get("MS_STOCK_DB", str(DB_DIR / "stock_master.db")))

# Core sector mapping heuristics by keyword
SECTOR_KEYWORDS = {
    "BANK": "Financials",
    "FINANCE": "Financials",
    "CAPITAL": "Financials",
    "INSURANCE": "Financials",
    "INVEST": "Financials",
    "TECH": "Technology",
    "INFOTECH": "Technology",
    "SOFTWARE": "Technology",
    "CONSULTANCY": "Technology",
    "STEEL": "Metals & Mining",
    "MINING": "Metals & Mining",
    "MINERAL": "Metals & Mining",
    "IRON": "Metals & Mining",
    "ALUMIN": "Metals & Mining",
    "COPPER": "Metals & Mining",
    "PHARMA": "Healthcare",
    "HEALTH": "Healthcare",
    "LAB": "Healthcare",
    "BIO": "Healthcare",
    "LIFE": "Healthcare",
    "DRUG": "Healthcare",
    "AUTO": "Automobile",
    "MOTOR": "Automobile",
    "TYRE": "Automobile",
    "VEHICLE": "Automobile",
    "POWER": "Energy & Utilities",
    "ENERGY": "Energy & Utilities",
    "OIL": "Energy & Utilities",
    "GAS": "Energy & Utilities",
    "PETRO": "Energy & Utilities",
    "SOLAR": "Energy & Utilities",
    "CONSUMER": "FMCG",
    "FOOD": "FMCG",
    "BREW": "FMCG",
    "SUGAR": "FMCG",
    "DAIRY": "FMCG",
    "RETAIL": "Retail & Consumer",
    "FASHION": "Retail & Consumer",
    "TEXTILE": "Manufacturing",
    "CHEM": "Chemicals",
    "FERT": "Chemicals",
    "CEMENT": "Infrastructure",
    "INFRA": "Infrastructure",
    "BUILD": "Infrastructure",
    "ENGINEER": "Capital Goods",
    "TELE": "Telecom",
    "COMMUNICATION": "Telecom",
}


def classify_sector(company_name: str, symbol: str) -> str:
    combined = f"{symbol} {company_name}".upper()
    for kw, sec in SECTOR_KEYWORDS.items():
        if kw in combined:
            return sec
    return "General"


class StockDatabaseManager:
    """
    Local SQLite repository for all NSE and BSE active equities.
    Supports fast fuzzy search, prefix lookup, and sector filtering.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stocks (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL DEFAULT 'NSE',
                    series TEXT,
                    isin TEXT,
                    listing_date TEXT,
                    sector TEXT NOT NULL DEFAULT 'General',
                    face_value TEXT,
                    bse_code TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_symbol ON stocks(symbol);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_name ON stocks(name);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_sector ON stocks(sector);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_exchange ON stocks(exchange);")
            conn.commit()

    def get_all_symbols(self) -> set[str]:
        """Return the stored catalogue scope without triggering an upstream import."""
        with self._get_connection() as connection:
            return {row[0] for row in connection.execute("SELECT symbol FROM stocks")}

    def get_stock_count(self) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM stocks")
            return cur.fetchone()[0]

    def populate_from_nse_master(self, csv_data: Optional[str] = None) -> int:
        """Downloads or parses official NSE equity master and populates database."""
        if not csv_data:
            try:
                req = urllib.request.Request(
                    NSE_MASTER_URL,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    csv_data = resp.read().decode("utf-8", errors="ignore")
            except Exception as e:
                logger.error("Failed to download NSE Master CSV: %s", e)
                return 0

        reader = csv.reader(io.StringIO(csv_data))
        try:
            raw_headers = next(reader)
        except StopIteration:
            return 0

        headers = [h.strip().upper() for h in raw_headers]
        sym_idx = headers.index("SYMBOL") if "SYMBOL" in headers else 0
        name_idx = headers.index("NAME OF COMPANY") if "NAME OF COMPANY" in headers else 1
        ser_idx = headers.index("SERIES") if "SERIES" in headers else 2
        date_idx = headers.index("DATE OF LISTING") if "DATE OF LISTING" in headers else 3
        isin_idx = headers.index("ISIN NUMBER") if "ISIN NUMBER" in headers else 6
        fv_idx = headers.index("FACE VALUE") if "FACE VALUE" in headers else 7

        records = []
        for row in reader:
            if not row or len(row) <= sym_idx:
                continue
            symbol = row[sym_idx].strip().upper()
            if not symbol or symbol == "SYMBOL":
                continue
            name = row[name_idx].strip() if len(row) > name_idx else symbol
            series = row[ser_idx].strip() if len(row) > ser_idx else "EQ"
            list_date = row[date_idx].strip() if len(row) > date_idx else ""
            isin = row[isin_idx].strip() if len(row) > isin_idx else ""
            fv = row[fv_idx].strip() if len(row) > fv_idx else "10"
            sec = classify_sector(name, symbol)

            records.append((symbol, name, "NSE/BSE", series, isin, list_date, sec, fv, symbol))

        with self._lock, self._get_connection() as conn:
            conn.executemany("""
                INSERT INTO stocks (symbol, name, exchange, series, isin, listing_date, sector, face_value, bse_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=excluded.name,
                    exchange=excluded.exchange,
                    series=excluded.series,
                    isin=excluded.isin,
                    listing_date=excluded.listing_date,
                    sector=excluded.sector,
                    face_value=excluded.face_value,
                    updated_at=CURRENT_TIMESTAMP;
            """, records)
            conn.commit()

        logger.info("Successfully populated %d stocks into stock master database.", len(records))
        return len(records)

    def seed_default_universe(self) -> int:
        """Seeds the top-100 liquid universe used by the terminal if offline or no master CSV."""
        try:
            from market import STOCK_CATALOGUE
        except ImportError:
            from marketscope.market import STOCK_CATALOGUE

        records = []
        for s in STOCK_CATALOGUE:
            sym = s.get("symbol", "").strip().upper()
            if not sym:
                continue
            name = s.get("name", sym)
            sec = s.get("sector", "General")
            records.append((sym, name, "NSE/BSE", "EQ", "", "", sec, "10", sym))

        with self._lock, self._get_connection() as conn:
            conn.executemany("""
                INSERT INTO stocks (symbol, name, exchange, series, isin, listing_date, sector, face_value, bse_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=excluded.name,
                    sector=excluded.sector,
                    updated_at=CURRENT_TIMESTAMP;
            """, records)
            conn.commit()

        logger.info("Seeded %d liquid symbols into stock master.", len(records))
        return len(records)

    def search_stocks(self, query: str, exchange: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Searches stocks by symbol prefix, symbol substring, or company name."""
        q = f"%{query.strip().upper()}%"
        exact_prefix = f"{query.strip().upper()}%"

        sql = """
            SELECT symbol, name, exchange, series, isin, sector, listing_date
            FROM stocks
            WHERE (symbol LIKE ? OR name LIKE ?)
        """
        params: List[Any] = [q, q]

        if exchange and exchange.upper() != "ALL":
            sql += " AND (exchange = ? OR exchange LIKE '%BSE%')"
            params.append(exchange.upper())

        # Sort exact prefix match first, then by symbol
        sql += """
            ORDER BY
                CASE WHEN symbol LIKE ? THEN 0 ELSE 1 END,
                symbol ASC
            LIMIT ?
        """
        params.extend([exact_prefix, limit])

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def get_stock(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM stocks WHERE symbol = ?", (sym,))
            row = cur.fetchone()
            return dict(row) if row else None


# Global singleton
_stock_db: Optional[StockDatabaseManager] = None


def get_stock_database() -> StockDatabaseManager:
    global _stock_db
    if _stock_db is None:
        _stock_db = StockDatabaseManager()
        # Seed if empty
        if _stock_db.get_stock_count() == 0:
            threading.Thread(target=_stock_db.populate_from_nse_master, daemon=True).start()
    return _stock_db

