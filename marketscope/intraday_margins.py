"""Verified public Zerodha MIS leverage, with explicit freshness and no orders.

The published equity calculator is a reference list, not an account-specific
margin quote. A gold-star candidate must be strictly above 3x by both the
published multiplier and the reciprocal of the published margin percentage.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

SOURCE_URL = "https://zerodha.com/margin-calculator/Equity/"
THRESHOLD = Decimal("3")
MAX_AGE_SECONDS = 86_400
MAX_SOURCE_BYTES = 8_000_000
IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).parent.parent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo and result.utcoffset() is not None else None


def _source_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _record(symbol, margin, leverage) -> dict:
    symbol = str(symbol).strip().upper()
    if not re.fullmatch(r"[A-Z0-9&._-]{1,40}", symbol):
        raise ValueError("Invalid equity symbol")
    if isinstance(margin, bool) or isinstance(leverage, bool):
        raise ValueError("Invalid margin values")
    try:
        m = Decimal(str(margin).strip().removesuffix("%").strip())
        lev = Decimal(str(leverage).strip().lower().removesuffix("x").removesuffix("×").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid margin values") from None
    if not m.is_finite() or not lev.is_finite() or not 0 < m <= 100 or not 1 <= lev <= 5:
        raise ValueError("Margin or multiplier outside the verified equity policy")
    computed = Decimal("100") / m
    # Published values have at most two effective decimal places. Allow rounding
    # and truncation, but reject a different column/product or gross mismatch.
    if abs(computed - lev) > Decimal("0.02"):
        raise ValueError("Margin percentage and multiplier disagree")
    return {"margin_percent": float(m), "leverage": float(lev),
            "eligible": bool(computed > THRESHOLD and lev > THRESHOLD),
            "exchange": "NSE"}


class _EquityTables(HTMLParser):
    """Read actual table cells; script data and commented CO columns are ignored."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.stack = []
        self.text = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "table":
            self.stack.append({"attrs": dict(attrs), "rows": [], "row": None, "cell": None})
        elif self.stack and tag == "tr":
            self.stack[-1]["row"] = []
        elif self.stack and tag in ("td", "th"):
            self.stack[-1]["cell"] = []

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in ("td", "th") and table["cell"] is not None:
            if table["row"] is not None:
                table["row"].append(" ".join(" ".join(table["cell"]).split()))
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            self.tables.append(self.stack.pop())

    def handle_data(self, text):
        if self.skip:
            return
        self.text.append(text)
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(text)


def parse_zerodha_html(html: str | bytes, checked_at: datetime | str | None = None) -> dict:
    """Parse Zerodha's published equity MIS/CO table into a verified snapshot.

    `source_updated_at` is the publisher's date; `checked_at` is when this
    particular public list was actually retrieved. They are never substituted.
    """
    checked = _timestamp(checked_at) if checked_at is not None else _now()
    if checked is None:
        raise ValueError("An explicit timezone is required for verification time")
    if isinstance(html, bytes):
        if len(html) > MAX_SOURCE_BYTES:
            raise ValueError("Margin source exceeds its size limit")
        html = html.decode("utf-8-sig")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("Invalid margin source")
    parser = _EquityTables()
    parser.feed(html)
    parser.close()
    if parser.stack:
        raise ValueError("The margin table response is incomplete")
    text = " ".join(" ".join(parser.text).split())
    match = re.search(r"Last\s+updated\s*:\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})", text, re.I)
    published = _source_date(match.group(1)) if match else None
    if published is None or published > checked.astimezone(IST).date():
        raise ValueError("The margin source has no valid publication date")
    candidates = []
    for table in parser.tables:
        header = " ".join(" ".join(row) for row in table["rows"][:2]).casefold()
        if "scrip" in header and re.search(r"mis\s*/\s*co", header) and "margin" in header and "leverage" in header:
            candidates.append(table)
    if not candidates:
        raise ValueError("The verified equity MIS table was not found")
    records = {}
    rejected = 0
    for table in candidates:
        for row in table["rows"][2:]:
            if len(row) != 3:
                if row:
                    rejected += 1
                continue
            try:
                record = _record(row[0], row[1], row[2])
            except ValueError:
                rejected += 1
                continue
            symbol = row[0].strip().upper()
            if symbol in records and records[symbol] != record:
                raise ValueError("The margin table has conflicting duplicate symbols")
            records[symbol] = record
    if not records:
        raise ValueError("The equity MIS table contains no valid records")
    return {"provider": "Zerodha", "product": "MIS", "source_url": SOURCE_URL,
            "source_updated_at": published.isoformat(), "checked_at": checked.isoformat(),
            "expires_at": (checked + timedelta(seconds=MAX_AGE_SECONDS)).isoformat(),
            "threshold": 3, "stale": False, "status": "partial" if rejected else "ready",
            "records": records, "record_count": len(records), "rejected_rows": rejected,
            "eligible_count": sum(row["eligible"] for row in records.values())}


