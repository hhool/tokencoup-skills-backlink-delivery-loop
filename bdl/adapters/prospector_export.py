"""Bridge: produce a versioned contract-C snapshot from a backlink-prospector store.

This module is the CONTRACT C producer. It reads the prospector's internal store
file (store-<center_domain>.json) and emits a prospect-snapshot document that the
consumer adapter (bdl/adapters/snapshot.py) validates. The consumer itself never
touches the prospector layout; this bridge is the only place that does, which is
why it lives under `adapters` rather than `services`.

Mapping rules:
  * Every pool domain becomes one prospect; admission (skip acquired, exclude
    competitor / reciprocal) is the CONSUMER's job, not the producer's.
  * `url` is copied verbatim as the site entry point. It is NOT a landing page;
    the consumer never uses it as link-ledger.target_url (see normalize.py).
  * `is_gap` mirrors the prospector's own definition: not acquired and not a
    competitor.
  * `source.content_hash` is the sha256 of the exact store file bytes, so a
    re-export after any store change yields a different hash and the consumer
    will re-validate.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from .. import CONTRACTS_VERSION
from ..errors import SnapshotError

PROSPECTOR_REPO = "tokencoup-skills-backlink-prospector"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def export_snapshot(store_path: str | Path, repo: str = PROSPECTOR_REPO) -> Dict[str, Any]:
    """Read a prospector store file and return a contract-C snapshot dict."""
    src = Path(store_path)
    if not src.is_file():
        raise SnapshotError("Prospector store file not found", path=str(src))

    raw = src.read_text(encoding="utf-8")
    try:
        store = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotError("Prospector store is not valid JSON", path=str(src), line=exc.lineno) from exc
    if not isinstance(store, dict):
        raise SnapshotError("Prospector store root must be an object", path=str(src))

    center = str(store.get("center_domain") or "").strip().lower()
    if not center:
        raise SnapshotError("Prospector store is missing center_domain", path=str(src))

    domains = store.get("domains")
    if not isinstance(domains, dict):
        raise SnapshotError("Prospector store is missing the domains map", path=str(src))

    prospects: List[Dict[str, Any]] = []
    for domain_key, record in domains.items():
        if not isinstance(record, dict):
            continue
        domain = str(record.get("domain") or domain_key or "").strip().lower()
        if not domain:
            continue

        url = str(record.get("url") or "").strip()
        if not url:
            url = "https://" + domain  # entry-point fallback; never used as a landing page

        type_key = str(record.get("type_key") or "unknown").strip().lower()
        priority = _coerce_int(record.get("priority"), 0)
        http_status = record.get("http_status")
        if http_status is not None:
            http_status = _coerce_int(http_status, None) or None

        acquired = bool(record.get("acquired"))
        is_competitor = bool(record.get("is_competitor"))
        is_gap = (not acquired) and (not is_competitor)

        prospects.append({
            "domain": domain,
            "url": url,
            "type_key": type_key,
            "priority": priority,
            "http_status": http_status,
            "acquired": acquired,
            "is_competitor": is_competitor,
            "is_gap": is_gap,
            "note": str(record.get("note") or ""),
            "note_en": "",
            "first_seen": record.get("first_seen"),
            "last_seen": record.get("last_seen"),
        })

    return {
        "center_domain": center,
        "schema_version": CONTRACTS_VERSION,
        "generated_at": _now(),
        "source": {
            "repo": repo,
            "file": src.name,
            "content_hash": _sha256_file(src),
        },
        "prospects": prospects,
    }
