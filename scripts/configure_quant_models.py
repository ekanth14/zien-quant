"""Create local PDF-informed Ollama variants without changing model weights.

Dry-run by default. Run with --apply to create new named variants from models
already installed locally. Existing source tags are never overwritten, and no
model downloads, deletions, training jobs or external API requests are performed.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marketscope.quant_knowledge import KNOWLEDGE_VERSION, formula_system_prompt


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Local Ollama redirects are not permitted")


def local_host(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("Ollama host must be an unauthenticated local HTTP endpoint")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Ollama host must not contain a path, query or fragment")
    hostname = parsed.hostname or ""
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    if not loopback or not parsed.port:
        raise ValueError("Only a loopback Ollama host with an explicit port is permitted")
    return value.rstrip("/")


def request_json(host: str, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        host + path, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def variant_name(source: str) -> str:
    """Map a source tag to a new predictable tag; never return the source itself."""
    name, sep, tag = source.partition(":")
    name = re.sub(r"[^a-z0-9_.-]", "-", name.lower())
    return f"{name}-quant:{tag if sep else 'latest'}"


def configure(host: str, apply: bool = False) -> Dict[str, Any]:
    host = local_host(host)
    system = formula_system_prompt()
    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()
    rows = request_json(host, "/api/tags").get("models", [])
    installed = {row["name"] for row in rows if isinstance(row.get("name"), str)}
    report: Dict[str, Any] = {
        "mode": "document_grounding", "weights_trained": False,
        "knowledge_version": KNOWLEDGE_VERSION, "knowledge_sha256": digest,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "applied": apply, "models": [], "primary_model": None,
    }
    sources = []
    for source in sorted(installed):
        # Managed variants are checked below instead of recursively deriving them.
        if "-quant:" in source:
            continue
        info = request_json(host, "/api/show", {"model": source})
        if "completion" not in info.get("capabilities", []):
            report["models"].append({
                "source": source, "status": "retained",
                "reason": "Non-completion model; document prompts do not retrain decision weights.",
            })
            continue
        sources.append(source)

    plans = [(source, variant_name(source)) for source in sources]
    preferred = next((s for s in sources if s.startswith("gemma4:")), None)
    if not preferred:
        preferred = next((s for s in sources if s.startswith("laya-ai:")), None)
    if not preferred and sources:
        preferred = sources[0]
    if preferred:
        plans.append((preferred, "laya-quant:latest"))

    for source, target in plans:
        if target == source:
            raise ValueError("Refusing to overwrite source model")
        row = {"source": source, "target": target, "status": "planned"}
        if target in installed:
            existing = request_json(host, "/api/show", {"model": target})
            if existing.get("system") == system:
                row["status"] = "already_configured"
            else:
                row["status"] = "retained_existing"
                row["reason"] = "Existing target has different settings; choose a new name before replacing it."
        elif apply:
            result = request_json(host, "/api/create", {
                "model": target, "from": source, "system": system,
                "parameters": {"temperature": 0.1, "num_ctx": 4096}, "stream": False,
            })
            if result.get("status") != "success":
                raise RuntimeError(f"Ollama did not confirm creation of {target}: {result}")
            verified = request_json(host, "/api/show", {"model": target})
            if verified.get("system") != system or "completion" not in verified.get("capabilities", []):
                raise RuntimeError(f"Created model {target} failed system/capability verification")
            row["status"] = "created"
        report["models"].append(row)
        if target == "laya-quant:latest" and row["status"] in ("created", "already_configured"):
            report["primary_model"] = target
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--apply", action="store_true", help="Create new local variants")
    parser.add_argument("--report", type=Path, help="Optional JSON report path inside this repository")
    args = parser.parse_args()
    try:
        report_path = args.report.resolve() if args.report else None
        if report_path and not report_path.is_relative_to(ROOT):
            raise ValueError("Report path must remain inside this repository")
        result = configure(args.host, args.apply)
        serialized = json.dumps(result, indent=2, ensure_ascii=True)
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 0
    except (ValueError, RuntimeError, OSError, urllib.error.URLError) as error:
        print(f"Local model configuration failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

