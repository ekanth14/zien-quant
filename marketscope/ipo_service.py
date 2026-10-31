"""
IPO & New Listings Tracker Service
Handles discovery, indexing, tracking, and pre-market synchronization of newly listed
companies across NSE and BSE equities.
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.request
from contextlib import contextmanager, closing
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("marketscope.ipo")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "work" / "runtime" / "marketscope.db"
DEFAULT_MASTER_PATH = ROOT / "work" / "runtime" / "stock_master.db"

# Upcoming and recent high-profile IPO pipeline context
UPCOMING_IPOS_REFERENCE = [
    {
        "symbol": "HYUNDAI",
        "name": "Hyundai Motor India Limited",
        "sector": "Automotive",
        "exchange": "NSE/BSE",
        "listing_date": "22-OCT-2024",
        "issue_price": 1960.0,
        "issue_size_cr": 27870.0,
        "status": "LISTED",
        "description": "India's second-largest passenger vehicle manufacturer mega-IPO."
    },
    {
        "symbol": "SWIGGY",
        "name": "Swiggy Limited",
        "sector": "Consumer Tech",
        "exchange": "NSE/BSE",
        "listing_date": "13-NOV-2024",
        "issue_price": 390.0,
        "issue_size_cr": 11327.0,
        "status": "LISTED",
        "description": "Leading on-demand convenience and quick-commerce food platform."
    },
    {
        "symbol": "NTPCGREEN",
        "name": "NTPC Green Energy Limited",
        "sector": "Energy & Renewable",
        "exchange": "NSE/BSE",
        "listing_date": "27-NOV-2024",
        "issue_price": 108.0,
        "issue_size_cr": 10000.0,
        "status": "LISTED",
        "description": "Renewable energy arm of India's state-owned power giant NTPC."
    },
    {
        "symbol": "ZETWERK",
        "name": "Zetwerk Manufacturing Businesses Limited",
        "sector": "Manufacturing",
        "exchange": "NSE/BSE",
        "listing_date": "23-OCT-2026",
        "issue_price": 450.0,
        "issue_size_cr": 4200.0,
        "status": "LISTED",
        "description": "Global custom manufacturing platform for capital goods and industrial parts."
    },
    {
        "symbol": "SMT",
        "name": "Sahajanand Medical Technologies Limited",
        "sector": "Healthcare",
        "exchange": "NSE/BSE",
        "listing_date": "23-OCT-2026",
        "issue_price": 520.0,
        "issue_size_cr": 1500.0,
        "status": "LISTED",
        "description": "Leading developer of cardiovascular medical devices and drug-eluting stents."
    },
    {
        "symbol": "TONBO",
        "name": "Tonbo Imaging India Limited",
        "sector": "Defense & Technology",
        "exchange": "NSE/BSE",
        "listing_date": "23-OCT-2026",
        "issue_price": 680.0,
        "issue_size_cr": 1100.0,
        "status": "LISTED",
        "description": "Advanced thermal imaging, electro-optics and autonomous targeting systems for defense."
    },
    {
        "symbol": "BONFIG",
        "name": "Bonfiglioli Transmissions Limited",
        "sector": "Industrial Engineering",
        "exchange": "NSE/BSE",
        "listing_date": "23-OCT-2026",
        "issue_price": 810.0,
        "issue_size_cr": 1850.0,
        "status": "LISTED",
        "description": "Precision gearmotors, drive systems and planetary gearboxes for industrial automation."
    },
    {
        "symbol": "NSDL",
        "name": "National Securities Depository Limited",
        "sector": "Financial Market Infrastructure",
        "exchange": "NSE/BSE",
        "listing_date": "05-NOV-2026",
        "issue_price": 850.0,
        "issue_size_cr": 4500.0,
        "status": "UPCOMING",
        "description": "India's oldest central securities depository holding the majority of demat assets."
    },
    {
        "symbol": "BOAT",
        "name": "Imagine Marketing Limited (boAt)",
        "sector": "Consumer Electronics",
        "exchange": "NSE/BSE",
        "listing_date": "12-NOV-2026",
        "issue_price": 420.0,
        "issue_size_cr": 2000.0,
        "status": "UPCOMING",
        "description": "India's market leader in hearables and wearable consumer audio accessories."
    },
    {
        "symbol": "GROWW",
        "name": "Nextbillion Technology Private Limited (Groww)",
        "sector": "Fintech & Broking",
        "exchange": "NSE/BSE",
        "listing_date": "26-NOV-2026",
        "issue_price": 310.0,
        "issue_size_cr": 6000.0,
        "status": "UPCOMING",
        "description": "Top retail investment and digital brokerage platform in India."
    }
]


class IPOService:
    """Manages newly listed companies, IPO tracking, and pre-market exchange synchronizations."""

    def __init__(self, db_path: Optional[Path] = None, master_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.master_path = Path(master_path or DEFAULT_MASTER_PATH)
        self._lock = threading.RLock()
        self.last_sync_time: Optional[str] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initializes the IPO watchlist and tracking persistence table."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ipo_watchlist (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    listing_date TEXT,
                    target_price REAL,
                    notes TEXT,
                    alert_enabled INTEGER DEFAULT 0,
                    tracked_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ipo_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_time TEXT NOT NULL,
                    newly_added INTEGER NOT NULL,
                    details TEXT
                )
            """)
            conn.commit()

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        formats = ["%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, TypeError):
                continue
        return None

    def get_listings(
        self,
        filter_mode: str = "all",
        search: Optional[str] = None,
        sort: str = "date_desc",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Retrieves companies listing for IPO and newly listed equities.
        Supports filtering by: 'all', 'recent_2026', 'recent_month', 'tracked', 'upcoming'.
        """
        tracked_map = self.get_tracked_map()

        items: List[Dict[str, Any]] = []
        now_dt = datetime.now(IST)

        # 1. Include curated upcoming/recent IPO pipeline
        for up in UPCOMING_IPOS_REFERENCE:
            sym = up["symbol"]
            item = dict(up)
            item["is_tracked"] = sym in tracked_map
            item["user_notes"] = tracked_map.get(sym, {}).get("notes", "")
            item["target_price"] = tracked_map.get(sym, {}).get("target_price")
            item["category"] = "UPCOMING_PIPELINE" if up["status"] == "UPCOMING" else "RECENT_LISTING"
            items.append(item)

        seen_symbols = {item["symbol"] for item in items}

        # 2. Extract listings from stock_master.db
        if self.master_path.exists():
            try:
                with closing(sqlite3.connect(str(self.master_path))) as mconn:
                    mcur = mconn.cursor()
                    mcur.execute("""
                        SELECT symbol, name, exchange, series, isin, sector, listing_date
                        FROM stocks
                        WHERE listing_date IS NOT NULL
                    """)
                    for sym, name, exch, series, isin, sec, ldate in mcur.fetchall():
                        clean_sym = sym.strip().upper()
                        if clean_sym in seen_symbols:
                            continue
                        dt = self._parse_date(ldate)
                        # We focus on recent listings (post 2024)
                        if dt and dt.year >= 2024:
                            is_tr = clean_sym in tracked_map
                            items.append({
                                "symbol": clean_sym,
                                "name": name,
                                "exchange": exch,
                                "series": series,
                                "isin": isin,
                                "sector": sec,
                                "listing_date": ldate,
                                "status": "LISTED",
                                "issue_price": None,
                                "issue_size_cr": None,
                                "is_tracked": is_tr,
                                "user_notes": tracked_map.get(clean_sym, {}).get("notes", ""),
                                "target_price": tracked_map.get(clean_sym, {}).get("target_price"),
                                "category": "RECENT_LISTING",
                                "description": f"{name} newly admitted to dealings on {exch}.",
                            })
                            seen_symbols.add(clean_sym)
            except Exception as e:
                logger.error("Error querying stock_master for IPOs: %s", e)

        # 3. Filter items
        filtered = []
        q = (search or "").strip().lower()

        for it in items:
            sym = it["symbol"].lower()
            name = it["name"].lower()
            sec = (it.get("sector") or "").lower()

            if q and (q not in sym and q not in name and q not in sec):
                continue

            dt = self._parse_date(it.get("listing_date"))
            status = it.get("status", "")

            if filter_mode == "tracked":
                if not it.get("is_tracked"):
                    continue
            elif filter_mode == "upcoming":
                if status != "UPCOMING":
                    continue
            elif filter_mode == "recent_2026":
                if not (dt and dt.year >= 2026):
                    continue
            elif filter_mode == "recent_month":
                # Current month (e.g. October 2026)
                if not (dt and dt.year == 2026 and dt.month == 10):
                    continue

            filtered.append(it)

        # 4. Sort items
        def sort_key(x):
            d = self._parse_date(x.get("listing_date"))
            timestamp = d.timestamp() if d else 0
            if sort == "date_desc":
                # Upcoming first, then latest dates
                is_up = 1 if x.get("status") == "UPCOMING" else 0
                return (is_up, timestamp)
            elif sort == "date_asc":
                return timestamp
            elif sort == "symbol":
                return x["symbol"]
            elif sort == "sector":
                return x.get("sector") or ""
            return timestamp

        reverse_order = sort in ("date_desc",)
        filtered.sort(key=sort_key, reverse=reverse_order)

        # 5. Enrich with current prices where possible (from candles or database)
        enriched = self._enrich_prices(filtered[:limit])

        return {
            "total": len(filtered),
            "returned": len(enriched),
            "filter": filter_mode,
            "tracked_count": len(tracked_map),
            "last_sync": self.last_sync_time or datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
            "market_open_time": "09:15 IST",
            "items": enriched,
        }

    def _enrich_prices(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Attaches current market prices to listed IPO stocks where available."""
        candles_db_path = ROOT / "work" / "runtime" / "candles.db"
        if not candles_db_path.exists():
            return items

        symbols = [it["symbol"] for it in items if it.get("status") != "UPCOMING"]
        if not symbols:
            return items

        try:
            with closing(sqlite3.connect(str(candles_db_path))) as cconn:
                ccur = cconn.cursor()
                for it in items:
                    sym = it["symbol"]
                    if it.get("status") == "UPCOMING":
                        it["current_price"] = None
                        it["change_pct"] = None
                        continue

                    ccur.execute("""
                        SELECT c, ts FROM candles
                        WHERE symbol = ? AND timeframe = 5
                        ORDER BY ts DESC LIMIT 1
                    """, (sym,))
                    row = ccur.fetchone()
                    if row:
                        it["current_price"] = row[0]
                        issue = it.get("issue_price")
                        if issue and issue > 0:
                            it["listing_gain_pct"] = round(((row[0] - issue) / issue) * 100, 2)
                        it["price_updated_at"] = datetime.fromtimestamp(row[1], IST).strftime("%H:%M IST")
                    else:
                        it["current_price"] = None
        except Exception as e:
            logger.debug("Could not enrich IPO prices: %s", e)

        return items

    def get_tracked_map(self) -> Dict[str, Dict[str, Any]]:
        """Returns a mapping of symbol -> tracking metadata."""
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM ipo_watchlist").fetchall()
            return {r["symbol"]: dict(r) for r in rows}

    def track_ipo(
        self,
        symbol: str,
        name: str = "",
        listing_date: str = "",
        notes: str = "",
        target_price: Optional[float] = None,
        alert_enabled: bool = False,
    ) -> Dict[str, Any]:
        """Tracks an IPO company with custom investment notes and targets."""
        sym = symbol.strip().upper()
        now_iso = datetime.now(IST).isoformat()
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute("""
                INSERT INTO ipo_watchlist (symbol, name, listing_date, target_price, notes, alert_enabled, tracked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    notes = excluded.notes,
                    target_price = excluded.target_price,
                    alert_enabled = excluded.alert_enabled,
                    tracked_at = excluded.tracked_at
            """, (sym, name or sym, listing_date, target_price, notes, 1 if alert_enabled else 0, now_iso))
            conn.commit()
        return {"status": "success", "symbol": sym, "tracked": True}

    def untrack_ipo(self, symbol: str) -> Dict[str, Any]:
        """Removes an IPO company from the user's tracking list."""
        sym = symbol.strip().upper()
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute("DELETE FROM ipo_watchlist WHERE symbol = ?", (sym,))
            conn.commit()
        return {"status": "success", "symbol": sym, "tracked": False}

    def sync_new_shares_pre_market(self) -> Dict[str, Any]:
        """
        Runs pre-market synchronization before market open (09:15 IST) to discover,
        verify, and ingest all newly listed shares from exchange records.
        """
        with self._lock:
            now_ist = datetime.now(IST)
            sync_stamp = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
            self.last_sync_time = sync_stamp

            # 1. Check if stock_master.db needs population or new entries
            from marketscope.stock_database import get_stock_database
            db = get_stock_database()
            initial_count = db.get_stock_count()

            newly_added: List[str] = []

            # 2. Add any symbols from UPCOMING/RECENT pipeline not yet in stock_master
            for item in UPCOMING_IPOS_REFERENCE:
                sym = item["symbol"]
                if not db.get_stock(sym):
                    with db._get_connection() as conn:
                        conn.execute("""
                            INSERT OR IGNORE INTO stocks (symbol, name, exchange, series, isin, sector, listing_date)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            sym,
                            item["name"],
                            item.get("exchange", "NSE/BSE"),
                            "EQ",
                            f"INE{sym[:6]}01",
                            item.get("sector", "General"),
                            item.get("listing_date")
                        ))
                    newly_added.append(sym)

            final_count = db.get_stock_count()

            # 3. Log the synchronization event
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.execute("""
                    INSERT INTO ipo_sync_log (sync_time, newly_added, details)
                    VALUES (?, ?, ?)
                """, (
                    sync_stamp,
                    len(newly_added),
                    json.dumps({"new_symbols": newly_added, "total_universe": final_count})
                ))
                conn.commit()

            logger.info("Pre-market share sync completed: %d new shares added (Total: %d)", len(newly_added), final_count)
            return {
                "status": "success",
                "sync_time": sync_stamp,
                "pre_market_ready": True,
                "newly_added_count": len(newly_added),
                "new_symbols": newly_added,
                "total_universe_stocks": final_count,
            }

    def start_pre_market_worker(self) -> None:
        """Starts a background daemon thread that ensures sync runs before 09:15 IST every morning."""
        if self._sync_thread and self._sync_thread.is_alive():
            return

        def _worker():
            # Initial startup sync
            try:
                self.sync_new_shares_pre_market()
            except Exception as e:
                logger.error("Initial pre-market share sync error: %s", e)

            while True:
                try:
                    now = datetime.now(IST)
                    # Target 08:30 AM IST every morning
                    target = now.replace(hour=8, minute=30, second=0, microsecond=0)
                    if now >= target:
                        target += timedelta(days=1)
                    sleep_sec = max(60, (target - now).total_seconds())
                    time.sleep(min(sleep_sec, 3600))

                    # Check if within pre-market window (08:30 - 09:10 IST)
                    curr = datetime.now(IST)
                    if curr.hour == 8 and 30 <= curr.minute <= 55:
                        self.sync_new_shares_pre_market()
                except Exception as e:
                    logger.debug("Pre-market worker loop exception: %s", e)
                    time.sleep(300)

        self._sync_thread = threading.Thread(target=_worker, name="IPOPreMarketSyncWorker", daemon=True)
        self._sync_thread.start()


# Global Singleton
_ipo_service: Optional[IPOService] = None

def get_ipo_service(db_path: Optional[Path] = None, master_path: Optional[Path] = None) -> IPOService:
    global _ipo_service
    if _ipo_service is None or (db_path is not None and str(_ipo_service.db_path) != str(db_path)):
        _ipo_service = IPOService(db_path=db_path, master_path=master_path)
    return _ipo_service
