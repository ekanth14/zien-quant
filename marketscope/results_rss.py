"""Recent financial filings from NSE's explicitly published public RSS feeds.

RSS is a bounded latest-items feed, never a historical range API. Symbols,
periods and reporting scope are read from the linked official XBRL document;
the feed's actual publication timestamp is retained without inventing dates.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
RSS_FEEDS = (
    "https://nsearchives.nseindia.com/content/RSS/Integrated_Filing_Financials.xml",
    "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml",
)
MAX_XML_BYTES = 12_000_000


def _local(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _xml(content: bytes) -> ET.Element:
    if not isinstance(content, bytes) or len(content) > MAX_XML_BYTES:
        raise ValueError("Unsupported or oversized exchange XML")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", content, re.I):
        raise ValueError("Entity declarations are not accepted")
    return ET.fromstring(content)


def _publication(value: str) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(IST)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(IST)
    except ValueError:
        pass
    return None


def official_document_url(value: str) -> str | None:
    """Only exchange archive XML documents; never arbitrary RSS-provided URLs."""
    try:
        parsed = urllib.parse.urlsplit(str(value).strip())
        allowed_host = parsed.hostname in ("nsearchives.nseindia.com", "archives.nseindia.com", "www.nseindia.com")
        if (parsed.scheme != "https" or not allowed_host or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment or parsed.query):
            return None
        if not parsed.path.startswith("/corporate/") or not parsed.path.lower().endswith(".xml"):
            return None
        return urllib.parse.urlunsplit(parsed)
    except (ValueError, TypeError):
        return None


def parse_feed(content: bytes, source: str) -> list[dict]:
    if source not in RSS_FEEDS:
        raise ValueError("Unsupported exchange feed")
    root = _xml(content)
    if _local(root.tag) != "rss":
        raise ValueError("Exchange response is not RSS")
    channel = next((node for node in root if _local(node.tag) == "channel"), None)
    if channel is None:
        raise ValueError("RSS channel is missing")
    entries = []
    for node in channel:
        if _local(node.tag) != "item":
            continue
        values = {_local(child.tag): (child.text or "").strip() for child in node}
        published = _publication(values.get("pubdate", ""))
        link = official_document_url(values.get("link", ""))
        if not published or not link:
            continue
        entries.append({"company": values.get("title", "")[:300], "document_url": link,
                        "published_at": published, "description": values.get("description", "")[:800],
                        "feed_url": source})
    return entries


def document_filings(content: bytes, item: dict) -> list[dict]:
    """Extract current-period filing identity, never prior-quarter announcements."""
    root = _xml(content)
    if _local(root.tag) != "xbrl":
        raise ValueError("Linked filing is not an XBRL instance")
    tags = {
        "symbol": "symbol", "nameofthecompany": "company", "nameofcompany": "company",
        "dateofstartofreportingperiod": "start", "dateofendofreportingperiod": "end",
        "natureofreportstandaloneconsolidated": "basis", "natureofreportstandaloneorconsolidated": "basis",
        "typeofreportingperiod": "period_type",
    }
    contexts = {}
    for element in root.iter():
        name = tags.get(_local(element.tag))
        context = element.attrib.get("contextRef") or element.attrib.get("contextref")
        if not name or not context:
            continue
        value = "".join(element.itertext()).strip()
        if not value:
            continue
        facts = contexts.setdefault(context, {})
        if name in facts and facts[name] != value:
            raise ValueError("Conflicting filing identity facts")
        facts[name] = value
    available = []
    for facts in contexts.values():
        symbol = facts.get("symbol", "").strip().upper()
        basis = facts.get("basis", "").strip().lower().replace(" ", "")
        if symbol in ("NOTLISTED", "NA", "NONE") or not re.fullmatch(r"[A-Z0-9&.\-]{1,30}", symbol):
            continue
        if basis not in ("standalone", "consolidated"):
            continue
        try:
            ended = date.fromisoformat(facts.get("end", ""))
            started = date.fromisoformat(facts["start"]) if facts.get("start") else None
        except ValueError:
            continue
        if ended > item["published_at"].date() or started and started > ended:
            continue
        available.append((ended, basis, facts))
    if not available:
        raise ValueError("Verified symbol, period and reporting scope are unavailable")
    newest = max(row[0] for row in available)
    rows = {}
    for ended, basis, facts in available:
        if ended != newest:
            continue
        # Same document can carry quarterly and YTD contexts for the same date.
        # Those contexts identify one filing, not multiple announcements.
        key = (facts["symbol"].upper(), basis)
        published = item["published_at"].isoformat()
        revised = "revision" in item.get("description", "").casefold()
        rows[key] = {
            "symbol": key[0], "cmName": facts.get("company") or item.get("company") or key[0],
            "creation_Date": None if revised else published, "qe_Date": ended.isoformat(),
            "consolidated": basis, "xbrl": item["document_url"],
            "seq_Id": urllib.parse.urlsplit(item["document_url"]).path.rsplit("/", 1)[-1],
            "revision_Date": published if revised else None,
            "reporting_period_type": facts.get("period_type"),
            "rss_source_url": item["feed_url"], "rss_recent_only": True,
        }
    return list(rows.values())


class RSSResultsFeed:
    """Read latest public RSS and cache linked immutable filing URLs.

    The injected reader performs ordinary bounded HTTP reads. It receives only
    exact published feed URLs or allow-listed document URLs. Document cache
    entries are revalidated daily or immediately when publication time changes.
    """
    def __init__(self, read_bytes, cache_dir=None, max_documents=20, cache_seconds=86_400):
        self.read_bytes = read_bytes
        self.cache_dir = Path(cache_dir) if cache_dir is not None else Path(os.environ.get(
            "MS_RESULTS_RSS_CACHE_PATH", Path(__file__).parent.parent / "work" / "runtime" / "results-rss-cache"))
        self.max_documents = min(20, max(1, int(max_documents)))
        self.cache_seconds = max(60, int(cache_seconds))
        self.last_coverage = {}
        self.errors = []
        self._lock = threading.Lock()
        self._memory = {}
        self._cached_reads = 0
        self._downloaded_reads = 0

    def _document(self, item: dict, now: datetime) -> bytes:
        url = official_document_url(item["document_url"])
        if not url:
            raise ValueError("Unsupported filing URL")
        key = hashlib.sha256(url.encode()).hexdigest()
        cached = self._memory.get(key)
        path = self.cache_dir / (key + ".json")
        if cached is None and path.is_file():
            try:
                if path.stat().st_size <= MAX_XML_BYTES * 2:
                    cached = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                cached = None
        publication = item["published_at"].isoformat()
        if cached and cached.get("url") == url and cached.get("published_at") == publication:
            stored = _publication(cached.get("stored_at", ""))
            if stored and 0 <= (now - stored).total_seconds() <= self.cache_seconds:
                content = cached.get("xml", "").encode("utf-8")
                _xml(content)
                self._memory[key] = cached
                while len(self._memory) > 32:
                    self._memory.pop(next(iter(self._memory)))
                self._cached_reads += 1
                return content
        content = self.read_bytes(url)
        _xml(content)
        cached = {"url": url, "published_at": publication, "stored_at": now.isoformat(),
                  "xml": content.decode("utf-8-sig")}
        self._memory[key] = cached
        while len(self._memory) > 32:
            self._memory.pop(next(iter(self._memory)))
        self._downloaded_reads += 1
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            # A read-only filesystem should not turn valid published data into
            # a failed source. The current instance retains the memory cache.
            pass
        return content

    def filings(self, start: date, end: date) -> list[dict]:
        if not isinstance(start, date) or not isinstance(end, date) or start > end:
            raise ValueError("A valid filing date range is required")
        with self._lock:
            now = datetime.now(IST)
            self.errors = []
            self._cached_reads = self._downloaded_reads = 0
            items, feeds_ok = [], 0
            for source in RSS_FEEDS:
                try:
                    items.extend(parse_feed(self.read_bytes(source), source))
                    feeds_ok += 1
                except Exception as exc:
                    self.errors.append(f"Published RSS unavailable: {source} ({type(exc).__name__}).")
            unique = {}
            for item in sorted(items, key=lambda row: row["published_at"], reverse=True):
                unique.setdefault(item["document_url"], item)
            available = list(unique.values())
            eligible = [item for item in available if start <= item["published_at"].date() <= end]
            selected = eligible[:self.max_documents]
            records = []
            for item in selected:
                try:
                    records.extend(document_filings(self._document(item, now), item))
                except Exception as exc:
                    self.errors.append(f"Filing identity unavailable: {item['document_url']} ({type(exc).__name__}).")
            self.last_coverage = {
                "source": "NSE published RSS", "recent_only": True,
                "history_complete": False, "feeds_ok": feeds_ok,
                "feed_items": len(available), "eligible_items": len(eligible),
                "documents_considered": len(selected), "records": len(records),
                "cached_documents": self._cached_reads, "downloaded_documents": self._downloaded_reads,
                "earliest_publication": min((item["published_at"] for item in available), default=None).isoformat() if available else None,
                "latest_publication": max((item["published_at"] for item in available), default=None).isoformat() if available else None,
                "checked_at": now.isoformat(), "errors": list(self.errors),
                "note": "Latest published RSS items only; this feed cannot fill or advance a historical backfill cursor.",
            }
            if not feeds_ok:
                raise RuntimeError("NSE public RSS feeds are unavailable")
            if selected and not records:
                raise RuntimeError("No selected RSS filing has a verified document identity")
            return records
