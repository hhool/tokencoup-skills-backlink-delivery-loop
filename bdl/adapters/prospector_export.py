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
  * `is_gap` is passed through when the upstream provides it, otherwise derived as
    "not acquired and not a competitor". The store file carries no `is_gap`; the
    CSV/UI export does, and that value must not be overwritten.
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
from ..models import normalize_domain

PROSPECTOR_REPO = "tokencoup-skills-backlink-prospector"

# Discovery fields copied from an enrichment file when one is supplied. They are
# observations of the site, so the producer never invents them: a missing key
# stays absent rather than becoming an empty string that reads like "not found".
_ENRICH_KEYS = (
    "submission_url",
    "form_shape",
    "form_fields",
    "contact_email",
    "contact_source",
    "contact_source_url",
    "enriched_at",
)


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


def load_enrichment(path: str | Path) -> Dict[str, Dict[str, Any]]:
    """Read a discovery overlay keyed by registrable host.

    The overlay is produced by ``tools/discover_entry.py`` in the prospector repo.
    Keeping it a separate file means discovery can be re-run without rewriting the
    store, and an export without an overlay is still a valid 1.1.0 snapshot.
    """
    src = Path(path)
    if not src.is_file():
        raise SnapshotError("Enrichment file not found", path=str(src))
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(
            "Enrichment file is not valid JSON", path=str(src), line=exc.lineno
        ) from exc
    if not isinstance(doc, dict):
        raise SnapshotError("Enrichment root must be an object", path=str(src))
    domains = doc.get("domains")
    if not isinstance(domains, dict):
        raise SnapshotError("Enrichment file is missing the domains map", path=str(src))
    index: Dict[str, Dict[str, Any]] = {}
    for key, record in domains.items():
        if not isinstance(record, dict):
            continue
        host = normalize_domain(str(record.get("domain") or key))
        if host:
            index[host] = record
    return index


def export_snapshot(
    store_path: str | Path,
    repo: str = PROSPECTOR_REPO,
    diagnostics: Dict[str, Any] | None = None,
    enrichment_path: str | Path = "",
) -> Dict[str, Any]:
    """Read a prospector store file and return a contract-C snapshot dict.

    ``diagnostics``, when supplied, is filled with provenance that cannot live
    inside the snapshot (the contract is closed): how many ``is_gap`` values were
    passed through versus derived, and how many prospects discovery reached.
    """
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
    basis_counts: Dict[str, int] = {}
    overlay = load_enrichment(enrichment_path) if enrichment_path else {}
    enriched = 0
    with_channel = 0
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
        # `is_gap` is passed through when the upstream carries it. The store file
        # does not (checked against a live store: the key set has no is_gap), so a
        # derived fallback is used there and flagged in `gap_basis`. The UI/CSV
        # export does carry it, and that value encodes upstream judgements this
        # tool cannot reproduce - a documentation page, or a competitor the flag
        # misses. Recomputing it would resurrect those rows as actionable targets.
        if isinstance(record.get("is_gap"), bool):
            is_gap = record["is_gap"]
            gap_basis = "upstream"
        else:
            is_gap = (not acquired) and (not is_competitor)
            gap_basis = "derived"

        entry = {
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
        }
        found = overlay.get(normalize_domain(domain)) if overlay else None
        if found:
            for key in _ENRICH_KEYS:
                if key in found and found[key] is not None:
                    entry[key] = found[key]
            if found.get("enriched_at"):
                enriched += 1
            if found.get("submission_url") or found.get("contact_email"):
                with_channel += 1
        prospects.append(entry)
        basis_counts[gap_basis] = basis_counts.get(gap_basis, 0) + 1

    if diagnostics is not None:
        diagnostics["is_gap_basis"] = dict(sorted(basis_counts.items()))
        diagnostics["prospects"] = len(prospects)
        diagnostics["enriched"] = enriched
        diagnostics["with_channel"] = with_channel
        diagnostics["enrichment_file"] = Path(enrichment_path).name if enrichment_path else ""

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
