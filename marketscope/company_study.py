"""Read-only adapter for the supplied, dated company-earnings research bundle.

Never merges research candles/filings into production stores or accepts client
snapshots, verification flags, registry paths or order instructions.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path

STUDY_ROOT = Path(__file__).resolve().parent.parent / "research" / "company-positive-earnings-study"
LANDMARKS = ("09:30", "09:40", "10:00", "10:30")


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _bridge():
    path = STUDY_ROOT / "website" / "forecast_bridge.py"
    spec = importlib.util.spec_from_file_location("zien_company_forecast_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompanyStudy:
    def __init__(self, root=STUDY_ROOT):
        self.root = Path(root)
        hashes = _json(self.root / "SHA256SUMS.json")
        required = ("local_data/positive_events.jsonl", "local_data/local_expansion_summary.json",
                    "model/local_model_dataset_with_market.json", "public_data/benchmark_mornings.json")
        for name in required:
            if hashlib.sha256((self.root / name).read_bytes()).hexdigest() != hashes.get(name):
                raise ValueError("Research snapshot integrity check failed")
        self.pairs = [json.loads(line) for line in (self.root / required[0]).read_text(encoding="utf-8").splitlines() if line]
        self.summary = _json(self.root / required[1])
        self.dataset = _json(self.root / required[2])
        self.benchmark = _json(self.root / required[3])
        self.rows = {(r["symbol"], r["session_date"], r["decision_at"][11:16]): r for r in self.dataset["rows"]}
        self.registry = self.root / "model" / "registry"
        self.events = []
        for pair in self.pairs:
            event = dict(pair["event"])
            event["id"] = hashlib.sha256("|".join(str(event[k]) for k in ("symbol", "period_end", "next_trading_date")).encode()).hexdigest()[:24]
            self.events.append(event)

    def overview(self):
        complete = [e for e in self.events if e["complete_price_window"]]
        return {
            "status": "IMPORTED_RESEARCH_SNAPSHOT", "as_of": self.summary["created_at"],
            "scope_start": "2023-05-06", "orders_enabled": False,
            "counts": {"company_periods": self.summary["eligible_company_periods"],
                       "positive_events": len(self.events), "complete_windows": len(complete),
                       "missing_windows": len(self.events)-len(complete),
                       "dates": len({e["next_trading_date"] for e in complete}),
                       "above_open": sum(e["return_0915_1100_pct"] > 0 for e in complete),
                       "below_open": sum(e["return_0915_1100_pct"] < 0 for e in complete),
                       "document_requests": self.summary["financial_document_requests"]},
            "years": self.summary["years"], "limitations": self.summary["limitations"],
            "models": _bridge().model_status(self.registry)["models"],
            "landmarks": list(LANDMARKS),
            "note": "Dated supplied observations; not a live feed or a validated prediction. The frozen margin cohort is not historical leverage eligibility.",
        }

    def response(self, *, event_id=None, search="", coverage="all", landmark="09:40"):
        if landmark not in LANDMARKS or coverage not in ("all", "complete", "missing") or len(search) > 100:
            raise ValueError("Invalid research filter")
        result = self.overview()
        if event_id:
            if not re.fullmatch(r"[a-f0-9]{24}", event_id):
                raise ValueError("Invalid research event ID")
            index = next((i for i, event in enumerate(self.events) if event["id"] == event_id), None)
            if index is None:
                raise KeyError("Research event not found")
            pair, event = self.pairs[index], self.events[index]
            row = self.rows.get((event["symbol"], event["next_trading_date"], landmark))
            if row:
                # Future labels remain offline evaluation data, never inference input.
                clean_row = {k: v for k, v in row.items() if k not in ("targets", "target_available_at")}
                decision = clean_row["decision_at"]
                from datetime import datetime
                prefix = [bar for bar in pair["candles"] if bar["ts"]+300 <= datetime.fromisoformat(decision).timestamp()]
                snapshot = {"row": clean_row, "current": pair["current"]["fact"],
                            "previous": pair["previous"]["fact"], "bars": prefix,
                            "benchmark_bars": self.benchmark.get("sessions", {}).get(event["next_trading_date"], {}).get("bars", [])}
                forecast = _bridge().predict_from_snapshot(snapshot, self.registry)
            else:
                forecast = {"status": "PRICE_WINDOW_MISSING", "predictions": None, "orders_enabled": False,
                            "blockers": ["No complete company price window in the supplied snapshot"]}
            result["detail"] = {"event": event, "current": pair["current"]["fact"],
                                "previous": pair["previous"]["fact"], "candles": pair["candles"],
                                "forecast": forecast, "observed_features": row.get("features") if row else None,
                                "quality": row.get("quality") if row else None,
                                "landmark": landmark, "source_urls": event["source_urls"].split("|")}
        query = search.casefold().strip()
        result["events"] = [e for e in self.events if (not query or query in (e["symbol"]+" "+e["company"]).casefold())
                            and (coverage == "all" or e["complete_price_window"] == (coverage == "complete"))]
        return result


@lru_cache(maxsize=1)
def get_company_study():
    return CompanyStudy()
