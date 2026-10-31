"""
MarketScope HTTP Service & REST APIs
Threaded HTTP server serving APIs, frontend assets, security headers,
and deterministic market snapshot delivery.
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import gzip
import hmac
import logging
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from contextlib import closing
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger("marketscope")
try:
    APP_VERSION = json.loads((Path(__file__).resolve().parent.parent / "package.json").read_text(encoding="utf-8"))["version"]
except (OSError, ValueError, KeyError):
    APP_VERSION = "unknown"

# Ensure package import resolution
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from env_loader import load_dotenv
load_dotenv()

from market import (
    DEFAULT_CONFIG,
    STOCK_CATALOGUE,
)
from scraper import (
    get_live_scraper_feed,
)
from stock_database import (
    get_stock_database,
)
from market_schedule import (
    get_market_schedule_info,
    IST,
)
from laya_ai import (
    get_laya_engine,
)
from laya_context import build_laya_website_context
from security import (
    DatabaseManager,
    RateLimiter,
    SESSION_LIFETIME_HOURS,
    verify_password,
    verify_totp,
)
from signal_journal import SignalJournal
from candle_archive import get_candle_archive
from results_service import get_results_service
from earnings_research import analyze_event
from paper_trading import get_paper_session
from paper_watchlist import get_paper_watchlist
from intraday_margins import get_intraday_margins
from fyers_history import (
    FyersHistoryAuthError,
    FyersHistoryError,
    FyersHistoryNotConfigured,
    FyersHistoryRateLimitError,
    get_fyers_history_provider,
)
try:
    from flow_engine import build_flow_payload
except ImportError:
    from marketscope.flow_engine import build_flow_payload

try:
    from market import (
        calculate_ema,
        calculate_rsi,
        calculate_vwap,
        calculate_rvol,
        calculate_support_resistance,
        calculate_atr,
        calculate_composite_score,
        calculate_order_blocks,
        calculate_cpr,
        calculate_camarilla,
        calculate_supertrend,
        calculate_vwap_bands,
    )
    from decision_engine import evaluate_ai_decision
except ImportError:
    from marketscope.market import (
        calculate_ema,
        calculate_rsi,
        calculate_vwap,
        calculate_rvol,
        calculate_support_resistance,
        calculate_atr,
        calculate_composite_score,
        calculate_order_blocks,
        calculate_cpr,
        calculate_camarilla,
        calculate_supertrend,
        calculate_vwap_bands,
    )
    from marketscope.decision_engine import evaluate_ai_decision

from session_manager import (
    get_session_state,
    build_session_snapshot,
    get_session_scorecard,
    enrich_daily_rankings,
    resolve_previous_session_date,
)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DB_PATH = os.environ.get(
    "MS_DB_PATH",
    str(BASE_DIR.parent / "work" / "runtime" / "marketscope.db"),
)

_FLOW_CACHE: Dict[Tuple[str, str, int], Tuple[float, Dict[str, Any]]] = {}
rate_limiter = RateLimiter(limit=120, window_sec=60)
rate_limiter_llm = RateLimiter(limit=6, window_sec=60)
rate_limiter_llm_auth = RateLimiter(limit=60, window_sec=60)
rate_limiter_track = RateLimiter(limit=10, window_sec=60)
rate_limiter_export = RateLimiter(limit=5, window_sec=60)
rate_limiter_login = RateLimiter(limit=12, window_sec=60)
rate_limiter_history = RateLimiter(limit=8, window_sec=60)
server_start_time = time.time()


def normalize_config_numbers(config: Dict[str, Any]) -> Dict[str, Any]:
    """Keep persisted numerical settings finite and of their declared type."""
    normalized = dict(config)
    for key, value in config.items():
        default = DEFAULT_CONFIG.get(key)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"{key} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{key} must be finite")
        if key in ("norm_trend", "norm_momentum", "norm_vwap", "norm_rsi") and number <= 0:
            raise ValueError(f"{key} must be greater than zero")
        if isinstance(default, int):
            if not number.is_integer():
                raise ValueError(f"{key} must be a whole number")
            normalized[key] = int(number)
        else:
            normalized[key] = number
    return normalized


class MarketScopeHandler(BaseHTTPRequestHandler):
    server_version = f"MarketScope/{APP_VERSION}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def setup(self):
        super().setup()
        self.db: DatabaseManager = getattr(self.server, "db_manager", None) or DatabaseManager(DEFAULT_DB_PATH)
        self.signal_journal: SignalJournal = getattr(self.server, "signal_journal", None) or SignalJournal(self.db)

    def _drain_request_body(self) -> None:
        if not hasattr(self, "_cached_json_body"):
            try:
                length = int(self.headers.get("Content-Length", 0)) if hasattr(self, "headers") and self.headers else 0
                if 0 < length <= 65536 and hasattr(self, "rfile") and self.rfile:
                    self.rfile.read(length)
                self._cached_json_body = None
            except Exception:
                pass

    def _send_json(self, data: Any, status: int = 200, cookies: Optional[List[str]] = None) -> None:
        self._drain_request_body()
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self._add_security_headers()
        if cookies:
            for c in cookies:
                self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str, content_type: str = "text/plain; charset=utf-8", status: int = 200) -> None:
        self._drain_request_body()
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _add_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; font-src 'self' https://fonts.gstatic.com https://unpkg.com data:; img-src 'self' data: https://lh3.googleusercontent.com; connect-src 'self';",
        )

    def _send_rate_limited(self, retry_after: int = 60, detail: str = "Rate limit exceeded.") -> None:
        self._drain_request_body()
        payload = json.dumps({
            "error": "rate_limit_exceeded",
            "detail": detail,
            "retry_after": retry_after,
        }).encode("utf-8")
        self.send_response(429)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Retry-After", str(retry_after))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _get_cookie_token(self) -> Optional[str]:
        if not hasattr(self, "headers") or not self.headers:
            return None
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
            if "ms_session" in cookie:
                return cookie["ms_session"].value
        except Exception:
            pass
        return None

    def _get_current_session(self) -> Optional[Dict[str, Any]]:
        raw_token = self._get_cookie_token()
        if not raw_token:
            return None
        return self.db.validate_session(raw_token)

    def _parse_json_body(self, max_bytes: int = 65536) -> Optional[Dict[str, Any]]:
        if hasattr(self, "_cached_json_body") and self._cached_json_body is not None:
            return self._cached_json_body
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > max_bytes:
                self._cached_json_body = None
                return None
            body = self.rfile.read(length)
            parsed = json.loads(body.decode("utf-8"))
            self._cached_json_body = parsed if isinstance(parsed, dict) else None
            return self._cached_json_body
        except Exception:
            self._cached_json_body = None
            return None

    def do_GET(self) -> None:
        client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"
        if not rate_limiter.is_allowed(client_ip):
            self._send_rate_limited(retry_after=rate_limiter.retry_after(client_ip), detail="Global rate limit exceeded.")
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Static assets
        if path in ("/", "/index.html"):
            self._serve_static_file("workspace.html")
            return
        elif path in ("/flow", "/flow.html"):
            self._serve_static_file("flow.html")
            return
        elif path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            self._serve_static_file(rel_path)
            return
        elif path.endswith(".html") and os.path.isfile(os.path.join(STATIC_DIR, path.lstrip("/"))):
            self._serve_static_file(path.lstrip("/"))
            return
        elif path in ("/matrix", "/terminal"):
            self._serve_static_file("terminal_flow_matrix.html")
            return
        elif path in ("/console", "/orderblock"):
            self._serve_static_file("orderblock_flow_console.html")
            return
        elif path in ("/bloomberg", "/terminal-bloomberg"):
            self._serve_static_file("bloomberg_terminal.html")
            return
        elif path in ("/mobile",):
            self._serve_static_file("mobile_terminal.html")
            return
        elif path in ("/results", "/results/"):
            self._serve_static_file("results.html")
            return

        # Research is read-only: no strategy activation or broker execution route.
        if path == "/api/research/strategies":
            try:
                from strategy.research_lab import catalogue
                self._send_json(catalogue())
            except (OSError, ValueError):
                self._send_json({"error": "Research registry unavailable"}, status=503)
            return
        if path == "/api/research/document":
            documents = {"formulas": "FORMULAS_AND_EVIDENCE.md",
                         "architecture": "PAPER_TRADING_ARCHITECTURE.md",
                         "coverage": "RESEARCH_COVERAGE.md"}
            name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name", [""])[0]
            if name not in documents:
                self._send_json({"error": "Unknown research document"}, status=400)
                return
            try:
                content = (BASE_DIR.parent / "docs" / "quantocracy" / documents[name]).read_text(encoding="utf-8")
                self._send_text(content, content_type="text/plain; charset=utf-8")
            except OSError:
                self._send_json({"error": "Research document unavailable"}, status=503)
            return

        # APIs
        if path in ("/api/paper-watchlist", "/api/paper-watchlist/report", "/api/paper-watchlist/export"):
            try:
                watch = get_paper_watchlist(self.db.db_path)
                day = query.get("day", [None])[0]
                if day:
                    datetime.strptime(day, "%Y-%m-%d")
                if path == "/api/paper-watchlist":
                    self._send_json(watch.status())
                elif path == "/api/paper-watchlist/report":
                    self._send_json(watch.report(day))
                else:
                    fmt = query.get("format", ["csv"])[0]
                    if fmt not in ("csv", "json"):
                        raise ValueError("Export format must be csv or json")
                    self._send_text(watch.export(day, fmt), "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8")
            except ValueError as exc:
                self._send_json({"error": "invalid_paper_request", "detail": str(exc)}, status=400)
            except Exception:
                self._send_json({"error": "paper_watchlist_unavailable", "detail": "Paper ledger is unavailable; no simulated results asserted."}, status=503)
        elif path == "/api/paper/status":
            self._send_json(get_paper_session().snapshot())
        elif path == "/api/paper/export":
            self._send_text(get_paper_session().export_csv(), "text/csv; charset=utf-8")
        elif path == "/api/bootstrap":
            self._handle_bootstrap()
        elif path == "/api/market":
            self._handle_market(query)
        elif path == "/api/stock":
            self._handle_stock(query)
        elif path == "/api/historical/symbols":
            self._handle_historical_symbols(query)
        elif path == "/api/historical/candles":
            if not rate_limiter_history.is_allowed(client_ip):
                self._send_rate_limited(
                    retry_after=rate_limiter_history.retry_after(client_ip),
                    detail="Historical chart requests are limited to 8 per minute.",
                )
                return
            self._handle_historical_candles(query)
        elif path == "/api/health":
            self._handle_health()
        elif path == "/api/intraday-margins":
            self._handle_intraday_margins()
        elif path == "/api/results":
            self._handle_results(query)
        elif path == "/api/results/detail":
            self._handle_result_detail(query)
        elif path == "/api/results/status":
            self._send_json(get_results_service().status())
        elif path == "/api/admin":
            self._handle_admin()
        elif path == "/api/admin/audit-logs":
            self._handle_admin_audit_logs()
        elif path == "/api/config/history":
            self._handle_config_history()
        elif path == "/api/export":
            if not rate_limiter_export.is_allowed(client_ip):
                self._send_rate_limited(retry_after=rate_limiter_export.retry_after(client_ip), detail="Export rate limit exceeded. Max 5 req/min.")
                return
            self._handle_export(query)
        elif path == "/api/backup/status":
            self._handle_backup_status()
        elif path == "/api/ai/status":
            self._handle_ai_status()
        elif path == "/api/ai/pulse":
            if not rate_limiter_llm.is_allowed(client_ip):
                self._send_rate_limited(retry_after=rate_limiter_llm.retry_after(client_ip), detail="AI pulse rate limit exceeded. Max 6 req/min.")
                return
            self._handle_ai_pulse(query)
        elif path == "/api/stocks/database":
            self._handle_stocks_database(query)
        elif path == "/api/stocks/quote":
            self._handle_stocks_quote(query)
        elif path == "/api/market/schedule":
            self._handle_market_schedule()
        elif path == "/api/signals/stats":
            self._handle_signals_stats(query)
        elif path == "/api/session/summary":
            self._handle_session_summary(query)
        elif path == "/api/flow":
            self._handle_flow(query)
        elif path == "/api/ai/analyze":
            self._handle_ai_analyze(query)
        else:
            self._send_json({"error": "Endpoint not found"}, status=404)

    def do_POST(self) -> None:
        client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"
        if not rate_limiter.is_allowed(client_ip):
            self._send_rate_limited(retry_after=rate_limiter.retry_after(client_ip), detail="Global rate limit exceeded.")
            return

        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urllib.parse.urlsplit(origin)
            if parsed_origin.scheme not in ("http", "https") or parsed_origin.netloc.lower() != self.headers.get("Host", "").lower():
                self._send_json({"error": "cross_origin_request", "detail": "Use the same dashboard address to sign in and submit changes."}, status=403)
                return

        # Body size cap 64 KB for every POST.
        content_length_header = self.headers.get("Content-Length")
        if content_length_header:
            try:
                content_len = int(content_length_header)
                if content_len < 0:
                    raise ValueError("Negative content length")
                if content_len > 65536:
                    self._send_json(
                        {"error": "payload_too_large", "detail": "Request body exceeds maximum size of 64KB."},
                        status=413,
                    )
                    return
            except ValueError:
                self._send_json({"error": "invalid_content_length"}, status=400)
                return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/api/paper/control", "/api/logout", "/api/config", "/api/config/preview", "/api/session/preview", "/api/backup", "/api/stocks/track", "/api/stocks/untrack"):
            session = self._get_current_session()
            if session:
                supplied = self.headers.get("X-CSRF-Token", "")
                expected = session.get("csrf_token", "")
                if not supplied or not expected or not hmac.compare_digest(supplied, expected):
                    self._send_json({"error": "invalid_csrf", "detail": "Your session changed. Refresh the dashboard and try again."}, status=403)
                    return

        if path == "/api/ai/chat":
            if not rate_limiter_llm.is_allowed(client_ip):
                self._send_rate_limited(retry_after=rate_limiter_llm.retry_after(client_ip), detail="AI endpoint rate limit exceeded. Max 6 req/min.")
                return

        if path in ("/api/stocks/track", "/api/stocks/untrack"):
            if not rate_limiter_track.is_allowed(client_ip):
                self._send_rate_limited(retry_after=rate_limiter_track.retry_after(client_ip), detail="Tracking endpoint rate limit exceeded. Max 10 req/min.")
                return

        if path == "/api/paper/control":
            session = self._get_current_session()
            if not session or session.get("role") not in ("owner", "admin"):
                self._send_json({"error": "auth_required", "detail": "Sign in as owner/admin to control the shared paper workspace."}, status=403)
                return
            body = self._parse_json_body() or {}
            try:
                self._send_json(get_paper_session().command(body.get("action"), body, session["email"]))
            except ValueError as exc:
                self._send_json({"error": "paper_control", "detail": str(exc)}, status=400)
        elif path == "/api/login":
            self._handle_login()
        elif path == "/api/logout":
            self._handle_logout()
        elif path == "/api/config/preview" or path == "/api/session/preview":
            self._handle_config_preview()
        elif path == "/api/config":
            self._handle_config_update()
        elif path == "/api/backup":
            self._handle_backup()
        elif path == "/api/metrics":
            self._handle_metrics()
        elif path == "/api/ai/analyze":
            self._handle_ai_analyze()
        elif path == "/api/ai/chat":
            self._handle_ai_chat()
        elif path == "/api/stocks/track":
            self._handle_stocks_track()
        elif path == "/api/stocks/untrack":
            self._handle_stocks_untrack()
        else:
            self._send_json({"error": "Unknown POST route"}, status=404)

    def _serve_static_file(self, filename: str) -> None:
        target = (STATIC_DIR / filename).resolve()
        if not target.is_relative_to(STATIC_DIR.resolve()) or not target.exists() or not target.is_file():
            self._send_json({"error": "File not found"}, status=404)
            return

        mime_type, _ = mimetypes.guess_type(str(target))
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            with open(target, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_json({"error": f"Error reading file: {str(e)}"}, status=500)

    def _handle_bootstrap(self) -> None:
        session = self._get_current_session()
        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        has_owner = self.db.has_owner()

        payload = {
            "config": {
                "brand_name": cfg.get("brand_name", DEFAULT_CONFIG["brand_name"]),
                "tagline": cfg.get("tagline", DEFAULT_CONFIG["tagline"]),
                "fast_ema_period": cfg.get("fast_ema_period", 9),
                "slow_ema_period": cfg.get("slow_ema_period", 21),
                "rsi_period": cfg.get("rsi_period", 14),
                "score_threshold": cfg.get("score_threshold", 65),
                "default_timeframe": cfg.get("default_timeframe", 5),
                "refresh_interval_ms": cfg.get("refresh_interval_ms", 2000),
                "formula_approved": cfg.get("formula_approved", True),
                "monitored_symbols": cfg.get("monitored_symbols", list(STOCK_CATALOGUE.keys())),
            },
            "has_owner": has_owner,
            "catalogue": STOCK_CATALOGUE,
            "session": {
                "authenticated": session is not None,
                "email": session["email"] if session else None,
                "role": session["role"] if session else None,
                "csrf_token": session["csrf_token"] if session else None,
            },
            "provider_status": {
                "mode": "live",
                "label": "Live Real-Time Feed (NSE & BSE)",
                "live_available": True,
                "scraper_active": True,
                "database_total_stocks": get_stock_database().get_stock_count(),
            },
            "laya_ai": {
                "name": "Laya AI Intelligence Suite",
                "version": APP_VERSION,
                "model": os.environ.get("OLLAMA_MODEL", "gemma4:12b") + " (Local Ollama LLM)",
                "capabilities": [
                    "Deterministic indicator calculations",
                    "Local model explanations",
                    "Market snapshot summaries",
                    "Authenticated research chat",
                    "Deep Institutional Trade Briefing",
                    "Macro Market Pulse Synthesis",
                    "Interactive Copilot Chat, including after-hours research",
                    "On-demand context from stored candles, signals, results, margins and website formulas",
                    "Website-data answers when local Ollama is unavailable",
                ],
            },
            "market_schedule": get_market_schedule_info(),
        }
        self._send_json(payload)

    def _handle_market(self, query: Dict[str, List[str]]) -> None:
        session_req = (query.get("session", [""])[0] or query.get("mode", [""])[0]).lower()
        try:
            tf = int(query.get("timeframe", ["5"])[0])
        except ValueError:
            tf = 5

        if tf not in (1, 5, 15, 60):
            self._send_json({"error": "invalid_timeframe", "detail": "Choose 1, 5, 15 or 60 minute candles."}, status=400)
            return

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        state_info = get_session_state(candle_archive=get_candle_archive(), db_manager=self.db)
        session_state = state_info["session_state"]
        session_date = state_info["session_date"]

        requested_date = query.get("date", [""])[0].strip()
        if requested_date:
            try:
                parsed_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
                if parsed_date.isoformat() != requested_date or parsed_date > datetime.now(IST).date():
                    raise ValueError("Date is outside the received history")
            except (ValueError, TypeError):
                self._send_json({"error": "invalid_date", "detail": "Choose a valid session date no later than today."}, status=400)
                return
            session_date = requested_date
            snap = build_session_snapshot(session_date, timeframe=tf, db_manager=self.db, config=cfg)
            snap.update(session_state="PREVIOUS", session_date=session_date, data_mode="PREVIOUS_SESSION")
            self._send_json(snap)
            return

        # 1. If explicit ?session=previous requested, always return the completed previous session
        if session_req == "previous":
            session_date = resolve_previous_session_date(state_info, get_candle_archive(), self.db)
            prev_snapshot = build_session_snapshot(session_date, timeframe=tf, db_manager=self.db, config=cfg)
            prev_snapshot["session_state"] = "PREVIOUS"
            prev_snapshot["session_date"] = session_date
            prev_snapshot["data_mode"] = "PREVIOUS_SESSION"
            prev_snapshot["scorecard"] = get_session_scorecard(session_date, db_manager=self.db)
            self._send_json(prev_snapshot)
            return

        # 2. Live Session (09:15-15:30 on a regular trading day)
        if session_state == "LIVE":
            scraper_feed = get_live_scraper_feed()
            try:
                snapshot = scraper_feed.get_snapshot(timeframe=tf, config=cfg)
            except Exception as err:
                logger.error("Scraper snapshot fatal error: %s", err)
                snapshot = None

            if snapshot:
                ok_cnt = snapshot.get("feed", {}).get("ok_count", 0)
                stale_cnt = snapshot.get("feed", {}).get("stale_count", 0)
                if ok_cnt > 0 or stale_cnt > 0:
                    snapshot["data_mode"] = "LIVE"
                    snapshot["session_state"] = "LIVE"
                    snapshot["session_date"] = session_date
                    snapshot["as_of"] = snapshot.get("timestamp")
                    journal = getattr(self, "signal_journal", None) or SignalJournal(self.db)
                    try:
                        paper_ids = journal.log_snapshot_signals(snapshot, timeframe=tf)
                        get_paper_watchlist(self.db.db_path).observe_snapshot(snapshot, tf, cfg, paper_ids)
                        candles_by_symbol = {}
                        for s_sym, s_val in snapshot.get("stocks", {}).items():
                            c_list = s_val.get("candles")
                            if c_list:
                                candles_by_symbol[s_sym] = c_list
                        journal.update_outcomes(candles_by_symbol)
                    except Exception as je:
                        logger.debug("Journal update error: %s", je)
                    self._send_json(snapshot)
                    return

            # Live feed down: check if a completed stored session snapshot exists in SQLite
            try:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT payload_json FROM session_snapshots WHERE complete = 1 ORDER BY session_date DESC LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row:
                        stored_snap = json.loads(row["payload_json"])
                        stored_snap["data_mode"] = "PREVIOUS_SESSION"
                        stored_snap["session_state"] = "PREVIOUS"
                        stored_snap["scorecard"] = get_session_scorecard(stored_snap.get("session_date", session_date), db_manager=self.db)
                        stored_snap = enrich_daily_rankings(stored_snap, stored_snap.get("session_date", session_date), archive=get_candle_archive())
                        self._send_json(stored_snap)
                        return
            except Exception as e:
                logger.warning("Error loading stored session snapshot fallback: %s", e)

            # No stored snapshot -> HTTP 503 with NO data at all
            self._send_json(
                {
                    "error": "feed_unavailable",
                    "detail": "Live market feed is currently unavailable and no completed previous session snapshot stored.",
                    "data_mode": "UNAVAILABLE",
                },
                status=503,
            )
            return

        # 3. Closed Today (after 15:30 on trading day)
        if session_state == "CLOSED_TODAY":
            snap = build_session_snapshot(session_date, timeframe=tf, db_manager=self.db, config=cfg)
            snap["session_state"] = "CLOSED_TODAY"
            snap["session_date"] = session_date
            snap["data_mode"] = "CLOSED_SESSION"
            snap["scorecard"] = get_session_scorecard(session_date, db_manager=self.db)
            self._send_json(snap)
            return

        # 4. Previous Session (Weekend, Holiday, or Pre-Open)
        snap = build_session_snapshot(session_date, timeframe=tf, db_manager=self.db, config=cfg)
        snap["session_state"] = "PREVIOUS"
        snap["session_date"] = session_date
        snap["data_mode"] = "PREVIOUS_SESSION"
        snap["scorecard"] = get_session_scorecard(session_date, db_manager=self.db)
        self._send_json(snap)

    def _handle_session_summary(self, query: Dict[str, List[str]]) -> None:
        target_date = query.get("date", [""])[0].strip()
        state_info = get_session_state(candle_archive=get_candle_archive(), db_manager=self.db)
        if not target_date:
            target_date = state_info["session_date"]

        # Cache only: query session_snapshots table
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT payload_json, complete, data_source FROM session_snapshots WHERE session_date = ? AND timeframe = 5",
                    (target_date,),
                )
                row = cur.fetchone()
        except Exception as e:
            logger.warning("Error querying session_snapshots for summary: %s", e)
            row = None

        if not row:
            self._send_json(
                {
                    "error": "session_not_found",
                    "detail": f"No completed real session snapshot found for date {target_date}.",
                },
                status=404,
            )
            return

        snapshot_payload = json.loads(row["payload_json"])
        scorecard = get_session_scorecard(target_date, db_manager=self.db)

        self._send_json({
            "session_date": target_date,
            "complete": row["complete"],
            "data_source": row["data_source"],
            "snapshot": snapshot_payload,
            "scorecard": scorecard,
        })

    def _send_flow_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data).encode("utf-8")
        if len(raw) > 300 * 1024:
            logger.warning("/api/flow payload size %d bytes exceeds 300 KB limit", len(raw))

        accept_encoding = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_encoding:
            compressed = gzip.compress(raw)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(compressed)
        else:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(raw)

    def _handle_flow(self, query: Dict[str, List[str]]) -> None:
        session_param = query.get("session", ["auto"])[0].lower()
        date_param = query.get("date", [None])[0]
        if date_param:
            date_param = date_param.strip()
        try:
            timeframe = int(query.get("timeframe", ["5"])[0])
        except (ValueError, TypeError):
            timeframe = 5

        # 5-second in-memory cache per (session, date, tf)
        cache_key = (session_param, date_param or "", timeframe)
        now = time.time()
        if cache_key in _FLOW_CACHE:
            cached_time, cached_payload = _FLOW_CACHE[cache_key]
            if now - cached_time < 5.0:
                self._send_flow_json(cached_payload, status=200)
                return

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        payload = build_flow_payload(
            session_param=session_param,
            date_param=date_param,
            candle_archive=get_candle_archive(),
            db_manager=self.db,
            config=cfg,
        )

        if payload.get("empty"):
            self._send_flow_json(
                {
                    "error": "market_data_unavailable",
                    "detail": "Market data unavailable. No real session data exists for this period.",
                    "session_state": payload.get("session_state"),
                    "data_mode": "UNAVAILABLE",
                },
                status=503,
            )
            return

        _FLOW_CACHE[cache_key] = (now, payload)
        self._send_flow_json(payload, status=200)

    def _handle_market_schedule(self) -> None:
        self._send_json(get_market_schedule_info())

    def _handle_historical_symbols(self, query: Dict[str, List[str]]) -> None:
        search = query.get("q", [""])[0].strip()[:80]
        try:
            limit = min(25, max(1, int(query.get("limit", ["15"])[0])))
        except (TypeError, ValueError):
            limit = 15
        try:
            result = get_fyers_history_provider().search_instruments(search, limit=limit)
        except FyersHistoryError as exc:
            self._send_json({"error": "fyers_symbol_search_unavailable", "detail": str(exc)}, status=503)
            return
        self._send_json(result)

    def _handle_historical_candles(self, query: Dict[str, List[str]]) -> None:
        provider = get_fyers_history_provider()
        try:
            result = provider.get_candles(
                symbol=query.get("symbol", [""])[0],
                start=query.get("from", [""])[0],
                end=query.get("to", [""])[0],
                resolution=query.get("resolution", ["D"])[0],
            )
        except FyersHistoryNotConfigured as exc:
            self._send_json({"error": "fyers_not_configured", "detail": str(exc)}, status=503)
            return
        except FyersHistoryAuthError as exc:
            self._send_json({"error": "fyers_auth_error", "detail": str(exc)}, status=503)
            return
        except FyersHistoryRateLimitError as exc:
            self._send_json({"error": "fyers_rate_limited", "detail": str(exc)}, status=429)
            return
        except FyersHistoryError as exc:
            self._send_json({"error": "fyers_history_unavailable", "detail": str(exc)}, status=503)
            return
        self._send_json(result)

    def _handle_stock(self, query: Dict[str, List[str]]) -> None:
        symbol = query.get("symbol", ["RELIANCE"])[0].upper()
        exchange = query.get("exchange", ["NSE"])[0].upper()
        mode = query.get("mode", ["live"])[0].lower()
        try:
            tf = int(query.get("timeframe", ["5"])[0])
        except ValueError:
            tf = 5

        if tf not in (1, 5, 15, 60):
            self._send_json({"error": "invalid_timeframe"}, status=400)
            return
        archive = get_candle_archive()
        state_info = get_session_state(candle_archive=archive, db_manager=self.db)
        requested_date = query.get("date", [""])[0].strip()
        if requested_date:
            try:
                parsed_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
                if parsed_date.isoformat() != requested_date or parsed_date > datetime.now(IST).date():
                    raise ValueError("Invalid session date")
            except (ValueError, TypeError):
                self._send_json({"error": "invalid_date", "detail": "Choose a valid session date no later than today."}, status=400)
                return
            session_date = requested_date
        elif mode == "previous" or query.get("session", [""])[0] == "previous":
            session_date = resolve_previous_session_date(state_info, archive, self.db)
        else:
            session_date = state_info.get("session_date")
        # Read the selected session first. Never substitute a different day's
        # history when a date was selected in the workspace.
        candles = archive.get_candles_for_session(symbol, tf, session_date) if session_date else []
        fetched = []
        if not candles or (state_info.get("session_state") == "LIVE" and not requested_date and mode != "previous"):
            fetched = get_live_scraper_feed().get_full_stock_candles(symbol, timeframe=tf, exchange=exchange)
            filtered = [c for c in fetched if datetime.fromtimestamp(c["t"], IST).date().isoformat() == session_date]
            if filtered:
                candles = filtered
            elif not candles and fetched:
                candles = fetched
                session_date = datetime.fromtimestamp(candles[-1]["t"], IST).date().isoformat()
        if not candles:
            self._send_json(
                {"error": f"Candle data unavailable for symbol {symbol}."},
                status=404,
            )
            return

        db = get_stock_database()
        info = db.get_stock(symbol) or STOCK_CATALOGUE.get(symbol, {"name": symbol, "sector": "General"})

        # Compute stock metrics
        closes = [float(c["c"]) for c in candles]
        highs = [float(c["h"]) for c in candles]
        lows = [float(c["l"]) for c in candles]
        volumes = [int(c.get("v", 0)) for c in candles]
        price = closes[-1] if closes else 0.0
        open_price = float(candles[0]["o"]) if candles else price
        day_high = max(highs) if highs else price
        day_low = min(lows) if lows else price
        total_vol = sum(volumes)

        first_candle_ts = candles[0].get("t") or candles[0].get("ts", 0)
        try:
            prior_close = archive.get_prior_session_close(symbol, tf, first_candle_ts)
            if not isinstance(prior_close, (int, float)) or type(prior_close).__name__.startswith("MagicMock"):
                prior_close = None
        except Exception:
            prior_close = None

        if prior_close is not None and prior_close > 0:
            change_pct = round(((price - prior_close) / prior_close) * 100, 2)
            change_basis = "prev_close"
        else:
            change_pct = round(((price - open_price) / max(0.01, open_price)) * 100, 2) if open_price > 0 else 0.0
            change_basis = "open_price"

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        fast_period = cfg.get("fast_ema_period", 9)
        slow_period = cfg.get("slow_ema_period", 21)
        rsi_period = cfg.get("rsi_period", 14)
        score_thresh = cfg.get("score_threshold", 65)

        fast_ema_series = calculate_ema(closes, fast_period)
        slow_ema_series = calculate_ema(closes, slow_period)
        latest_fast_ema = fast_ema_series[-1] if fast_ema_series else price
        latest_slow_ema = slow_ema_series[-1] if slow_ema_series else price

        rsi = calculate_rsi(closes, rsi_period)
        vwap = calculate_vwap(candles)
        rvol = calculate_rvol(volumes)
        sup, res_val = calculate_support_resistance(candles)
        atr_val = calculate_atr(candles)
        order_blocks = calculate_order_blocks(candles)

        momentum = round(((closes[-1] - closes[-6]) / closes[-6]) * 100, 2) if len(closes) >= 6 else 0.0
        score_dict = calculate_composite_score(
            fast_ema=latest_fast_ema,
            slow_ema=latest_slow_ema,
            momentum=momentum,
            close=price,
            vwap=vwap,
            rsi=rsi,
            score_threshold=score_thresh,
            config=cfg,
        )
        if "raw" in score_dict:
            score_dict["raw"]["atr"] = atr_val

        cpr = calculate_cpr(day_high, day_low, prior_close if prior_close else price)
        camarilla = calculate_camarilla(day_high, day_low, prior_close if prior_close else price)
        supertrend = calculate_supertrend(candles, period=10, multiplier=3.0)
        vwap_bands = calculate_vwap_bands(candles)

        ai_decision = evaluate_ai_decision(
            symbol=symbol,
            price=price,
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

        self._send_json({
            "symbol": symbol,
            "name": info.get("name", symbol),
            "sector": info.get("sector", "General"),
            "exchange": info.get("exchange", "NSE/BSE"),
            "timeframe": tf,
            "count": len(candles),
            "session_date": session_date,
            "candles": candles,
            "price": price,
            "change_pct": change_pct,
            "change_basis": change_basis,
            "day_high": day_high,
            "day_low": day_low,
            "volume": total_vol,
            "metrics": score_dict,
            "indicators": {
                "fast_ema": latest_fast_ema,
                "slow_ema": latest_slow_ema,
                "rsi": rsi,
                "vwap": vwap,
                "rvol": rvol,
                "atr": atr_val,
                "supertrend": supertrend,
                "cpr": cpr,
                "camarilla": camarilla,
                "vwap_bands": vwap_bands,
                "support": sup,
                "resistance": res_val,
                "order_blocks": order_blocks,
            },
            "ai_decision": ai_decision,
            "updated_at": candles[-1]["t"] if candles else None,
            "source": "candles.db archive" if candles and not fetched else "Exchange Live Feed (Yahoo Finance)",
        })

    def _handle_historical_symbols(self, query: Dict[str, List[str]]) -> None:
        search = query.get("q", [""])[0].strip()[:80]
        try:
            limit = min(25, max(1, int(query.get("limit", ["15"])[0])))
        except (TypeError, ValueError):
            limit = 15
        try:
            result = get_fyers_history_provider().search_instruments(search, limit=limit)
        except FyersHistoryError as exc:
            self._send_json({"error": "fyers_symbol_search_unavailable", "detail": str(exc)}, status=503)
            return
        self._send_json(result)

    def _handle_historical_candles(self, query: Dict[str, List[str]]) -> None:
        provider = get_fyers_history_provider()
        try:
            result = provider.get_candles(
                symbol=query.get("symbol", [""])[0],
                start=query.get("from", [""])[0],
                end=query.get("to", [""])[0],
                resolution=query.get("resolution", ["D"])[0],
            )
        except FyersHistoryNotConfigured as exc:
            self._send_json({"error": "fyers_not_configured", "detail": str(exc)}, status=503)
            return
        except FyersHistoryAuthError as exc:
            self._send_json({"error": "fyers_auth_error", "detail": str(exc)}, status=503)
            return
        except FyersHistoryRateLimitError as exc:
            self._send_json({"error": "fyers_rate_limited", "detail": str(exc)}, status=429)
            return
        except FyersHistoryError as exc:
            self._send_json({"error": "fyers_history_unavailable", "detail": str(exc)}, status=503)
            return
        self._send_json(result)

    def _handle_intraday_margins(self) -> None:
        """Expose the published broker reference only for the website catalogue."""
        payload = dict(get_intraday_margins().payload())
        catalogue = dict(get_stock_database().get_company_symbols())
        records = payload.get("records", {})
        payload["source_record_count"] = payload.get("source_record_count", len(records))
        payload["source_eligible_count"] = payload.get(
            "source_eligible_count", payload.get("eligible_count", 0))
        payload["records"] = {symbol: {**row, "company_name": catalogue[symbol]}
                              for symbol, row in records.items() if symbol in catalogue}
        payload["record_count"] = len(payload["records"])
        payload["eligible_count"] = sum(row.get("eligible") is True for row in payload["records"].values())
        payload["catalogue_count"] = len(catalogue)
        self._send_json(payload)

    def _handle_results(self, query: Dict[str, List[str]]) -> None:
        """Read locally collected filings; browsers never trigger network backfills."""
        first = lambda key, default="": query.get(key, [default])[0]
        try:
            date_from, date_to = first("from"), first("to")
            for value in (date_from, date_to):
                if value:
                    datetime.strptime(value, "%Y-%m-%d")
            if date_from and date_to and date_from > date_to:
                raise ValueError("Start date must precede end date.")
            limit = int(first("limit", "100"))
            if not 1 <= limit <= 200:
                raise ValueError("Limit must be between 1 and 200.")
            offset = int(first("offset", "0"))
            if offset < 0 or offset > 100000:
                raise ValueError("Invalid page offset.")
            search = first("search").strip()[:100].casefold()
            tone = first("tone", "all")
            if tone not in ("all", "positive", "negative", "mixed"):
                raise ValueError("Unknown result filter.")
        except (ValueError, TypeError):
            self._send_json({"error": "invalid_results_filter", "detail": "Use ISO dates, a valid date range and a limit of 1–200."}, status=400)
            return
        data = get_results_service().list_events(date_from=date_from or None, date_to=date_to or None, symbol=first("symbol").upper()[:30], search=search, tone=tone, offset=offset, limit=limit)
        self._send_json(data)

    def _handle_result_detail(self, query: Dict[str, List[str]]) -> None:
        event_id = query.get("id", [""])[0]
        if not event_id or len(event_id) > 160:
            self._send_json({"error": "invalid_result_id"}, status=400)
            return
        service = get_results_service()
        event = service.get_event(event_id)
        if event is None:
            self._send_json({"error": "result_not_found"}, status=404)
            return
        symbol = event["symbol"]
        analysis = analyze_event(event, service.history(symbol), {symbol: service.get_candles(symbol)}, datetime.now(timezone.utc))
        self._send_json({"event": event, "analysis": analysis, "status": service.status()})

    def _handle_health(self) -> None:
        uptime = round(time.time() - server_start_time, 1)
        self._send_json({
            "status": "healthy",
            "service": "MarketScope Flow Terminal",
            "version": APP_VERSION,
            "uptime_seconds": uptime,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "feed_active": get_market_schedule_info().get("is_open", False),
        })

    def _handle_admin(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required."}, status=401)
            return
        if session.get("role") not in ("owner", "admin"):
            self._send_json({"error": "forbidden", "detail": "Admin or owner privilege required."}, status=403)
            return

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, email, role, plan, created_at, disabled FROM users")
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, action, actor, details, created_at FROM audit ORDER BY id DESC LIMIT 50")
            audit_log = [dict(r) for r in cur.fetchall()]

        self._send_json({"users": users, "audit": audit_log})

    def _handle_admin_audit_logs(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required."}, status=401)
            return
        if session.get("role") not in ("owner", "admin"):
            self._send_json({"error": "forbidden", "detail": "Admin or owner privilege required."}, status=403)
            return

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, action, actor, details, created_at FROM audit ORDER BY id DESC LIMIT 100")
            audit_log = [dict(r) for r in cur.fetchall()]

        self._send_json({"audit_logs": audit_log, "count": len(audit_log)})

    def _handle_config_history(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required."}, status=401)
            return
        if session.get("role") != "owner":
            self._send_json({"error": "forbidden", "detail": "Owner privilege required."}, status=403)
            return

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, config_json, actor, created_at FROM config_versions ORDER BY id DESC LIMIT 20")
            rows = [dict(r) for r in cur.fetchall()]
        self._send_json({"history": rows})

    def _handle_export(self, query: Dict[str, List[str]]) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required to export data."}, status=401)
            return
        try:
            tf = int(query.get("timeframe", ["5"])[0])
        except ValueError:
            tf = 5
        if tf not in (1, 5, 15, 60):
            self._send_json({"error": "invalid_timeframe"}, status=400)
            return

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        archive = get_candle_archive()
        state_info = get_session_state(candle_archive=archive, db_manager=self.db)
        selected_date = query.get("date", [""])[0].strip()
        if selected_date:
            try:
                parsed_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
                if parsed_date.isoformat() != selected_date or parsed_date > datetime.now(IST).date():
                    raise ValueError("Invalid session date")
            except (ValueError, TypeError):
                self._send_json({"error": "invalid_date", "detail": "Choose a valid session date no later than today."}, status=400)
                return
        elif query.get("session", [""])[0] == "previous":
            selected_date = resolve_previous_session_date(state_info, archive, self.db)
        else:
            selected_date = state_info.get("session_date")
        snapshot = None
        if state_info.get("session_state") == "LIVE" and selected_date == state_info.get("session_date"):
            try:
                snapshot = get_live_scraper_feed().get_snapshot(timeframe=tf, config=cfg)
                snapshot.update(session_date=selected_date, data_mode="LIVE")
            except Exception:
                pass
        if not snapshot or (snapshot.get("feed", {}).get("ok_count", 0) == 0 and snapshot.get("feed", {}).get("stale_count", 0) == 0):
            if selected_date:
                snapshot = build_session_snapshot(selected_date, timeframe=tf, db_manager=self.db, config=cfg)
        if not snapshot:
            self._send_json({"error": "session_unavailable", "detail": "No market data exists for the selected session."}, status=503)
            return
        filename = f"zien_quant_{selected_date}_{tf}m.csv"

        stocks = snapshot.get("stocks", {})

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Symbol", "Company", "Sector", "Status", "Price", "SessionOpen", "ChangePct",
            "OpenChangePct", "Volume", "RVOL", "VWAP", "Support", "Resistance",
            "Score", "Signal", "RSI", "FastEMA", "SlowEMA", "Updated", "SessionDate", "DataMode"
        ])

        for sym, s in stocks.items():
            status = s.get("status", "ok")
            metrics = s.get("metrics", {})
            raw = metrics.get("raw", {})
            # Stocks with status != 'ok' must be EXCLUDED from signal columns
            is_ok = (status == "ok")
            writer.writerow([
                sym,
                s.get("name"),
                s.get("sector"),
                status,
                s.get("price") if is_ok else "",
                s.get("session_open") if is_ok else "",
                s.get("change_pct") if is_ok else "",
                s.get("open_change_pct") if is_ok else "",
                s.get("volume") if is_ok else "",
                s.get("rvol") if is_ok else "",
                s.get("vwap") if is_ok else "",
                s.get("support") if is_ok else "",
                s.get("resistance") if is_ok else "",
                metrics.get("score") if is_ok else "",
                metrics.get("signal") if is_ok else "UNAVAILABLE",
                raw.get("rsi") if is_ok else "",
                raw.get("fast_ema") if is_ok else "",
                raw.get("slow_ema") if is_ok else "",
                s.get("updated_at"),
                selected_date,
                snapshot.get("data_mode", "PREVIOUS_SESSION"),
            ])

        csv_text = output.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(csv_text.encode("utf-8"))))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(csv_text.encode("utf-8"))

    def _handle_backup_status(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required."}, status=401)
            return
        if session.get("role") != "owner":
            self._send_json({"error": "forbidden", "detail": "Owner privilege required."}, status=403)
            return

        backup_dir = BASE_DIR.parent / "work" / "backups"
        backups = []
        if backup_dir.exists():
            for p in sorted(backup_dir.glob("*.db"), key=os.path.getmtime, reverse=True):
                backups.append({"name": p.name, "size_bytes": p.stat().st_size, "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()})
        self._send_json({"backup_count": len(backups), "backups": backups[:10]})

    def _handle_backup(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required."}, status=401)
            return
        if session.get("role") != "owner":
            self._send_json({"error": "forbidden", "detail": "Owner privilege required."}, status=403)
            return

        backup_dir = BASE_DIR.parent / "work" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"marketscope_backup_{ts}.db"
        dest_path = backup_dir / backup_name
        with closing(sqlite3.connect(self.db.db_path)) as source, closing(sqlite3.connect(str(dest_path))) as destination:
            source.backup(destination)
        with self.db.get_connection() as conn:
            self.db.log_audit(conn, "create_backup", session.get("email", "owner"), f"Backup created: {backup_name}")
        self._send_json({"status": "success", "backup": backup_name, "path": str(dest_path), "created_at": datetime.now(timezone.utc).isoformat()})

    def _handle_ai_status(self) -> None:
        engine = get_laya_engine()
        status = engine.check_status()
        status.update(chat_available_after_hours=True, chat_requires_owner_sign_in=True,
                      chat_data_mode="latest website snapshot and locally stored records")
        self._send_json(status)

    def _handle_ai_pulse(self, query: Dict[str, List[str]]) -> None:
        force_refresh = query.get("refresh", ["0"])[0] in ("1", "true")
        poll = query.get("poll", ["0"])[0] in ("1", "true")
        if force_refresh:
            session = self._get_current_session()
            if not session:
                self._send_json({"error": "auth_required", "detail": "Authentication required to refresh market pulse."}, status=401)
                return

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        snapshot = None
        state_info = get_session_state(candle_archive=get_candle_archive(), db_manager=self.db)
        session_state = state_info.get("session_state")
        session_date = state_info.get("session_date")

        if session_state in ("PREVIOUS", "CLOSED_TODAY"):
            if session_date:
                snapshot = build_session_snapshot(session_date, timeframe=5, db_manager=self.db, config=cfg)
        else:
            try:
                snapshot = get_live_scraper_feed().get_snapshot(timeframe=5, config=cfg)
            except Exception as e:
                if session_date:
                    snapshot = build_session_snapshot(session_date, timeframe=5, db_manager=self.db, config=cfg)
                if not snapshot:
                    self._send_json({"error": "feed_unavailable", "detail": f"Live feed error: {e}"}, status=503)
                    return
            if snapshot and snapshot.get("feed", {}).get("ok_count", 0) == 0 and snapshot.get("feed", {}).get("stale_count", 0) == 0:
                if session_date:
                    snapshot = build_session_snapshot(session_date, timeframe=5, db_manager=self.db, config=cfg)
                if not snapshot or snapshot.get("feed", {}).get("ok_count", 0) == 0:
                    self._send_json({"error": "feed_unavailable", "detail": "Live market data unavailable for market pulse."}, status=503)
                    return

        if not snapshot:
            self._send_json({"error": "feed_unavailable", "detail": "Market data unavailable for market pulse."}, status=503)
            return

        engine = get_laya_engine()
        pulse = engine.get_market_pulse(snapshot, force_refresh=force_refresh, poll=poll)
        self._send_json(pulse)

    def _handle_ai_analyze(self, query: Optional[Dict[str, List[str]]] = None) -> None:
        client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"
        if query is not None:
            symbol = query.get("symbol", ["RELIANCE"])[0].upper()
            mode = query.get("mode", ["live"])[0].lower()
            poll = query.get("poll", ["0"])[0] in ("1", "true", "True")
            try:
                tf = int(query.get("timeframe", ["5"])[0])
            except (ValueError, TypeError):
                tf = 5
            force_refresh = query.get("refresh", ["0"])[0] in ("1", "true", "True")
        else:
            body = self._parse_json_body() or {}
            symbol = str(body.get("symbol", "RELIANCE")).upper()
            mode = str(body.get("mode", "live")).lower()
            poll = bool(body.get("poll", False))
            try:
                tf = int(body.get("timeframe", 5))
            except (ValueError, TypeError):
                tf = 5
            force_refresh = bool(body.get("refresh", False))

        session = self._get_current_session()
        if not poll:
            limiter = rate_limiter_llm_auth if session else rate_limiter_llm
            max_desc = "Max 60 req/min for authenticated sessions." if session else "Max 6 req/min."
            if not limiter.is_allowed(client_ip):
                self._send_rate_limited(retry_after=limiter.retry_after(client_ip), detail=f"AI endpoint rate limit exceeded. {max_desc}")
                return

        if force_refresh:
            if not session:
                self._send_json({"error": "auth_required", "detail": "Authentication required to refresh analysis."}, status=401)
                return

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        # Reject unknown symbols before a feed outage can mask a missing symbol.
        # Keep catalogue entries and owner-tracked symbols available offline.
        known_symbols = cfg.get("monitored_symbols", [])
        if symbol not in STOCK_CATALOGUE and symbol not in known_symbols and not get_stock_database().get_stock(symbol):
            self._send_json({"error": f"Symbol '{symbol}' not found in active universe."}, status=404)
            return
        snapshot = None
        state_info = get_session_state(candle_archive=get_candle_archive(), db_manager=self.db)
        session_state = state_info.get("session_state")
        session_date = state_info.get("session_date")
        if mode not in ("live", "previous"):
            mode = "live"
        if mode == "previous" and session_state in ("LIVE", "CLOSED_TODAY"):
            previous_clock = datetime.now(IST).replace(hour=8, minute=59, second=0, microsecond=0)
            previous_state = get_session_state(clock_dt=previous_clock, candle_archive=get_candle_archive(), db_manager=self.db)
            session_state = previous_state.get("session_state")
            session_date = previous_state.get("session_date")

        if session_state in ("PREVIOUS", "CLOSED_TODAY") and session_date:
            snapshot = build_session_snapshot(session_date, timeframe=tf, db_manager=self.db, config=cfg, allow_remote_fallback=False)
        else:
            try:
                snapshot = get_live_scraper_feed().get_snapshot(timeframe=tf, config=cfg)
            except Exception as e:
                if session_date:
                    snapshot = build_session_snapshot(session_date, timeframe=tf, db_manager=self.db, config=cfg, allow_remote_fallback=False)
                if not snapshot:
                    self._send_json({"error": "feed_unavailable", "detail": f"Live feed error: {e}"}, status=503)
                    return

        stock_data = snapshot.get("stocks", {}).get(symbol) if snapshot else None
        if not stock_data and session_state == "LIVE":
            try:
                proc = get_live_scraper_feed()._process_stock(symbol, timeframe=tf, config=cfg)
                if proc:
                    stock_data = proc[1]
            except Exception:
                pass
        if not stock_data and session_date:
            archive = get_candle_archive()
            candles = archive.get_candles_for_session(symbol, tf, session_date)
            if candles and len(candles) >= 5:
                from marketscope.decision_engine import score_stock
                score_dict = score_stock(symbol, candles, timeframe=tf, config=cfg)
                closes = [float(c.get("c", 0)) for c in candles if c.get("c") is not None]
                stock_data = {
                    "symbol": symbol, "status": "ok", "source": "archive",
                    "price": closes[-1] if closes else None,
                    "session_open": candles[0].get("o"),
                    "day_high": max(float(c.get("h", 0)) for c in candles),
                    "day_low": min(float(c.get("l", 0)) for c in candles),
                    "change_pct": None,
                    "metrics": score_dict,
                    "candles": candles,
                    "session_date": session_date,
                    "session_state": session_state,
                }

        if not stock_data:
            self._send_json({"error": f"Symbol '{symbol}' not found in active universe."}, status=404)
            return

        if stock_data.get("status") not in (None, "ok", "stale"):
            err_msg = str(stock_data.get("error", "failed"))
            if "404" in err_msg or "not found" in err_msg.lower():
                self._send_json({"error": f"Symbol '{symbol}' not found in active universe."}, status=404)
                return
            self._send_json(
                {"error": f"Real-time market data unavailable for symbol '{symbol}': {err_msg}"},
                status=503,
            )
            return

        engine = get_laya_engine()
        analysis = engine.analyze_stock(symbol, stock_data, timeframe=tf, force_refresh=force_refresh, poll=poll)
        if analysis.get("queue_full"):
            self._send_json({"error": "ai_queue_full", "detail": "Laya AI queue full (max 20 requests). Please retry in a few seconds."}, status=429)
            return
        self._send_json(analysis)

    def _handle_ai_chat(self) -> None:
        body = self._parse_json_body() or {}
        client_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"
        is_local = client_ip in ("127.0.0.1", "::1", "localhost")
        is_terminal = is_local and (body.get("client") == "bloomberg_terminal" or (hasattr(self, "headers") and self.headers and self.headers.get("X-Terminal-Client") == "bloomberg"))
        session = self._get_current_session()
        if not session and not is_terminal:
            self._send_json({"error": "auth_required", "detail": "Authentication required for AI copilot chat."}, status=401)
            return
        message = str(body.get("message", "")).strip()
        symbol = body.get("symbol")
        mode = str(body.get("mode", "live")).lower()
        if symbol:
            symbol = str(symbol).strip().upper()
            if not re.fullmatch(r"[A-Z0-9&._-]{1,30}", symbol):
                symbol = None

        if not message:
            self._send_json({"error": "Message is required."}, status=400)
            return
        if len(message) > 500:
            self._send_json({"error": "Message must be 500 characters or fewer."}, status=400)
            return

        try:
            timeframe = int(body.get("timeframe", 5))
        except (TypeError, ValueError):
            timeframe = 5
        if timeframe not in (1, 5, 15, 60):
            timeframe = 5
        history = body.get("history", [])
        if not isinstance(history, list):
            history = []
        watchlist = body.get("watchlist", [])
        if not isinstance(watchlist, list):
            watchlist = []

        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        snapshot = None
        state_info = get_session_state(candle_archive=get_candle_archive(), db_manager=self.db)
        session_state = str(state_info.get("session_state") or "UNAVAILABLE")
        session_date = state_info.get("session_date")

        if mode not in ("live", "previous"):
            mode = "live"
        requested_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
        historical_date = None
        if requested_date:
            try:
                historical_date = datetime.strptime(requested_date.group(1), "%Y-%m-%d").date().isoformat()
            except ValueError:
                pass
        if historical_date:
            session_state = "HISTORICAL"
            session_date = historical_date
        elif mode == "previous" and session_state in ("LIVE", "CLOSED_TODAY"):
            # Resolve the last completed session from stored candles/snapshots,
            # matching the website's explicit Previous session view.
            previous_clock = datetime.now(IST).replace(hour=8, minute=59, second=0, microsecond=0)
            previous_state = get_session_state(clock_dt=previous_clock, candle_archive=get_candle_archive(), db_manager=self.db)
            session_state = str(previous_state.get("session_state") or "PREVIOUS")
            session_date = previous_state.get("session_date")

        # Use the exchange session state to choose live versus stored data. The
        # assistant itself is available at every time of day, including holidays.
        if session_state in ("PREVIOUS", "CLOSED_TODAY", "HISTORICAL") and session_date:
            try:
                snapshot = build_session_snapshot(session_date, timeframe=timeframe, db_manager=self.db, config=cfg, allow_remote_fallback=False)
            except Exception as exc:
                logger.info("Laya could not assemble the stored session snapshot: %s", type(exc).__name__)
        else:
            try:
                snapshot = get_live_scraper_feed().get_snapshot(timeframe=timeframe, config=cfg)
            except Exception as exc:
                logger.info("Laya live snapshot unavailable: %s", type(exc).__name__)
            if not snapshot and session_date:
                try:
                    snapshot = build_session_snapshot(session_date, timeframe=timeframe, db_manager=self.db, config=cfg, allow_remote_fallback=False)
                    session_state = "STORED_FALLBACK"
                except Exception as exc:
                    logger.info("Laya stored snapshot fallback unavailable: %s", type(exc).__name__)

        if not isinstance(snapshot, dict):
            snapshot = {"session_date": session_date, "session_state": session_state, "stocks": {}}

        website_context = build_laya_website_context(
            query=message,
            snapshot=snapshot,
            selected_symbol=symbol,
            session_state=session_state,
            session_date=session_date,
            timeframe=timeframe,
            config=cfg,
            db_manager=self.db,
            watchlist=watchlist,
        )

        engine = get_laya_engine()
        res = engine.chat_copilot(
            query=message,
            current_symbol=(website_context.get("symbols") or [symbol])[0],
            snapshot_data=snapshot,
            website_context=website_context["text"],
            history=history,
        )
        res["context"] = {
            "session_state": session_state,
            "session_date": session_date,
            "stocks_available": website_context.get("snapshot_stock_count", 0),
            "symbols_resolved": website_context.get("symbols", []),
        }
        if res.get("error"):
            self._send_json(res, status=503)
            return
        self._send_json(res)

    def _handle_stocks_database(self, query: Dict[str, List[str]]) -> None:
        q = query.get("q", [""])[0]
        exchange = query.get("exchange", ["ALL"])[0]
        try:
            limit = min(100, int(query.get("limit", ["50"])[0]))
        except ValueError:
            limit = 50

        db = get_stock_database()
        results = db.search_stocks(query=q, exchange=exchange, limit=limit)
        self._send_json({
            "total_matches": len(results),
            "total_database": db.get_stock_count(),
            "query": q,
            "exchange": exchange,
            "stocks": results,
        })

    def _handle_stocks_quote(self, query: Dict[str, List[str]]) -> None:
        symbol = query.get("symbol", ["RELIANCE"])[0].upper()
        exchange = query.get("exchange", ["NSE"])[0].upper()
        scraper = get_live_scraper_feed()
        res = scraper.scrape_stock_candles(symbol, timeframe=5, exchange=exchange)
        self._send_json(res)

    def _handle_stocks_track(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required to track symbols."}, status=401)
            return

        body = self._parse_json_body() or {}
        raw_symbol = str(body.get("symbol", "")).strip().upper()
        if not raw_symbol or not re.match(r"^[A-Z0-9&\-]{1,20}$", raw_symbol):
            self._send_json({"error": "invalid_symbol", "detail": "Symbol format invalid. Must match ^[A-Z0-9&-]{1,20}$"}, status=400)
            return

        # Check symbol exists in stock_master.db
        db = get_stock_database()
        stock = db.get_stock(raw_symbol)
        if not stock:
            self._send_json({"error": "symbol_not_found", "detail": f"Symbol '{raw_symbol}' not found in stock master database."}, status=404)
            return

        scraper = get_live_scraper_feed()
        try:
            cfg = self.db.get_latest_config()
            if not isinstance(cfg, dict):
                cfg = DEFAULT_CONFIG
        except Exception:
            cfg = DEFAULT_CONFIG
        max_tracked = int(cfg.get("max_tracked_symbols", 100))

        clean = raw_symbol.replace(".NS", "").replace(".BO", "")
        with scraper._lock:
            if clean not in scraper.monitored_symbols and len(scraper.monitored_symbols) >= max_tracked:
                self._send_json({"error": "tracking_limit_reached", "detail": f"Tracking limit of {max_tracked} symbols reached.", "max": max_tracked}, status=409)
                return

        added = scraper.add_symbol_to_track(raw_symbol)
        self._send_json({
            "status": "success",
            "symbol": raw_symbol,
            "added": added,
            "total_monitored": len(scraper.monitored_symbols),
        })

    def _handle_stocks_untrack(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required to untrack symbols."}, status=401)
            return

        body = self._parse_json_body() or {}
        raw_symbol = str(body.get("symbol", "")).strip().upper()
        if not raw_symbol or not re.match(r"^[A-Z0-9&\-]{1,20}$", raw_symbol):
            self._send_json({"error": "invalid_symbol", "detail": "Symbol format invalid. Must match ^[A-Z0-9&-]{1,20}$"}, status=400)
            return

        scraper = get_live_scraper_feed()
        removed = scraper.remove_symbol_to_track(raw_symbol)
        self._send_json({
            "status": "success",
            "symbol": raw_symbol,
            "removed": removed,
            "total_monitored": len(scraper.monitored_symbols),
        })

    def _handle_signals_stats(self, query: Optional[Dict[str, List[str]]] = None) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required to view signal statistics."}, status=401)
            return
        include_gated = False
        if query:
            include_gated = query.get("include_gated", ["0"])[0] in ("1", "true", "True")
        journal = getattr(self, "signal_journal", None) or SignalJournal(self.db)
        stats = journal.get_signal_stats(include_gated=include_gated)
        self._send_json(stats)

    def _handle_login(self) -> None:
        client_ip = self.client_address[0] if getattr(self, "client_address", None) else "127.0.0.1"
        if not rate_limiter_login.is_allowed(client_ip):
            self._send_rate_limited(retry_after=rate_limiter_login.retry_after(client_ip), detail="Too many sign-in attempts. Wait a minute before trying again.")
            return
        body = self._parse_json_body()
        if not isinstance(body, dict):
            self._send_json({"error": "invalid_login_payload", "detail": "Enter your email and password."}, status=400)
            return

        email, password, totp_code = body.get("email", ""), body.get("password", ""), body.get("totp_code", "")
        if not all(isinstance(value, str) for value in (email, password, totp_code)):
            self._send_json({"error": "invalid_login_payload", "detail": "Email, password and authenticator code must be text."}, status=400)
            return
        email, totp_code = email.strip().lower(), totp_code.strip()
        if not email or not password or len(email) > 254 or len(password) > 1024:
            self._send_json({"error": "invalid_login_payload", "detail": "Enter your email and password."}, status=400)
            return

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email = ? AND disabled = 0", (email,))
            user = cur.fetchone()

        if not user or not verify_password(password, user["password_hash"]):
            self._send_json({"error": "invalid_credentials", "detail": "Email or password is incorrect."}, status=401)
            return

        new_step = None
        if user["totp_secret"]:
            if not totp_code:
                self._send_json({"error": "mfa_required", "detail": "Enter the six-digit code from the authenticator enrolled for this account.", "requires_mfa": True}, status=400)
                return
            valid, new_step = verify_totp(user["totp_secret"], totp_code, last_step=user["totp_last_step"])
            if not valid:
                self._send_json({"error": "invalid_mfa", "detail": "The authenticator code is invalid, expired or already used. Try a fresh code.", "requires_mfa": True}, status=401)
                return

        issued = self.db.create_authenticated_session(
            user["id"], new_step,
            expected_credentials={"password_hash": user["password_hash"], "totp_secret": user["totp_secret"]},
        )
        if not issued:
            self._send_json({"error": "login_state_changed", "detail": "Account or authenticator state changed. Try a fresh sign-in.", "requires_mfa": bool(user["totp_secret"])}, status=401)
            return
        raw_token, csrf_token = issued
        cookie = SimpleCookie()
        cookie["ms_session"] = raw_token
        cookie["ms_session"]["httponly"] = True
        cookie["ms_session"]["samesite"] = "Strict"
        cookie["ms_session"]["path"] = "/"
        cookie["ms_session"]["max-age"] = SESSION_LIFETIME_HOURS * 3600
        if os.environ.get("MS_COOKIE_SECURE", "").lower() in ("1", "true", "yes"):
            cookie["ms_session"]["secure"] = True

        self._send_json(
            {
                "success": True,
                "user": {"email": user["email"], "role": user["role"], "plan": user["plan"]},
                "csrf_token": csrf_token,
            },
            cookies=[cookie["ms_session"].OutputString()],
        )

    def _handle_logout(self) -> None:
        raw_token = self._get_cookie_token()
        if raw_token:
            self.db.delete_session(raw_token)
        cookie = SimpleCookie()
        cookie["ms_session"] = ""
        cookie["ms_session"]["httponly"] = True
        cookie["ms_session"]["samesite"] = "Strict"
        cookie["ms_session"]["path"] = "/"
        cookie["ms_session"]["max-age"] = 0
        if os.environ.get("MS_COOKIE_SECURE", "").lower() in ("1", "true", "yes"):
            cookie["ms_session"]["secure"] = True
        self._send_json({"success": True}, cookies=[cookie["ms_session"].OutputString()])

    def _handle_config_preview(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Sign in to preview configuration changes."}, status=401)
            return
        if session.get("role") != "owner":
            self._send_json({"error": "forbidden", "detail": "Owner privilege required."}, status=403)
            return
        body = self._parse_json_body() or {}
        try:
            fast = int(body.get("fast_ema_period", 9))
            slow = int(body.get("slow_ema_period", 21))
            rsi = int(body.get("rsi_period", 14))
            thresh = int(body.get("score_threshold", 65))
        except (ValueError, TypeError, OverflowError):
            self._send_json({"error": "invalid_config", "detail": "Indicator settings must be whole numbers."}, status=400)
            return

        if not (0 < fast < slow <= 500 and 1 <= rsi <= 100 and 1 <= thresh <= 100):
            self._send_json({"error": "invalid_config", "detail": "Choose valid indicator periods with fast EMA below slow EMA."}, status=400)
            return

        temp_cfg = dict(DEFAULT_CONFIG)
        temp_cfg.update({"fast_ema_period": fast, "slow_ema_period": slow, "rsi_period": rsi, "score_threshold": thresh})

        state_info = get_session_state(candle_archive=get_candle_archive(), db_manager=self.db)
        session_date = state_info.get("session_date")
        snapshot = None
        if session_date:
            snapshot = build_session_snapshot(session_date, timeframe=5, db_manager=self.db, config=temp_cfg)
        if not snapshot:
            snapshot = get_live_scraper_feed().get_snapshot(timeframe=5, config=temp_cfg)
        self._send_json({"preview": True, "snapshot": snapshot})

    def _handle_config_update(self) -> None:
        session = self._get_current_session()
        if not session:
            self._send_json({"error": "auth_required", "detail": "Authentication required."}, status=401)
            return
        if session.get("role") != "owner":
            self._send_json({"error": "forbidden", "detail": "Owner privilege required."}, status=403)
            return

        body = self._parse_json_body()
        if not body:
            self._send_json({"error": "Missing payload"}, status=400)
            return

        # CSRF check
        client_csrf = self.headers.get("X-CSRF-Token")
        if not client_csrf or client_csrf != session.get("csrf_token"):
            self._send_json({"error": "Invalid CSRF token"}, status=403)
            return

        current_cfg = self.db.get_latest_config() or dict(DEFAULT_CONFIG)
        merged = dict(current_cfg)
        merged.update(body)

        try:
            merged = normalize_config_numbers(merged)
            fast = int(merged.get("fast_ema_period", 9))
            slow = int(merged.get("slow_ema_period", 21))
            if fast <= 0 or slow <= 0 or fast >= slow:
                self._send_json({"error": "Fast EMA must be positive and strictly less than Slow EMA"}, status=400)
                return

            w_trend = float(merged.get("weight_trend", 35.0))
            w_mom = float(merged.get("weight_momentum", 30.0))
            w_vwap = float(merged.get("weight_vwap", 20.0))
            w_rsi = float(merged.get("weight_rsi", 15.0))
            if any(w < 0 for w in (w_trend, w_mom, w_vwap, w_rsi)):
                self._send_json({"error": "Composite weights must be non-negative"}, status=400)
                return
            if abs((w_trend + w_mom + w_vwap + w_rsi) - 100.0) > 0.01:
                self._send_json({"error": f"Composite weights must sum to 100 (got {w_trend + w_mom + w_vwap + w_rsi:.2f})"}, status=400)
                return

            rsi_period = int(merged.get("rsi_period", 14))
            if rsi_period <= 0 or rsi_period > 100:
                self._send_json({"error": "RSI period must be between 1 and 100"}, status=400)
                return

            rsi_pb_long_min = float(merged.get("rsi_pullback_long_min", 42.0))
            rsi_pb_long_max = float(merged.get("rsi_pullback_long_max", 68.0))
            rsi_pb_short_min = float(merged.get("rsi_pullback_short_min", 32.0))
            rsi_pb_short_max = float(merged.get("rsi_pullback_short_max", 58.0))
            rsi_ob = float(merged.get("rsi_overbought", 76.0))
            for rsi_val, name in [
                (rsi_pb_long_min, "rsi_pullback_long_min"),
                (rsi_pb_long_max, "rsi_pullback_long_max"),
                (rsi_pb_short_min, "rsi_pullback_short_min"),
                (rsi_pb_short_max, "rsi_pullback_short_max"),
                (rsi_ob, "rsi_overbought"),
            ]:
                if not (0.0 <= rsi_val <= 100.0):
                    self._send_json({"error": f"RSI threshold {name} must be within [0, 100]"}, status=400)
                    return
            if rsi_pb_long_min >= rsi_pb_long_max or rsi_pb_short_min >= rsi_pb_short_max:
                self._send_json({"error": "RSI pullback minimum must be strictly less than maximum"}, status=400)
                return

            score_bd_short = int(merged.get("score_breakdown_short", 30))
            score_pb_short = int(merged.get("score_pullback_short", 35))
            score_pb_long = int(merged.get("score_pullback_long", 65))
            score_bo_long = int(merged.get("score_breakout_long", 70))
            for sc, name in [
                (score_bd_short, "score_breakdown_short"),
                (score_pb_short, "score_pullback_short"),
                (score_pb_long, "score_pullback_long"),
                (score_bo_long, "score_breakout_long"),
            ]:
                if not (0 <= sc <= 100):
                    self._send_json({"error": f"Score cutoff {name} must be within [0, 100]"}, status=400)
                    return
            if not (score_bd_short <= score_pb_short < score_pb_long <= score_bo_long):
                self._send_json({"error": "Score cutoffs must be ordered: breakdown_short <= pullback_short < pullback_long <= breakout_long"}, status=400)
                return

            min_rr = float(merged.get("min_rr_hurdle", 1.5))
            if min_rr < 1.0 or min_rr > 10.0:
                self._send_json({"error": "Minimum R:R hurdle must be between 1.0 and 10.0"}, status=400)
                return
        except (ValueError, TypeError, OverflowError) as err:
            self._send_json({"error": f"Invalid configuration parameter format: {err}"}, status=400)
            return

        version_id = self.db.save_config(merged, actor=session["email"])

        self._send_json({"success": True, "version_id": version_id, "config": merged})

    def _handle_metrics(self) -> None:
        # Telemetry ingestion with contract validation fix:
        # Handles both epoch timestamp and elapsed milliseconds
        body = self._parse_json_body()
        if not body:
            self._send_json({"error": "Empty body"}, status=400)
            return

        try:
            raw_render = body.get("render_ms", 0.0)
            raw_latency = body.get("feed_latency_ms", 0.0)
            if any(isinstance(value, bool) or not isinstance(value, (int, float, str))
                   for value in (raw_render, raw_latency)):
                raise ValueError("Metric durations must be numbers")
            raw_render, raw_latency = float(raw_render), float(raw_latency)
            if any(not math.isfinite(value) or value < 0 for value in (raw_render, raw_latency)):
                raise ValueError("Metric durations must be finite non-negative numbers")
        except (ValueError, TypeError, OverflowError) as error:
            self._send_json({"error": "invalid_metrics", "detail": str(error)}, status=400)
            return
        # If timestamp is passed (e.g. > 1e11 ms), calculate delta or normalize
        if raw_render > 1_000_000_000_000:
            render_duration = min(60_000.0, max(0.0, time.time() * 1000 - raw_render))
        else:
            render_duration = min(float(raw_render), 60_000.0)

        latency_ms = min(raw_latency, 60_000.0)
        client_ts = body.get("client_timestamp")

        try:
            self.db.record_metric(render_duration, latency_ms, client_ts)
            self._send_json({"recorded": True})
        except Exception as e:
            self._send_json({"recorded": False, "error": str(e)}, status=500)


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT", "8765"))
    env_name = os.environ.get("MS_ENV", "production")
    db = DatabaseManager(DEFAULT_DB_PATH)
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, MarketScopeHandler)
    httpd.db_manager = db
    results = get_results_service()
    results.start()
    paper = get_paper_session()
    paper.start_worker()
    watchlist = get_paper_watchlist(db.db_path)
    watchlist.start(db)
    margins = get_intraday_margins()
    margins.start()
    print(f"============================================================")
    print(f" MarketScope NSE Flow Terminal running on http://{host}:{port}")
    print(f" Mode: Market Data Feed & Previous Session Archive [{env_name}]")
    print(f" Database: {DEFAULT_DB_PATH}")
    print(f"============================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        watchlist.stop()
        paper.stop()
        margins.stop()
        results.stop()
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MarketScope HTTP Server")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Host address to bind")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")), help="Port to listen on")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