def validate_snapshot(snapshot: dict, now: datetime | None = None) -> dict:
    """Validate a cached or bundled snapshot; recompute eligibility, never trust it."""
    now = _timestamp(now) if now is not None else _now()
    if now is None or not isinstance(snapshot, dict):
        raise ValueError("Invalid margin snapshot")
    if snapshot.get("provider") != "Zerodha" or snapshot.get("product") != "MIS" or snapshot.get("source_url") != SOURCE_URL:
        raise ValueError("Margin snapshot provenance is not the verified source")
    checked = _timestamp(snapshot.get("checked_at"))
    published = _source_date(snapshot.get("source_updated_at"))
    if checked is None or checked > now + timedelta(minutes=5) or published is None or published > checked.astimezone(IST).date():
        raise ValueError("Margin snapshot timestamps are invalid")
    raw = snapshot.get("records")
    if not isinstance(raw, dict) or not raw or len(raw) > 20_000:
        raise ValueError("Margin snapshot records are invalid")
    records = {}
    for symbol, row in raw.items():
        if not isinstance(row, dict):
            raise ValueError("Margin snapshot row is invalid")
        record = _record(symbol, row.get("margin_percent"), row.get("leverage"))
        records[str(symbol).strip().upper()] = record
    source_record_count = snapshot.get("source_record_count")
    source_eligible_count = snapshot.get("source_eligible_count")
    catalogue_count = snapshot.get("catalogue_count")
    source_record_count = source_record_count if isinstance(source_record_count, int) and 0 <= source_record_count <= 20_000 else len(records)
    source_eligible_count = source_eligible_count if isinstance(source_eligible_count, int) and 0 <= source_eligible_count <= source_record_count else sum(row["eligible"] for row in records.values())
    catalogue_count = catalogue_count if isinstance(catalogue_count, int) and 0 <= catalogue_count <= 20_000 else None
    stale = (now - checked).total_seconds() >= MAX_AGE_SECONDS
    if stale:
        for row in records.values():
            row["eligible"] = False
    result = {"provider": "Zerodha", "product": "MIS", "source_url": SOURCE_URL,
            "source_updated_at": published.isoformat(), "checked_at": checked.isoformat(),
            "expires_at": (checked + timedelta(seconds=MAX_AGE_SECONDS)).isoformat(),
            "threshold": 3, "stale": stale, "status": "stale" if stale else "partial" if snapshot.get("rejected_rows") else "ready",
            "records": records, "record_count": len(records),
            "eligible_count": sum(row["eligible"] for row in records.values()),
            "rejected_rows": int(snapshot.get("rejected_rows") or 0)}
    result.update(source_record_count=source_record_count, source_eligible_count=source_eligible_count)
    if catalogue_count is not None:
        result["catalogue_count"] = catalogue_count
    return result


