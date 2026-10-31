"""
MarketScope Security Primitives
PBKDF2-HMAC-SHA256 password hashing, RFC 6238 TOTP with replay protection,
cryptographic session handling, CSRF defense, rate limiting, and SQLite schema migrations.
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PBKDF2_ITERATIONS = 600_000
SESSION_LIFETIME_HOURS = 12


def hash_password(password: str) -> str:
    """Hash password using Salted PBKDF2-HMAC-SHA256 with 600,000 iterations."""
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    key_b64 = base64.b64encode(key).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${key_b64}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored PBKDF2 hash."""
    try:
        parts = hashed.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2].encode("ascii"), validate=True)
        expected_key = base64.b64decode(parts[3].encode("ascii"), validate=True)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(derived, expected_key)
    except Exception:
        return False


def generate_totp_secret() -> str:
    """Generate base32 secret for TOTP (RFC 6238)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def get_totp_token(secret: str, time_step: Optional[int] = None) -> Tuple[str, int]:
    """Calculate 6-digit TOTP token and current time step (30s window)."""
    if time_step is None:
        time_step = int(time.time() // 30)

    # Decode base32 with padding if needed
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded.encode("ascii"), casefold=True)
    msg = struct.pack(">Q", time_step)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    token = f"{code_int % 1_000_000:06d}"
    return token, time_step


def verify_totp(secret: str, code: str, last_step: int = 0) -> Tuple[bool, int]:
    """
    Verify TOTP token allowing current, previous, and next step (+-1 window).
    Enforces replay protection by rejecting steps <= last_step.
    """
    if not isinstance(code, str):
        return False, last_step
    code = code.strip()
    if not secret or len(code) != 6 or not code.isascii() or not code.isdigit():
        return False, last_step

    current_step = int(time.time() // 30)
    for step in (current_step, current_step - 1, current_step + 1):
        if step <= last_step:
            continue
        try:
            token, _ = get_totp_token(secret, time_step=step)
        except (ValueError, TypeError):
            return False, last_step
        if hmac.compare_digest(token, code):
            return True, step
    return False, last_step


def hash_token(token: str) -> str:
    """Hash raw session/recovery token using SHA-256 for secure DB lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RateLimiter:
    """Simple in-memory sliding window rate limiter."""

    def __init__(self, limit: int = 60, window_sec: int = 60):
        self.limit = limit
        self.window_sec = window_sec
        self.history: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_sec
        with self._lock:
            entries = [t for t in self.history.get(key, []) if t > cutoff]
            if len(entries) >= self.limit:
                self.history[key] = entries
                return False
            entries.append(now)
            self.history[key] = entries
        return True

    def retry_after(self, key: str) -> int:
        now = time.time()
        cutoff = now - self.window_sec
        with self._lock:
            entries = [t for t in self.history.get(key, []) if t > cutoff]
        if not entries:
            return 1
        return max(1, int(entries[0] + self.window_sec - now) + 1)


class DatabaseManager:
    """SQLite Database Manager with migrations, WAL mode, and session handling."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        mig_dir = Path(__file__).parent / "migrations"
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
            """)
            conn.commit()

            if mig_dir.exists():
                for sql_file in sorted(mig_dir.glob("*.sql")):
                    prefix = sql_file.name.split("_")[0]
                    try:
                        v = int(prefix)
                    except ValueError:
                        continue
                    cur.execute("SELECT version FROM schema_migrations WHERE version = ?", (v,))
                    if not cur.fetchone():
                        with open(sql_file, "r", encoding="utf-8") as f:
                            sql = f.read()
                        conn.executescript(sql)
                        cur.execute(
                            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                            (v, datetime.now(timezone.utc).isoformat()),
                        )
                        conn.commit()
        finally:
            conn.close()

    def has_owner(self) -> bool:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1")
            return cur.fetchone() is not None
        finally:
            conn.close()

    def create_user(
        self,
        email: str,
        password: str,
        role: str = "member",
        totp_secret: Optional[str] = None,
        plan: str = "standard",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        pwd_hash = hash_password(password)
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, role, totp_secret, totp_last_step, disabled, plan, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)
                    """,
                    (email.lower().strip(), pwd_hash, role, totp_secret, plan, now, now),
                )
                uid = cur.lastrowid
                self.log_audit(conn, "user_created", email, f"Role: {role}")
                return uid
        finally:
            conn.close()

    def create_session(self, user_id: int) -> Tuple[str, str]:
        """Create session returning raw token and csrf_token."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)
        csrf_token = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=SESSION_LIFETIME_HOURS)

        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (token_hash, user_id, csrf_token, now.isoformat(), expires.isoformat()),
                )
            return raw_token, csrf_token
        finally:
            conn.close()

    def validate_session(self, raw_token: str) -> Optional[Dict[str, Any]]:
        token_h = hash_token(raw_token)
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT s.id as session_id, s.csrf_token, s.expires_at,
                       u.id as user_id, u.email, u.role, u.disabled, u.plan
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0
                """,
                (token_h, now),
            )
            row = cur.fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            conn.close()

    def delete_session(self, raw_token: str) -> None:
        token_h = hash_token(raw_token)
        conn = self.get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_h,))
        finally:
            conn.close()

    def create_authenticated_session(
        self, user_id: int, mfa_step: Optional[int] = None,
        expected_credentials: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[str, str]]:
        """Atomically consume an authenticator step and issue its session."""
        raw_token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)
        conn = self.get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT disabled,password_hash,totp_secret,totp_last_step FROM users WHERE id = ?", (user_id,)).fetchone()
                if not row or row["disabled"]:
                    return None
                if expected_credentials is not None:
                    for key in ("password_hash", "totp_secret"):
                        current, expected = row[key] or "", expected_credentials.get(key) or ""
                        if not hmac.compare_digest(current, expected):
                            return None
                if row["totp_secret"]:
                    if mfa_step is None or mfa_step <= (row["totp_last_step"] or 0):
                        return None
                    conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (mfa_step, user_id))
                conn.execute(
                    "INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,expires_at) VALUES(?,?,?,?,?)",
                    (hash_token(raw_token),user_id,csrf_token,now.isoformat(),(now+timedelta(hours=SESSION_LIFETIME_HOURS)).isoformat()),
                )
            return raw_token, csrf_token
        finally:
            conn.close()

    def revoke_user_sessions(self, user_id: int) -> None:
        conn = self.get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        finally:
            conn.close()

    def log_audit(self, conn: sqlite3.Connection, action: str, actor: Optional[str], details: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO audit (action, actor, details, created_at) VALUES (?, ?, ?, ?)",
            (action, actor or "system", details, now),
        )

    def get_latest_config(self) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT config_json FROM config_versions ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                try:
                    return json.loads(row["config_json"])
                except Exception:
                    pass
            return None
        finally:
            conn.close()

    def save_config(self, config_dict: Dict[str, Any], actor: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        raw_json = json.dumps(config_dict, indent=2)
        conn = self.get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO config_versions (config_json, actor, created_at) VALUES (?, ?, ?)",
                    (raw_json, actor, now),
                )
                cid = cur.lastrowid
                self.log_audit(conn, "config_update", actor, f"Config version {cid} saved")
                return cid
        finally:
            conn.close()

    def record_metric(self, render_ms: float, latency_ms: float, client_ts: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO metrics (render_ms, feed_latency_ms, client_timestamp, created_at) VALUES (?, ?, ?, ?)",
                    (render_ms, latency_ms, client_ts, now),
                )
        finally:
            conn.close()

