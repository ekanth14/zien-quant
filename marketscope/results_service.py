"""Published NSE earnings filings, comparable financials and durable research inputs.

Public exchange endpoints are best-effort: blocked/changed schemas are errors, never
an empty successful feed. No trading, generated figures or credentials are involved.
The worker runs only while the application server runs; it is not an OS scheduler.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager, closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
RESULTS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
INTEGRATED_PAGE = "https://www.nseindia.com/companies-listing/corporate-integrated-filing"
COMPARISON_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results-comparision"
ANNOUNCEMENTS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
logger = logging.getLogger("marketscope.results")


def _now() -> datetime:
    return datetime.now(IST)


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, (str, datetime, date)):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), IST)
    text = str(value).strip()
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return result.replace(tzinfo=IST) if result.tzinfo is None else result.astimezone(IST)
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "NA", "N/A", "null"):
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        n = float(text)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _first(row: dict, *names: str):
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return None


def _basis(value: Any) -> str:
    text = str(value or "").lower().replace("-", " ")
    if any(s in text for s in ("non consolidated", "nonconsolidated", "standalone")) or text in ("n", "no", "false", "0"):
        return "standalone"
    if "consolidated" in text or text in ("c", "y", "yes", "true", "1", "cons"):
        return "consolidated"
    return "unknown"


def _company_key(name: Any) -> str:
    text = str(name or "").casefold().replace("&amp;", "&").strip()
    text = re.sub(r"\bltd\.?$", "limited", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _official_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or value.strip().upper() in ("", "-", "NA", "N/A", "NULL"):
        return None
    url = urllib.parse.urljoin("https://www.nseindia.com/", value.strip())
    parsed = urllib.parse.urlsplit(url)
    try:
        allowed = parsed.scheme == "https" and parsed.port in (None, 443)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.path.rstrip("/").split("/")[-1] == "-":
        return None
    if allowed and (host == "nseindia.com" or host.endswith(".nseindia.com")) and not parsed.username and not parsed.password:
        return url
    return None


def normalize_filing(row: dict, fetched_at: datetime | None = None) -> dict | None:
    """Normalize integrated and older NSE metadata without guessing missing dates."""
    if not isinstance(row, dict):
        return None
    # NSE's downloadable table uses human-readable CSV headers rather than its
    # JSON field names. Map only those explicit metadata columns; imported
    # financial figures are never accepted as trusted reported facts here.
    aliases = {"symbol": "symbol", "companyname": "companyName",
               "broadcastdatetime": "broadcastDate", "periodended": "periodEnded",
               "forquarterending": "periodEnded", "quarterenddate": "periodEnded",
               "consolidatednonconsolidated": "basis", "consolidatedstandalone": "basis",
               "reviseddatetime": "revised_at", "auditedunaudited": "auditStatus",
               "exchangedisseminationtime": "exchange_published_at",
               "typeofsubmission": "typeOfSubmission", "xbrl": "xbrl"}
    row = dict(row)
    for key, value in list(row.items()):
        mapped = aliases.get(re.sub(r"[^a-z0-9]", "", str(key).lower()))
        if mapped and row.get(mapped) in (None, ""):
            row[mapped] = value
    symbol = str(_first(row, "symbol", "sm_symbol", "Symbol") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9&.\-]{1,30}", symbol):
        return None
    stamp_fields = ("creation_Date", "broadcast_Date", "broadcastDate", "broadcast_date", "broadCastDate", "an_dt", "date", "filingDateTs", "filingDate")
    stamps = [(row.get(key), parse_datetime(row.get(key))) for key in stamp_fields]
    valid_stamps = [(raw, stamp) for raw, stamp in stamps if stamp is not None]
    timed_stamps = [(raw, stamp) for raw, stamp in valid_stamps if ":" in str(raw)]
    selected_stamp = next(iter(timed_stamps or valid_stamps), (None, None))
    announced = selected_stamp[1]
    revised = parse_datetime(_first(row, "revision_Date", "revisedDate", "revised_date", "revised_at"))
    revision_row = "revision" in str(_first(row, "typeOfSubmission", "type_Sub") or "").casefold()
    if announced is None and revised is None and not revision_row:
        exchange = parse_datetime(row.get("exchange_published_at"))
        if exchange:
            selected_stamp = (row["exchange_published_at"], exchange)
            announced = exchange
    original_unknown = announced is None
    if announced is None:
        # A revision is itself a published filing. Retain it in the ledger,
        # without claiming its revision time was the original announcement.
        announced = revised or parse_datetime(row.get("exchange_published_at"))
    period = parse_datetime(_first(row, "qe_Date", "to_date", "toDate", "periodEnded", "periodTo", "to", "re_to_dt", "period_end"))
    if announced is None or period is None or period.date() > announced.date():
        return None
    basis = _basis(_first(row, "consolidated", "consolidatedStatus", "consolidated_status", "consol", "basis"))
    attachment = None
    for key in ("xbrl", "xbrl_link", "xbrlLink", "xbrlFile", "xbrl_file", "xbrl_File_Path", "xbrlFilePath", "xbrl_Path", "xbrlFileLink", "ixbrlFile", "file_Name", "fileName", "attachment", "attchmntFile", "ixbrl", "iXBRL"):
        candidate = _official_url(row.get(key))
        if candidate:
            attachment = candidate
            break
    fetched = fetched_at or _now()
    identifier = hashlib.sha256(f"{symbol}|{period.date()}|{basis}".encode()).hexdigest()[:24]
    return {
        "id": identifier, "symbol": symbol,
        "company": str(_first(row, "cmName", "companyName", "company_name", "company", "companyname", "sm_name", "name") or symbol)[:300],
        "announcement_time_known": not original_unknown and ":" in str(selected_stamp[0]),
        "original_announcement_unknown": original_unknown,
        "announced_at": announced.isoformat(), "period_end": period.date().isoformat(),
        "basis": basis, "source": "NSE published filing", "source_url": attachment or RESULTS_PAGE,
        "document_url": attachment, "source_filing_id": str(_first(row, "seq_Id", "seqId", "seqNumber", "filingId", "id") or ""),
        "revised_at": revised.isoformat() if revised else None,
        "known_at": (revised or announced).isoformat(), "first_seen_at": fetched.isoformat(),
        "source_published_at": (revised or announced).isoformat(),
        "revision": 1, "financials": {}, "previous": {}, "growth": {"notes": []},
        "notes": ["Original announcement time was not supplied; this date is the publication of a revision."] if original_unknown else [],
        "financial_status": "awaiting_document", "financials_available_at": None,
        "news": [], "audit_status": str(_first(row, "audited", "auditStatus") or "Unknown"),
        "submission_type": str(_first(row, "type_Sub", "typeOfSubmission", "relatingTo") or "Quarterly financial filing")[:300],
    }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def parse_financial_document(content: bytes, event: dict) -> list[dict]:
    """Read exact quarterly XBRL/iXBRL contexts, INR units, scales and scope.

    Unknown taxonomies/HTML remain unavailable. No annual-to-quarter subtraction,
    table-position inference, unit guessing, or consolidation mixing is performed.
    """
    if len(content) > 12_000_000 or re.search(br"<!\s*(DOCTYPE|ENTITY)", content, re.I):
        raise ValueError("Unsafe or oversized financial document")
    try:
        tree = ET.fromstring(content)
    except ET.ParseError:
        return []
    contexts, units = {}, {}
    for element in tree.iter():
        local = _local(element.tag)
        if local == "context":
            start = end = None
            dimensions = []
            for child in element.iter():
                child_local = _local(child.tag)
                if child_local == "startdate":
                    start = parse_datetime(child.text)
                elif child_local == "enddate":
                    end = parse_datetime(child.text)
                elif child_local in ("explicitmember", "typedmember"):
                    dimensions.append((str(child.attrib.get("dimension", "")) + " " + "".join(child.itertext())).lower())
            if start and end and 70 <= (end.date() - start.date()).days <= 110:
                scope = "standalone" if any("standalone" in s for s in dimensions) else "consolidated" if any("consolidated" in s for s in dimensions) else event.get("basis", "unknown")
                # Segment/subsidiary contexts cannot stand for whole-company results.
                invalid = any(not any(word in dim for word in ("standalone", "consolidated", "reportingentity")) for dim in dimensions)
                if not invalid:
                    contexts[element.attrib.get("id")] = {"start": start.date().isoformat(), "period_end": end.date().isoformat(), "basis": scope}
        elif local == "unit":
            measures = [_local((c.text or "").strip()) for c in element.iter() if _local(c.tag) == "measure"]
            units[element.attrib.get("id")] = measures
    tags = {
        "revenuefromoperations": ("revenue", 0), "revenuefromoperationsnet": ("revenue", 1),
        "revenue": ("revenue", 2), "profitlossforperiod": ("net_profit", 0),
        "profitloss": ("net_profit", 1), "profitlossforperiodfromcontinuingoperations": ("net_profit", 2),
        "basicearningslosspershare": ("eps", 0), "basicearningslosspersharefromcontinuingoperations": ("eps", 1),
        "earningspersharebasic": ("eps", 2),
        "profitlossfortheperiod": ("net_profit", 1),
        "profitlossfromordinaryactivitiesaftertax": ("net_profit", 3),
        "basicearningspershareafterextraordinaryitems": ("eps", 2),
    }
    if "banking" in str(event.get("document_url") or event.get("source_url") or "").lower():
        tags["income"] = ("revenue", 3)
    rows = {}
    for element in tree.iter():
        attrs = {k.lower(): v for k, v in element.attrib.items()}
        context = contexts.get(attrs.get("contextref"))
        if not context or context["basis"] not in ("standalone", "consolidated"):
            continue
        name = _local(attrs.get("name", element.tag))
        if name not in tags:
            continue
        metric, priority = tags[name]
        measures = units.get(attrs.get("unitref"), [])
        if "inr" not in measures or (metric != "eps" and len(measures) != 1):
            continue
        value = _number("".join(element.itertext()))
        if value is None:
            continue
        try:
            scale = int(attrs.get("scale", "0"))
            if not -12 <= scale <= 12:
                continue
            value *= 10 ** scale
        except ValueError:
            continue
        if attrs.get("sign") == "-":
            value = -abs(value)
        if metric != "eps":
            value /= 100_000.0
        key = (context["period_end"], context["basis"])
        result = rows.setdefault(key, {**context, "unit": "INR lakh", "revenue_metric": "revenue_from_operations", "_priority": {}})
        if priority < result["_priority"].get(metric, 999):
            result[metric] = value
            if metric == "revenue":
                result["revenue_metric"] = "total_income" if name == "income" else "revenue_from_operations"
            if metric == "net_profit":
                result["profit_metric"] = "profit_continuing_operations" if "continuingoperations" in name else "profit_for_period"
            result["_priority"][metric] = priority
    for result in rows.values():
        result.pop("_priority", None)
    return list(rows.values())


def comparable_growth(current: dict, previous: dict | None) -> dict:
    out = {"revenue_qoq": None, "profit_qoq": None, "eps_qoq": None, "notes": []}
    if not previous:
        out["notes"].append("A comparable previous quarter is unavailable.")
        return out
    if current.get("basis") != previous.get("basis") or current.get("unit") != previous.get("unit") or current.get("revenue_metric") != previous.get("revenue_metric") or current.get("profit_metric") != previous.get("profit_metric"):
        out["notes"].append("Different reporting scopes, units or revenue definitions are not compared.")
        return out
    for metric, label in (("revenue", "revenue"), ("net_profit", "profit"), ("eps", "eps")):
        a, b = _number(current.get(metric)), _number(previous.get(metric))
        if a is None or b is None:
            continue
        if b > 0:
            out[f"{label}_qoq"] = (a - b) / b * 100.0
        elif b == 0:
            out["notes"].append(f"{label.title()}: zero base; percentage growth is undefined.")
        elif a > 0:
            out["notes"].append(f"{label.title()}: loss-to-profit turnaround; percentage growth is not comparable.")
        else:
            out["notes"].append(f"{label.title()}: prior base is negative; percentage growth is not comparable.")
    return out


def apply_financial_rows(event: dict, rows: list[dict], available_at: str, source_url: str) -> dict:
    matching = [r for r in rows if r.get("basis") == event["basis"] and r.get("period_end") == event["period_end"]]
    if not matching:
        return event
    current = matching[0]
    period = date.fromisoformat(event["period_end"])
    prior = sorted((r for r in rows if r.get("basis") == event["basis"] and r.get("period_end", "") < event["period_end"]), key=lambda r: r["period_end"], reverse=True)
    previous = next((r for r in prior if 70 <= (period - date.fromisoformat(r["period_end"])).days <= 110), None)
    year_ago = next((r for r in prior if 350 <= (period - date.fromisoformat(r["period_end"])).days <= 380), None)
    growth = comparable_growth(current, previous)
    yearly = comparable_growth(current, year_ago)
    growth.update({"revenue_yoy": yearly["revenue_qoq"], "profit_yoy": yearly["profit_qoq"], "eps_yoy": yearly["eps_qoq"]})
    if year_ago is None:
        growth["notes"].append("A comparable year-ago quarter is unavailable.")
    result = dict(event)
    result.update(financials=current, previous=previous or {}, year_ago=year_ago or {}, growth=growth,
                  financial_status="available", financials_available_at=available_at, financial_known_at=available_at,
                  financial_source_url=source_url, reported_comparatives=rows)
    return result


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        allowed = _official_url(newurl) if old.hostname and old.hostname.endswith("nseindia.com") else new.scheme == "https" and new.hostname == old.hostname
        if not allowed:
            raise ValueError("Refused external data redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NSEPublicClient:
    """Normal cookie session, bounded reads and no access-control workarounds."""
    def __init__(self, timeout=12):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SafeRedirect())
        self.session_ready = False
        self.last_corporate_actions = {}

    def _read(self, url: str, referer=INTEGRATED_PAGE, limit=8_000_000) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "ZienQuant/2.0 personal financial research", "Accept": "application/json,text/xml,application/xhtml+xml,text/html", "Referer": referer})
        with self.opener.open(req, timeout=self.timeout) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Data response exceeds size limit")
            return raw

    def _json(self, path: str, params: dict) -> Any:
        if not self.session_ready:
            self._read("https://www.nseindia.com/", limit=2_000_000)
            self.session_ready = True
        url = "https://www.nseindia.com/api/" + path + "?" + urllib.parse.urlencode(params)
        try:
            return json.loads(self._read(url))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("NSE returned a non-JSON response; source unavailable") from exc

    @staticmethod
    def _records(payload: Any) -> tuple[list, int]:
        if isinstance(payload, list):
            return payload, len(payload)
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"], int(payload.get("totalCount", len(payload["data"])))
        raise ValueError("Unsupported NSE filing response schema")

    def filings(self, start: date, end: date) -> list[dict]:
        if start < date(2025, 1, 1) <= end:
            return self.filings(start, date(2024, 12, 31)) + self.filings(date(2025, 1, 1), end)
        if end < date(2025, 1, 1):
            legacy, count = self._records(self._json("corporates-financial-results", {"index": "equities", "period": "Quarterly", "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")}))
            if count > len(legacy) or legacy and not any(normalize_filing(row) for row in legacy):
                raise ValueError("Unsupported or incomplete legacy NSE filing schema")
            return legacy
        params = {"index": "equities", "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y"), "period_ended": "all", "type": "Integrated Filing- Financials", "page": 0, "size": 500}
        records, count = self._records(self._json("integrated-filing-results", params))
        if count > 5_000:
            raise ValueError("NSE filing range exceeds safe page limit; narrow the date range")
        if count > len(records):
            params["size"] = min(count + 20, 5_000)
            records, count = self._records(self._json("integrated-filing-results", params))
        unique = {json.dumps(row, sort_keys=True): row for row in records if isinstance(row, dict)}
        for page in range(1, 21):
            if len(unique) >= count:
                break
            params["page"] = page
            batch, new_count = self._records(self._json("integrated-filing-results", params))
            count = max(count, new_count)
            before = len(unique)
            unique.update({json.dumps(row, sort_keys=True): row for row in batch if isinstance(row, dict)})
            if len(unique) == before:
                break
        records = list(unique.values())
        if count > len(records):
            raise ValueError("NSE returned an incomplete filing page; no partial page was imported")
        if records and not any(normalize_filing(row) for row in records):
            raise ValueError("NSE filing fields changed; no supported dates/symbols in response")
        return records

    def recent_filings(self, start: date, end: date) -> list[dict]:
        """Use the explicitly published public RSS when the query feed fails.

        This method is intentionally separate from `filings`: a latest-items
        RSS response must never advance the full historical backfill cursor.
        """
        try:
            rows = self.filings(start, end)
            self.last_recent_coverage = {"source": "NSE filing query", "recent_only": False, "records": len(rows), "errors": []}
            return rows
        except Exception as exc:
            primary_error = f"NSE filing query unavailable ({type(exc).__name__})."
        try:
            from .results_rss import RSSResultsFeed
        except ImportError:
            from results_rss import RSSResultsFeed
        if not hasattr(self, "_rss_feed"):
            self._rss_feed = RSSResultsFeed(lambda url: self._read(url, referer="https://www.nseindia.com/static/rss-feed", limit=12_000_000))
        rows = self._rss_feed.filings(start, end)
        self.last_recent_coverage = {**self._rss_feed.last_coverage, "query_error": primary_error}
        return rows

    def document(self, url: str) -> bytes:
        validated = _official_url(url)
        if not validated:
            raise ValueError("Unsupported financial document host")
        return self._read(validated, limit=12_000_000)

    def comparison(self, symbol: str) -> list[dict]:
        payload = self._json("results-comparision", {"symbol": symbol})
        if not isinstance(payload, dict) or not isinstance(payload.get("resCmpData"), list):
            raise ValueError("Unsupported NSE comparison schema")
        rows = []
        for raw in payload["resCmpData"]:
            period = parse_datetime(raw.get("re_to_dt"))
            if not period:
                continue
            rows.append({"period_end": period.date().isoformat(), "basis": "standalone", "unit": "INR lakh", "revenue_metric": "total_income", "profit_metric": "reported_net_profit", "revenue": _number(raw.get("re_total_inc")), "net_profit": _number(_first(raw, "re_net_profit", "re_net_profit_after_tax")), "eps": _number(_first(raw, "re_eps", "re_eps_basic", "re_basic_eps"))})
        return rows

    def announcements(self, start: date, end: date) -> list[dict]:
        payload = self._json("corporate-announcements", {"index": "equities", "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")})
        records, count = self._records(payload)
        if count > len(records):
            raise ValueError("Incomplete NSE announcements response")
        return records

    def candles(self, symbol: str) -> list[dict]:
        rows = []
        fetched = int(_now().timestamp())
        actions = {}
        for interval, range_, timeframe in (("5m", "60d", 5), ("1d", "10y", 1440)):
            url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol + ".NS", safe="") + "?" + urllib.parse.urlencode({"interval": interval, "range": range_, "events": "splits,div", "includeAdjustedClose": "true"})
            payload = json.loads(self._read(url, referer="https://finance.yahoo.com/", limit=8_000_000))
            chart = payload.get("chart", {})
            data = chart.get("result")
            if chart.get("error") or not isinstance(data, list) or not data:
                raise ValueError("Yahoo candle source unavailable")
            item = data[0]
            for category in ("splits", "dividends"):
                reported = item.get("events", {}).get(category, {})
                if isinstance(reported, dict):
                    for action in reported.values():
                        if isinstance(action, dict) and isinstance(action.get("date"), (int, float)):
                            day = datetime.fromtimestamp(action["date"], IST).date().isoformat()
                            actions.setdefault(day, []).append(category)
            if item.get("meta", {}).get("currency") not in (None, "INR"):
                raise ValueError("Unexpected price currency")
            timestamps = item.get("timestamp", [])
            quotes = item.get("indicators", {}).get("quote", [])
            if not isinstance(timestamps, list) or not quotes:
                continue
            quote = quotes[0]
            for i, ts in enumerate(timestamps):
                if not isinstance(ts, (int, float)) or ts + timeframe * 60 > fetched:
                    continue
                values = {short: _number(quote.get(long, [])[i]) if i < len(quote.get(long, [])) else None for short, long in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"))}
                if any(values[k] is None for k in ("o", "h", "l", "c", "v")) or min(values[k] for k in ("o", "h", "l", "c")) <= 0 or values["v"] < 0 or values["h"] < max(values["o"], values["c"], values["l"]) or values["l"] > min(values["o"], values["c"]):
                    continue
                rows.append({"t": int(ts), **values, "timeframe": timeframe, "closed": True, "source": "Yahoo chart OHLC (adjustment basis unverified)"})
        self.last_corporate_actions[symbol] = actions
        return rows


class ResultsService:
    def __init__(self, db_path=None, candle_db_path=None, client=None, poll_seconds=300, symbol_provider=None, company_provider=None):
        root = Path(__file__).parent.parent / "work" / "runtime"
        self.db_path = str(db_path or os.environ.get("MS_RESULTS_DB_PATH", root / "results.db"))
        self.candle_db_path = str(candle_db_path or os.environ.get("MS_CANDLES_DB_PATH", root / "candles.db"))
        self.client = client or NSEPublicClient()
        self.symbol_provider = symbol_provider or self._catalogue_symbols
        self.company_provider = company_provider or (self._catalogue_companies if symbol_provider is None else lambda: [])
        self.start_date = date.fromisoformat(os.environ.get("MS_RESULTS_START_DATE", "2023-05-06")).isoformat()
        self.poll_seconds = max(60, int(os.environ.get("MS_RESULTS_POLL_SECONDS", poll_seconds)))
        self.lookback_days = min(3650, max(30, int(os.environ.get("MS_RESULTS_BACKFILL_DAYS", os.environ.get("MS_RESULTS_LOOKBACK_DAYS", "1825")))))
        self.symbol_batch = min(20, max(1, int(os.environ.get("MS_RESULTS_SYMBOL_BATCH", "6"))))
        self.backfill_pause_seconds = min(30.0, max(0.25, float(os.environ.get("MS_RESULTS_BACKFILL_PAUSE_SECONDS", "1"))))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._thread = None
        self._ingest_symbols = None
        self._init_db()

    @staticmethod
    def _catalogue_symbols():
        try:
            from .stock_database import DB_PATH, StockDatabaseManager
        except ImportError:
            from stock_database import DB_PATH, StockDatabaseManager
        # Do not use the singleton getter: it auto-populates an empty catalogue.
        # This feature must fail closed until the website's catalogue exists.
        if not Path(DB_PATH).exists():
            return set()
        return StockDatabaseManager(Path(DB_PATH)).get_all_symbols()

    def _universe(self):
        try:
            return {str(s).strip().upper() for s in self.symbol_provider() if re.fullmatch(r"[A-Z0-9&.\-]{1,30}", str(s).strip().upper())}
        except Exception:
            return set()

    def _in_scope(self, event, allowed=None):
        return (event.get("symbol") in (allowed if allowed is not None else self._universe())
                and str(event.get("announced_at", ""))[:10] >= self.start_date
                and str(event.get("period_end", "")) >= self.start_date)

    @staticmethod
    def _catalogue_companies():
        try:
            from .stock_database import DB_PATH, StockDatabaseManager
        except ImportError:
            from stock_database import DB_PATH, StockDatabaseManager
        if not Path(DB_PATH).exists():
            return []
        return StockDatabaseManager(Path(DB_PATH)).get_company_symbols()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
            CREATE TABLE IF NOT EXISTS result_events(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,announced_at TEXT NOT NULL,period_end TEXT NOT NULL,basis TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_results_date ON result_events(announced_at);
            CREATE INDEX IF NOT EXISTS idx_results_symbol ON result_events(symbol,period_end);
            CREATE TABLE IF NOT EXISTS result_versions(event_id TEXT NOT NULL,revision INTEGER NOT NULL,known_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(event_id,revision));
            CREATE TABLE IF NOT EXISTS results_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS results_symbol_jobs(symbol TEXT PRIMARY KEY,last_financial TEXT,last_candles TEXT,last_error TEXT,attempts INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS result_news(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,published_at TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS result_scope_archive(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,announced_at TEXT NOT NULL,period_end TEXT NOT NULL,basis TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL,archived_at TEXT NOT NULL,reason TEXT NOT NULL);
            """)

    def _meta(self, key, default=None):
        with self._connection() as con:
            row = con.execute("SELECT value FROM results_meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def _set_meta(self, key, value):
        with self._connection() as con:
            con.execute("INSERT INTO results_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="results-poller", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.refresh()
            except Exception:
                logger.exception("Results refresh failed")
            self._wake.wait(self.poll_seconds)

    def refresh_async(self):
        if self._thread and self._thread.is_alive():
            self._wake.set()
        else:
            self.start()
        return self.status()

    def _upsert_event(self, event):
        allowed = self._ingest_symbols if self._ingest_symbols is not None else self._universe()
        if not self._in_scope(event, allowed):
            return
        now = _now().isoformat()
        with self._connection() as con:
            existing = con.execute("SELECT payload FROM result_events WHERE id=?", (event["id"],)).fetchone()
            if existing:
                old = json.loads(existing[0])
                old_stamp = parse_datetime(old.get("source_published_at") or old.get("revised_at") or old.get("announced_at"))
                incoming_stamp = parse_datetime(event.get("source_published_at") or event.get("revised_at") or event.get("announced_at"))
                if old_stamp and incoming_stamp and incoming_stamp < old_stamp:
                    # Older exports may improve the original publication date,
                    # but must not replace the newest document or its figures.
                    if not event.get("original_announcement_unknown") and event["announced_at"] < old["announced_at"]:
                        old["announced_at"] = event["announced_at"]
                        old["announcement_time_known"] = event.get("announcement_time_known", False)
                        old["original_announcement_unknown"] = False
                        con.execute("UPDATE result_events SET announced_at=?,payload=? WHERE id=?", (old["announced_at"], json.dumps(old, allow_nan=False), event["id"]))
                    return
                new_source = event.get("source_filing_id")
                old_source = old.get("source_filing_id")
                source_changed = bool(new_source and old_source and new_source != old_source)
                document_changed = source_changed or event.get("document_url") != old.get("document_url") or event.get("revised_at") != old.get("revised_at")
                preserve_financials = not event.get("financials") and bool(old.get("financials"))
                if preserve_financials:
                    for key in ("financials", "previous", "year_ago", "growth", "financial_status", "financials_available_at", "financial_known_at", "financial_source_url", "reported_comparatives", "comparison_available_at", "previous_source_url", "year_ago_source_url"):
                        event[key] = old.get(key)
                    if document_changed:
                        event["financial_status"] = "revision_pending"
                event["first_seen_at"] = old.get("first_seen_at", now)
                event["announced_at"] = min(old["announced_at"], event["announced_at"])
                if not old.get("original_announcement_unknown"):
                    event["announcement_time_known"] = old.get("announcement_time_known", False)
                    event["original_announcement_unknown"] = False
                changing = ("document_url", "revised_at", "financials", "source_filing_id", "basis", "previous", "year_ago", "growth", "comparison_available_at")
                changed = any(event.get(k) != old.get(k) for k in changing)
                event["revision"] = int(old.get("revision", 1)) + int(changed)
                if document_changed and not event.get("revised_at"):
                    event["known_at"] = incoming_stamp.isoformat() if incoming_stamp and old_stamp and incoming_stamp > old_stamp else now
                elif not changed:
                    event["known_at"] = old.get("known_at", event.get("known_at"))
                if changed:
                    con.execute("UPDATE results_symbol_jobs SET last_financial=NULL WHERE symbol=?", (event["symbol"],))
            con.execute("INSERT INTO result_events VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at,announced_at=excluded.announced_at", (event["id"], event["symbol"], event["announced_at"], event["period_end"], event["basis"], json.dumps(event, allow_nan=False), now))
            con.execute("INSERT OR IGNORE INTO result_versions VALUES(?,?,?,?)", (event["id"], event["revision"], event.get("known_at") or now, json.dumps(event, allow_nan=False)))
            con.execute("INSERT OR IGNORE INTO results_symbol_jobs(symbol) VALUES(?)", (event["symbol"],))

    def _news(self, records):
        allowed = self._ingest_symbols if self._ingest_symbols is not None else self._universe()
        count = 0
        with self._connection() as con:
            for row in records:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                if symbol not in allowed:
                    continue
                stamp = parse_datetime(_first(row, "an_dt", "broadcastDate", "date", "creation_Date"))
                title = str(_first(row, "desc", "subject", "attchmntText", "description") or "").strip()
                if not re.fullmatch(r"[A-Z0-9&.\-]{1,30}", symbol) or not stamp or not title:
                    continue
                url = _official_url(_first(row, "attchmntFile", "attachment", "url")) or ANNOUNCEMENTS_PAGE
                text = title.lower()
                category = "risk" if any(k in text for k in ("default", "resign", "penalty", "litigation", "downgrade", "fraud", "loss")) else "potential_catalyst" if any(k in text for k in ("dividend", "order", "contract", "buyback", "upgrade", "acquisition")) else "neutral"
                item = {"title": title[:800], "url": url, "published_at": stamp.isoformat(), "category": category, "source": "NSE company disclosure", "note": "Subject classification only; read the filing for context."}
                identifier = hashlib.sha256(f"{symbol}|{stamp.isoformat()}|{title}|{url}".encode()).hexdigest()
                con.execute("INSERT OR REPLACE INTO result_news VALUES(?,?,?,?)", (identifier, symbol, stamp.isoformat(), json.dumps(item)))
                count += 1
        return count

    def _import_filing_rows(self, rows, *, source_url=RESULTS_PAGE, start=None, end=None):
        """Store validated filing metadata independently of numerical enrichment."""
        if not isinstance(rows, list) or len(rows) > 100_000:
            raise ValueError("Expected a list containing at most 100,000 filing rows")
        source = _official_url(source_url)
        if not source:
            raise ValueError("A valid official NSE source URL is required")
        allowed = self._ingest_symbols if self._ingest_symbols is not None else self._universe()
        result = {"received": len(rows), "imported": 0, "outside_universe": 0,
                  "rejected": 0, "outside_range": 0, "unresolved_company": 0, "source_url": source}
        name_symbols = {}
        for symbol, company in self.company_provider():
            if symbol in allowed:
                name_symbols.setdefault(_company_key(company), set()).add(symbol)
        normalized = []
        unique_ids = set()
        seen_rows = set()
        now = _now()
        for raw in rows:
            if isinstance(raw, dict):
                columns = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in raw.items()}
                if not columns.get("symbol"):
                    matches = name_symbols.get(_company_key(columns.get("companyname")), set())
                    if len(matches) != 1:
                        result["unresolved_company"] += 1
                        continue
                    raw = {**raw, "symbol": next(iter(matches))}
            event = normalize_filing(raw, now)
            if event is None or date.fromisoformat(event["announced_at"][:10]) > now.date():
                result["rejected"] += 1
                continue
            if event["symbol"] not in allowed:
                result["outside_universe"] += 1
                continue
            if not self._in_scope(event, allowed):
                result["outside_range"] += 1
                continue
            day = date.fromisoformat(event["announced_at"][:10])
            if start and day < start or end and day > end:
                result["outside_range"] += 1
                continue
            event["discovery_source_url"] = source
            event["metadata_imported_at"] = now.isoformat()
            key = (event["id"], event["known_at"], event["document_url"], event["source_filing_id"])
            if key not in seen_rows:
                normalized.append(event)
                unique_ids.add(event["id"])
                seen_rows.add(key)
        # Preserve source versions chronologically, regardless of export order.
        normalized.sort(key=lambda event: (event["known_at"], event["source_filing_id"]))
        for event in normalized:
            self._upsert_event(event)
        result["imported"] = len(unique_ids)
        result["source_versions"] = len(normalized)
        return result

    def import_filings(self, rows, source_url=RESULTS_PAGE):
        """Import a sourced NSE JSON/CSV export, preserving the catalogue boundary.

        Rows are raw filing metadata. Unknown dates are rejected and arbitrary
        `financials` or `growth` values in an import are never trusted.
        """
        if not self._refresh_lock.acquire(blocking=False):
            return {"busy": True, "imported": 0, "status": self.status()}
        try:
            self._ingest_symbols = self._universe()
            if not self._ingest_symbols:
                return {"imported": 0, "status": self.status()}
            result = self._import_filing_rows(rows, source_url=source_url)
            stamp = _now().isoformat()
            status = self._meta("status", {})
            status.setdefault("state", "not_started")
            status.update(last_import_at=stamp, last_import=result,
                          state="partial" if result["imported"] else status.get("state", "not_started"))
            self._set_meta("status", status)
            return {**result, "status": self.status()}
        finally:
            self._ingest_symbols = None
            self._refresh_lock.release()

    def _history_batch(self, status, now, *, earliest=None, end_limit=None, cursor_key="backfill_cursor"):
        """Collect one <=30-day batch; a failed source never advances its cursor."""
        earliest = earliest or now.date() - timedelta(days=self.lookback_days)
        earliest = max(earliest, date.fromisoformat(self.start_date))
        end_limit = end_limit or now.date() - timedelta(days=8)
        cursor = parse_datetime(self._meta(cursor_key))
        end = min(cursor.date(), end_limit) if cursor else end_limit
        if end < earliest:
            return {"success": True, "complete": True, "imported": 0, "received": 0}
        start = max(earliest, end - timedelta(days=29))
        status["history_batch_from"] = start.isoformat()
        status["history_batch_to"] = end.isoformat()
        try:
            rows = self.client.filings(start, end)
            result = self._import_filing_rows(rows, start=start, end=end)
            next_cursor = start - timedelta(days=1)
            self._set_meta(cursor_key, next_cursor.isoformat())
            status["last_history_success_at"] = _now().isoformat()
            status["history_records_received"] = result["received"]
            status["history_records_imported"] = result["imported"]
            return {**result, "success": True, "complete": next_cursor < earliest,
                    "cursor": next_cursor.isoformat(), "from": start.isoformat(), "to": end.isoformat()}
        except Exception as exc:
            error = "NSE history: " + str(exc)[:400]
            status.setdefault("errors", []).append(error)
            return {"success": False, "complete": False, "imported": 0, "received": 0, "error": error}

    def backfill(self, max_batches=12, date_from=None, date_to=None, enrich=False):
        """Collect a paced, resumable history job without waiting for today's feed.

        With no dates this resumes the automatic five-year cursor. Explicit date
        ranges have separate cursors so a one-off import cannot skip the normal
        job's coverage. Metadata is committed after every successful batch; price
        and XBRL enrichment is optional and never hides these announcements.
        """
        try:
            batches = int(max_batches)
        except (ValueError, TypeError) as exc:
            raise ValueError("max_batches must be between 1 and 120") from exc
        if not 1 <= batches <= 120:
            raise ValueError("max_batches must be between 1 and 120")
        now = _now()
        try:
            earliest = date.fromisoformat(str(date_from)) if date_from else now.date() - timedelta(days=self.lookback_days)
            latest = date.fromisoformat(str(date_to)) if date_to else now.date()
        except ValueError as exc:
            raise ValueError("History dates must use YYYY-MM-DD") from exc
        if earliest > latest or latest > now.date() or earliest.year < 2000:
            raise ValueError("Choose a historical date range from 2000 through today")
        if not self._refresh_lock.acquire(blocking=False):
            return {**self.status(), "backfill_busy": True}
        cursor_key = "backfill_cursor" if date_from is None and date_to is None else f"backfill_cursor:{earliest.isoformat()}:{latest.isoformat()}"
        try:
            self._ingest_symbols = self._universe()
            if not self._ingest_symbols:
                return self.status()
            status = self._meta("status", {})
            status.update(backfill_job_state="running", backfill_job_started_at=now.isoformat(),
                          backfill_job_from=earliest.isoformat(), backfill_job_to=latest.isoformat(),
                          backfill_job_completed_batches=0, backfill_job_imported=0,
                          backfill_job_received=0, backfill_job_error=None)
            # A history-only job does not erase an existing current-feed error.
            status.setdefault("errors", [])
            self._set_meta("status", status)
            for _ in range(batches):
                if self._stop.is_set():
                    status["backfill_job_state"] = "stopped"
                    break
                result = self._history_batch(status, now, earliest=earliest, end_limit=latest, cursor_key=cursor_key)
                if not result["success"]:
                    status["backfill_job_state"] = "failed"
                    status["backfill_job_error"] = result["error"]
                    break
                if "cursor" in result:
                    status["backfill_job_completed_batches"] += 1
                    status["backfill_job_cursor"] = result["cursor"]
                    status["backfill_job_received"] += result["received"]
                    status["backfill_job_imported"] += result["imported"]
                    status["state"] = "partial"
                status["backfill_job_state"] = "complete" if result["complete"] else "partial"
                self._set_meta("status", status)
                if result["complete"]:
                    break
                if self._stop.wait(self.backfill_pause_seconds):
                    status["backfill_job_state"] = "stopped"
                    break
            if enrich and not self._stop.is_set() and status["backfill_job_completed_batches"]:
                self._enrich_symbols(status, now)
                self._enrich_fyers(status, now)
            status["backfill_job_finished_at"] = _now().isoformat()
            self._set_meta("status", status)
            return self.status()
        finally:
            self._ingest_symbols = None
            self._refresh_lock.release()

    def refresh(self):
        if not self._refresh_lock.acquire(blocking=False):
            return self.status()
        now = _now()
        try:
            status = self._meta("status", {})
            self._ingest_symbols = self._universe()
            if not self._ingest_symbols:
                status.update(state="needs_catalogue", last_attempt_at=now.isoformat(), errors=["The website stock catalogue is empty or unavailable; no companies are selected."], universe_count=0)
                self._set_meta("status", status)
                return self.status()
            status.update(state="refreshing", last_attempt_at=now.isoformat(), errors=[], poll_seconds=self.poll_seconds, endpoint="/api/integrated-filing-results")
            self._set_meta("status", status)
            recent_start = now.date() - timedelta(days=7)
            successful = False
            history_successful = False
            try:
                collect_recent = getattr(self.client, "recent_filings", self.client.filings)
                raw = collect_recent(recent_start, now.date())
                imported = self._import_filing_rows(raw, start=recent_start, end=now.date())
                successful = True
                status["last_success_at"] = now.isoformat()
                status["recent_records_received"] = len(raw)
                coverage = getattr(self.client, "last_recent_coverage", None)
                if isinstance(coverage, dict):
                    status["recent_feed_coverage"] = coverage
                    if coverage.get("recent_only"):
                        status["errors"].append("Using NSE's published latest-items RSS. Historical query coverage is incomplete.")
                status["recent_records_imported"] = imported["imported"]
            except Exception as exc:
                status["errors"].append("NSE results: " + str(exc)[:400])
            if not self._stop.is_set():
                # A failure in today's feed must not block a separately usable
                # historical endpoint or leave previously announced results empty.
                batch = self._history_batch(status, now)
                history_successful = batch["success"] and "cursor" in batch
                try:
                    status["news_received"] = self._news(self.client.announcements(recent_start, now.date()))
                except Exception as exc:
                    status["errors"].append("NSE announcements: " + str(exc)[:400])
                if successful or history_successful:
                    self._enrich_symbols(status, now)
                    self._enrich_fyers(status, now)
            status["state"] = "ready" if successful and not status["errors"] else "partial" if successful or history_successful else "unavailable"
            status["last_completed_at"] = _now().isoformat()
            status["next_poll_at"] = (_now() + timedelta(seconds=self.poll_seconds)).isoformat()
            self._set_meta("status", status)
            return self.status()
        finally:
            self._ingest_symbols = None
            self._refresh_lock.release()

    def _rebuild_comparisons(self, symbol):
        """Join separately filed quarters, preserving when each fact was known.

        A filing need not repeat the prior quarter's figures. Previously that
        left QoQ missing even after both original documents were collected.
        """
        versions = self.history(symbol)
        targets = self.history(symbol, include_versions=False)
        pool = []
        for source in versions:
            if source.get("financial_status") != "available":
                continue
            available = parse_datetime(source.get("financials_available_at"))
            if not available:
                continue
            url = source.get("financial_source_url") or source.get("source_url")
            facts = [source.get("financials", {})] + list(source.get("reported_comparatives") or [])
            for fact in facts:
                if isinstance(fact, dict) and fact.get("period_end"):
                    pool.append((fact, available, url))
        updated = 0
        for event in targets:
            current = event.get("financials") or {}
            known = parse_datetime(event.get("financials_available_at"))
            if event.get("financial_status") != "available" or not current or not known:
                continue
            ending = date.fromisoformat(event["period_end"])
            def select(min_days, max_days):
                choices = []
                for fact, available, url in pool:
                    if any(fact.get(k) != current.get(k) for k in ("basis", "unit", "revenue_metric", "profit_metric")):
                        continue
                    try:
                        gap = (ending - date.fromisoformat(fact["period_end"])).days
                    except (ValueError, TypeError):
                        continue
                    if min_days <= gap <= max_days:
                        choices.append((fact, available, url))
                past = [item for item in choices if item[1] <= known]
                if past:
                    return max(past, key=lambda item: item[1])
                return min(choices, key=lambda item: item[1]) if choices else None
            previous, yearly = select(70, 110), select(350, 380)
            growth = comparable_growth(current, previous[0] if previous else None)
            year_growth = comparable_growth(current, yearly[0] if yearly else None)
            growth.update(revenue_yoy=year_growth["revenue_qoq"], profit_yoy=year_growth["profit_qoq"], eps_yoy=year_growth["eps_qoq"])
            if not yearly:
                growth["notes"].append("A comparable year-ago quarter is unavailable.")
            dependencies = [known] + [item[1] for item in (previous, yearly) if item]
            comparison_known = max(dependencies)
            if comparison_known > known:
                growth["notes"].append("Some comparison figures became available after this filing; they are excluded from point-in-time predictions.")
            enriched = {**event, "previous": previous[0] if previous else {}, "year_ago": yearly[0] if yearly else {}, "growth": growth,
                        "comparison_available_at": comparison_known.isoformat(), "previous_source_url": previous[2] if previous else None,
                        "year_ago_source_url": yearly[2] if yearly else None}
            if any(enriched.get(k) != event.get(k) for k in ("previous", "year_ago", "growth", "comparison_available_at")):
                self._upsert_event(enriched)
                updated += 1
        return updated

    def _enrich_symbols(self, status, now):
        allowed = self._universe()
        with self._connection() as con:
            all_jobs = con.execute("SELECT j.* FROM results_symbol_jobs j LEFT JOIN result_events e ON e.symbol=j.symbol GROUP BY j.symbol ORDER BY COALESCE(j.last_financial,''),MAX(e.announced_at) DESC").fetchall()
        jobs = [job for job in all_jobs if job["symbol"] in allowed][:self.symbol_batch]
        for job in jobs:
            if self._stop.is_set():
                break
            symbol = job["symbol"]
            errors = []
            financial_ok = False
            document_budget = 3
            for event in self.history(symbol, include_versions=False):
                if document_budget and event.get("document_url") and (not event.get("financials") or event.get("financial_status") != "available" or event.get("revised_at") and (event.get("financials_available_at") or "") < event["revised_at"]):
                    document_budget -= 1
                    try:
                        rows = parse_financial_document(self.client.document(event["document_url"]), event)
                        enriched = apply_financial_rows(event, rows, event.get("known_at") or event["announced_at"], event["document_url"])
                        if enriched.get("financials"):
                            self._upsert_event(enriched)
                            financial_ok = True
                    except Exception as exc:
                        errors.append("document: " + str(exc)[:160])
            # Last-five comparison is explicitly standalone/current-vintage total income.
            standalone = [e for e in self.history(symbol, include_versions=False) if e["basis"] == "standalone" and not e.get("financials")]
            if standalone:
                try:
                    rows = self.client.comparison(symbol)
                    for event in standalone:
                        enriched = apply_financial_rows(event, rows, now.isoformat(), COMPARISON_PAGE)
                        if enriched.get("financials"):
                            enriched["growth"]["notes"].append("Current comparison vintage, not verified original historical figures; revenue column is total income.")
                            self._upsert_event(enriched)
                            financial_ok = True
                except Exception as exc:
                    errors.append("comparison: " + str(exc)[:160])
            self._rebuild_comparisons(symbol)
            candles_ok = False
            try:
                candles = self.client.candles(symbol)
                self._save_candles(symbol, candles)
                actions = getattr(self.client, "last_corporate_actions", {}).get(symbol)
                if actions is not None:
                    prior_actions = self._meta("corporate_actions:" + symbol, {})
                    prior_actions.update(actions)
                    self._set_meta("corporate_actions:" + symbol, prior_actions)
                candles_ok = bool(candles)
            except Exception as exc:
                errors.append("candles: " + str(exc)[:160])
            with self._connection() as con:
                con.execute("UPDATE results_symbol_jobs SET last_financial=?,last_candles=COALESCE(?,last_candles),last_error=?,attempts=attempts+1 WHERE symbol=?", (now.isoformat(), now.isoformat() if candles_ok else None, "; ".join(errors) or None, symbol))
            if errors:
                status["errors"].append(symbol + ": " + "; ".join(errors))
            self._stop.wait(0.35)

    def _enrich_fyers(self, status, now):
        try:
            from .results_history import fetch_fyers_event_candles, fyers_configured
            from .earnings_research import reaction_session, _previous_day
        except ImportError:
            from results_history import fetch_fyers_event_candles, fyers_configured
            from earnings_research import reaction_session, _previous_day
        status["fyers_history_configured"] = fyers_configured()
        if not status["fyers_history_configured"]:
            return
        done = set(self._meta("fyers_history_done", []))
        jobs = self._meta("fyers_history_attempts", {})
        events = self.history(include_versions=False)
        events.sort(key=lambda event: jobs.get(event["id"], {}).get("last_attempt_at", ""))
        attempted = 0
        candidates = 0
        for event in events:
            if event.get("announcement_time_known") is False:
                continue
            session = reaction_session(event["announced_at"])
            day = session.get("date")
            if not session.get("calendar_verified") or not session.get("eligible") or not day or day >= now.date().isoformat():
                continue
            candidates += 1
            job = jobs.get(event["id"], {})
            retry = parse_datetime(job.get("retry_at"))
            if event["id"] in done or retry and retry > now or attempted >= self.symbol_batch or self._stop.is_set():
                continue
            attempted += 1
            attempts = int(job.get("attempts", 0)) + 1
            job = {"last_attempt_at": now.isoformat(), "attempts": attempts,
                   "retry_at": (now + timedelta(seconds=min(86_400, self.poll_seconds * 2 ** min(attempts, 8)))).isoformat()}
            try:
                candles = fetch_fyers_event_candles(event["symbol"], day, now=now)
                self._save_candles(event["symbol"], candles, source="FYERS historical candles")
                reaction_day = date.fromisoformat(day)
                previous_day = _previous_day(reaction_day)
                opening = datetime.combine(reaction_day, datetime.min.time(), IST).replace(hour=9, minute=15)
                previous_close = datetime.combine(previous_day, datetime.min.time(), IST).replace(hour=15, minute=25)
                required = {int((opening + timedelta(minutes=5 * i)).timestamp()) for i in range(9)}
                required.add(int(previous_close.timestamp()))
                available = {c["t"] for c in self.get_candles(event["symbol"]) if c.get("timeframe") == 5 and c.get("source") == "FYERS historical candles"}
                if required.issubset(available):
                    done.add(event["id"])
                    job["state"] = "complete"
                    job["retry_at"] = None
                else:
                    job["state"] = "partial"
                    job["missing_required_bars"] = len(required - available)
            except Exception as exc:
                job["state"] = "unavailable"
                status["errors"].append(str(exc)[:180])
                jobs[event["id"]] = job
                break  # A rejected token is not repeatedly probed in this cycle.
            jobs[event["id"]] = job
            self._stop.wait(0.35)
        current_ids = {event["id"] for event in events}
        status["fyers_history_complete_events"] = len(done & current_ids)
        status["fyers_history_pending_events"] = max(0, candidates - len(done & current_ids))
        self._set_meta("fyers_history_done", sorted(done))
        self._set_meta("fyers_history_attempts", jobs)

    def _save_candles(self, symbol, candles, source="Yahoo chart OHLC (adjustment basis unverified)"):
        # Uses the existing archive schema; only verified closed data is written.
        try:
            from .candle_archive import CandleArchive
        except ImportError:
            from candle_archive import CandleArchive
        archive = CandleArchive(self.candle_db_path)
        for timeframe in (5, 1440):
            rows = [c for c in candles if c.get("timeframe") == timeframe and c.get("closed") is True]
            archive.archive_closed_candles(symbol, timeframe, rows, source=source)

    def get_candles(self, symbol):
        if symbol.upper() not in self._universe():
            return []
        if not Path(self.candle_db_path).exists():
            return []
        with closing(sqlite3.connect(f"file:{urllib.parse.quote(self.candle_db_path)}?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute("SELECT ts AS t,o,h,l,c,v,timeframe,source FROM candles WHERE symbol=? AND timeframe IN (5,1440) ORDER BY ts", (symbol.upper(),)).fetchall()
            except sqlite3.OperationalError:
                return []
            actions = self._meta("corporate_actions:" + symbol.upper(), {})
            return [{**dict(row), "closed": True, "adjustments_verified": False,
                     "corporate_action": datetime.fromtimestamp(row["t"], IST).date().isoformat() in actions,
                     "corporate_action_types": actions.get(datetime.fromtimestamp(row["t"], IST).date().isoformat(), [])}
                    for row in rows]

    def history(self, symbol=None, include_versions=True):
        query = "SELECT payload FROM result_events WHERE announced_at>=? AND period_end>=?"
        params = (self.start_date, self.start_date)
        if symbol:
            query += " AND symbol=?"
            params += (symbol.upper(),)
        query += " ORDER BY announced_at DESC"
        with self._connection() as con:
            allowed = self._universe()
            current = [json.loads(row[0]) for row in con.execute(query, params)]
            current = [event for event in current if event["symbol"] in allowed]
            if not include_versions:
                return current
            ids = {event["id"] for event in current}
            version_query = "SELECT v.payload FROM result_versions v JOIN result_events e ON e.id=v.event_id WHERE e.announced_at>=? AND e.period_end>=?"
            if symbol:
                version_query += " AND e.symbol=?"
            versions = [json.loads(row[0]) for row in con.execute(version_query + " ORDER BY v.known_at", params)]
            # Flat immutable snapshots allow the research engine to select the
            # latest version actually available before each historical cutoff.
            return [event for event in versions if event["id"] in ids and self._in_scope(event, allowed)] + current

    def get_event(self, event_id):
        with self._connection() as con:
            row = con.execute("SELECT payload FROM result_events WHERE id=?", (str(event_id),)).fetchone()
            if not row:
                return None
            event = json.loads(row[0])
            if not self._in_scope(event):
                return None
            news = con.execute("SELECT payload FROM result_news WHERE symbol=? AND published_at>=? ORDER BY published_at DESC LIMIT 15", (event["symbol"], self.start_date)).fetchall()
            event["news"] = [json.loads(n[0]) for n in news]
            return event

    def list_events(self, date_from=None, date_to=None, symbol="", limit=100, search="", tone="all", offset=0):
        clauses, params = ["announced_at>=?", "period_end>=?"], [self.start_date, self.start_date]
        for value, op in ((date_from, ">="), (date_to, "<=")):
            if value:
                try:
                    normalized = date.fromisoformat(str(value)).isoformat()
                except ValueError as exc:
                    raise ValueError("Dates must use YYYY-MM-DD") from exc
                clauses.append(f"substr(announced_at,1,10){op}?")
                params.append(normalized)
        if date_from and date_to and str(date_from) > str(date_to):
            raise ValueError("Start date must not be after end date")
        if symbol:
            clauses.append("symbol=?")
            params.append(symbol.strip().upper())
        if tone not in ("all", "positive", "negative", "mixed", "unavailable"):
            raise ValueError("Unsupported result tone filter")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as con:
            records = con.execute("SELECT payload FROM result_events" + where + " ORDER BY announced_at DESC", params).fetchall()
        events = []
        allowed = self._universe()
        term = str(search).strip().lower()[:100]
        summary = {"positive": 0, "negative": 0, "mixed": 0, "unavailable": 0}
        for row in records:
            event = json.loads(row[0])
            if event["symbol"] not in allowed:
                continue
            if term and term not in (event["symbol"] + " " + event["company"]).lower():
                continue
            g = event.get("growth", {})
            values = [g.get("revenue_qoq"), g.get("profit_qoq")]
            if any(v is None for v in values):
                category = "unavailable"
            elif all(v > 0 for v in values):
                category = "positive"
            elif all(v < 0 for v in values):
                category = "negative"
            else:
                category = "mixed"
            event["tone"] = category
            summary[category] += 1
            if tone != "all" and category != tone:
                continue
            events.append(event)
        offset, limit = max(0, int(offset)), min(200, max(1, int(limit)))
        total = len(events)
        selected = events[offset:offset + limit]
        return {"events": selected, "total": total, "summary": summary, "offset": offset,
                "next_offset": offset + len(selected) if offset + len(selected) < total else None,
                "status": self.status()}

    def archive_scope_exclusions(self):
        """Move older declarations out of the active ledger, retaining recovery data."""
        stamp = _now().isoformat()
        with self._connection() as con:
            count = con.execute("SELECT count(*) FROM result_events WHERE announced_at<? OR period_end<?", (self.start_date, self.start_date)).fetchone()[0]
            con.execute("INSERT OR REPLACE INTO result_scope_archive SELECT id,symbol,announced_at,period_end,basis,payload,updated_at,?,? FROM result_events WHERE announced_at<? OR period_end<?", (stamp, "Outside the selected post-COVID date range", self.start_date, self.start_date))
            con.execute("DELETE FROM result_events WHERE announced_at<? OR period_end<?", (self.start_date, self.start_date))
        return {"archived": count, "scope_start_date": self.start_date}

    def status(self):
        status = self._meta("status", {"state": "not_started", "errors": [], "last_success_at": None})
        now = _now()
        stamp = parse_datetime(status.get("last_success_at"))
        allowed = self._universe()
        with self._connection() as con:
            events = [json.loads(row[0]) for row in con.execute("SELECT payload FROM result_events WHERE announced_at>=? AND period_end>=?", (self.start_date, self.start_date))]
            events = [event for event in events if event["symbol"] in allowed]
            count = len(events)
            financial = sum(e.get("financial_status") == "available" for e in events)
            stamps = sorted(e["announced_at"] for e in events)
            bounds = (stamps[0], stamps[-1]) if stamps else (None, None)
            jobs_rows = [row for row in con.execute("SELECT symbol,last_candles FROM results_symbol_jobs") if row[0] in allowed]
            jobs = (len(jobs_rows), sum(bool(row[1]) for row in jobs_rows))
        if not allowed:
            status = {**status, "state": "needs_catalogue", "errors": ["The website stock catalogue is empty or unavailable; no companies are selected."]}
        cursor = self._meta("backfill_cursor")
        effective_start = max(date.fromisoformat(self.start_date), now.date() - timedelta(days=self.lookback_days))
        return {**status, "scope_start_date": self.start_date, "effective_history_start": effective_start.isoformat(), "universe_count": len(allowed), "universe_source": "website stock catalogue", "running": bool(self._thread and self._thread.is_alive()), "refreshing": self._refresh_lock.locked(), "stale": not allowed or stamp is None or (now - stamp).total_seconds() > self.poll_seconds * 2 or status.get("state") == "unavailable", "poll_seconds": self.poll_seconds, "event_count": count, "financial_count": int(financial or 0), "earliest_announcement": bounds[0], "latest_announcement": bounds[1], "symbols_queued": jobs[0], "symbols_with_candles": int(jobs[1] or 0), "backfill_cursor": cursor, "lookback_days": self.lookback_days, "backfill_days": (now.date() - effective_start).days + 1, "backfill_complete": bool(cursor and cursor < effective_start.isoformat()), "backfill_chunks_remaining": max(0, ((date.fromisoformat(cursor) - effective_start).days + 30) // 30) if cursor else ((now.date() - effective_start).days + 30) // 30, "sources": [{"name": "NSE published financial filings", "url": RESULTS_PAGE}, {"name": "NSE corporate announcements", "url": ANNOUNCEMENTS_PAGE}, {"name": "Yahoo chart candles", "url": "https://finance.yahoo.com/"}], "coverage_note": "Only companies currently in the website stock catalogue; NSE filing coverage only. Numeric coverage depends on supported XBRL contexts. Yahoo intraday history is limited; long-term daily candles cannot verify a 09:15–09:40 pattern. Automatic refresh requires this server to remain running."}


_service = None
_service_lock = threading.Lock()


def get_results_service():
    global _service
    with _service_lock:
        if _service is None:
            _service = ResultsService()
        return _service