class _SourceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        try:
            port_allowed = parsed.port in (None, 443)
        except ValueError:
            port_allowed = False
        if parsed.scheme != "https" or parsed.hostname != "zerodha.com" or not port_allowed or parsed.username or parsed.password:
            raise ValueError("External margin-source redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class IntradayMarginsService:
    def __init__(self, cache_path=None, seed_path=None, refresh_seconds=300, reader=None, clock=None, min_records=100):
        self.cache_path = Path(cache_path or os.environ.get("MS_INTRADAY_MARGINS_CACHE", ROOT / "work" / "runtime" / "zerodha-equity-margins.json"))
        self.seed_path = Path(seed_path or ROOT / "marketscope" / "static" / "data" / "zerodha-equity-margins.json")
        self.refresh_seconds = max(60, min(3600, int(os.environ.get("MS_INTRADAY_MARGINS_REFRESH_SECONDS", refresh_seconds))))
        self.reader = reader or self._read
        self.clock = clock or _now
        self.min_records = max(1, int(min_records))
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._snapshot = None
        self._last_error = None
        self._last_attempt = None
        self._load()

    @staticmethod
    def _read() -> bytes:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "ZienQuant/2.0 personal margin-list reference", "Accept": "text/html"})
        opener = urllib.request.build_opener(_SourceRedirect())
        with opener.open(request, timeout=12) as response:
            content = response.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError("Margin response exceeds size limit")
        return content

    def _load(self):
        candidates = []
        for path in (self.cache_path, self.seed_path):
            try:
                if path.is_file() and path.stat().st_size <= MAX_SOURCE_BYTES:
                    snapshot = validate_snapshot(json.loads(path.read_text(encoding="utf-8")), self.clock())
                    candidates.append(snapshot)
            except (ValueError, TypeError, OSError, UnicodeDecodeError):
                continue
        if candidates:
            self._snapshot = max(candidates, key=lambda item: item["checked_at"])

    def payload(self) -> dict:
        with self._lock:
            if self._snapshot is None:
                # A newer verified static seed may arrive after service creation.
                self._load()
            if self._snapshot is not None:
                result = validate_snapshot(self._snapshot, self.clock())
                if self._last_error and not result["stale"]:
                    result["status"] = "cached"
            else:
                result = {"provider": "Zerodha", "product": "MIS", "source_url": SOURCE_URL,
                          "source_updated_at": None, "checked_at": None, "expires_at": None,
                          "threshold": 3, "stale": True, "status": "unavailable", "records": {},
                          "record_count": 0, "eligible_count": 0, "rejected_rows": 0}
            result.update(refresh_seconds=self.refresh_seconds, last_attempt_at=self._last_attempt,
                          last_error=self._last_error,
                          note="Published MIS reference only; leverage can change intraday. This is not an order-time margin quote.")
            return result

    def refresh(self) -> dict:
        if not self._refresh_lock.acquire(blocking=False):
            return self.payload()
        checked = self.clock()
        with self._lock:
            self._last_attempt = checked.isoformat()
        try:
            snapshot = parse_zerodha_html(self.reader(), checked_at=checked)
            if snapshot["record_count"] < self.min_records:
                raise ValueError("The margin table appears incomplete")
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temporary.write_text(json.dumps(snapshot, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            temporary.replace(self.cache_path)
            with self._lock:
                self._snapshot = snapshot
                self._last_error = None
        except Exception as exc:
            # No credentials are used; avoid dumping HTML or arbitrary bodies.
            detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            with self._lock:
                self._last_error = f"The Zerodha margin list could not be refreshed ({detail})."
        finally:
            self._refresh_lock.release()
        return self.payload()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="zerodha-margin-list", daemon=True)
            self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.refresh_seconds)

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)


_service = None
_service_lock = threading.Lock()


def get_intraday_margins() -> IntradayMarginsService:
    global _service
    with _service_lock:
        if _service is None:
            _service = IntradayMarginsService()
        return _service
