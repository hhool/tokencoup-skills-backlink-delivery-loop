"""Adapter: consume versioned prospect snapshots exported by backlink-prospector.

Contract C: the upstream exports a snapshot; this package only reads it. No direct
access to the upstream store file and no dependency on its private layout.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .. import CONTRACTS_VERSION
from ..errors import SnapshotError
from ..models import Prospect, Snapshot

SUPPORTED_VERSIONS = {CONTRACTS_VERSION}
_TOP_KEYS = {"center_domain", "schema_version", "generated_at", "source", "prospects"}
_SOURCE_KEYS = {"repo", "file", "content_hash"}
_PROSPECT_KEYS = {
    "domain", "url", "type_key", "priority", "http_status", "acquired",
    "is_competitor", "is_gap", "note", "note_en", "first_seen", "last_seen",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _reject_unknown(where: str, actual: set, allowed: set) -> None:
    extra = actual - allowed
    if extra:
        raise SnapshotError("Unknown field in snapshot", where=where, fields=sorted(extra))


def _require_mapping(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotError("Expected a JSON object", where=where)
    return value


def _parse_prospect(raw: Any, index: int) -> Prospect:
    where = "prospects[%d]" % index
    item = _require_mapping(raw, where)
    _reject_unknown(where, set(item), _PROSPECT_KEYS)
    for key in ("domain", "url", "type_key"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise SnapshotError("Missing or invalid required field", where=where, field=key)
    if not isinstance(item.get("priority"), int) or isinstance(item.get("priority"), bool):
        raise SnapshotError("priority must be an integer", where=where)
    status = item.get("http_status")
    if status is not None and (not isinstance(status, int) or isinstance(status, bool)):
        raise SnapshotError("http_status must be an integer or null", where=where)
    for key in ("acquired", "is_competitor", "is_gap"):
        if not isinstance(item.get(key), bool):
            raise SnapshotError("Field must be a boolean", where=where, field=key)
    return Prospect(
        domain=item["domain"].strip().lower(),
        url=item["url"].strip(),
        type_key=item["type_key"].strip().lower(),
        priority=item["priority"],
        http_status=status,
        acquired=item["acquired"],
        is_competitor=item["is_competitor"],
        is_gap=item["is_gap"],
        note=item.get("note", "") or "",
        note_en=item.get("note_en", "") or "",
        first_seen=item.get("first_seen"),
        last_seen=item.get("last_seen"),
    )


def load_snapshot(path: str | Path, expected_center_domain: str = "") -> Snapshot:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotError("Cannot read snapshot", path=str(target), reason=exc.strerror) from exc

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotError("Snapshot is not valid JSON", path=str(target), line=exc.lineno) from exc

    root = _require_mapping(doc, "$")
    _reject_unknown("$", set(root), _TOP_KEYS)
    missing = _TOP_KEYS - set(root)
    if missing:
        raise SnapshotError("Snapshot is missing required fields", fields=sorted(missing))

    version = root["schema_version"]
    if version not in SUPPORTED_VERSIONS:
        raise SnapshotError(
            "Unsupported snapshot schema version",
            version=version,
            supported=sorted(SUPPORTED_VERSIONS),
        )

    source = _require_mapping(root["source"], "source")
    _reject_unknown("source", set(source), _SOURCE_KEYS)
    if not _HASH_RE.match(str(source.get("content_hash", ""))):
        raise SnapshotError("source.content_hash must be sha256:<64 hex>", where="source")

    center = str(root["center_domain"]).strip().lower()
    if not center:
        raise SnapshotError("center_domain must not be empty")
    if expected_center_domain and center != expected_center_domain.strip().lower():
        raise SnapshotError(
            "Snapshot center_domain does not match the requested domain",
            expected=expected_center_domain,
            actual=center,
        )

    if not isinstance(root["prospects"], list):
        raise SnapshotError("prospects must be an array", where="prospects")

    prospects: List[Prospect] = []
    seen = set()
    for index, raw_item in enumerate(root["prospects"]):
        prospect = _parse_prospect(raw_item, index)
        if prospect.key in seen:
            raise SnapshotError("Duplicate prospect domain", where="prospects[%d]" % index, domain=prospect.key)
        seen.add(prospect.key)
        prospects.append(prospect)

    file_hash = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return Snapshot(
        center_domain=center,
        schema_version=version,
        generated_at=str(root["generated_at"]),
        source=dict(source),
        prospects=prospects,
        file_hash=file_hash,
    )
