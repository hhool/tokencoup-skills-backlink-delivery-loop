"""Adapter: import a backlink-prospector CSV export as a contract-C snapshot.

The prospector has two output surfaces:

  * ``data/store-<center_domain>.json`` - the internal store. `export-snapshot`
    reads this. It carries no ``is_gap`` column, so that field has to be derived.
  * ``backlink-prospects-<center_domain>-<stamp>.csv`` - the UI export. It DOES
    carry ``is_gap``, and it also carries ``valid`` and ``already_have``.

The CSV is the richer surface, but its column names differ from the snapshot
contract (``already_have`` vs ``acquired``) and it has columns the closed
snapshot contract has no home for. This adapter performs that mapping explicitly
instead of silently dropping information:

  * ``already_have``  -> ``acquired``
  * ``is_gap``        -> passed through, AND-ed with ``valid`` (an invalid record
                        is not a gap). Both folds are counted in ``diagnostics``.
  * unmapped columns  -> reported as ``dropped_columns``; they are commercial
                        scores (authority, relevance, obtainability, tranco rank,
                        type labels) that the ledger does not store.

Rows are de-duplicated on the normalised host, keeping the highest priority. The
output validates against ``contracts/prospect-snapshot.schema.json`` unchanged.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import CONTRACTS_VERSION
from ..errors import SnapshotError
from ..models import domain_from_text, normalize_domain

PROSPECTOR_REPO = "tokencoup-skills-backlink-prospector"

# Columns that map onto the snapshot contract.
_MAPPED = {
    "url", "domain", "type_key", "priority", "http_status",
    "is_gap", "is_competitor", "already_have", "valid", "note", "note_en",
}

# Columns that are known but have no home in the closed contract.
_KNOWN_UNMAPPED = {
    "type_label_zh", "type_label_en", "authority_tier", "relevance",
    "authority_score", "obtainability", "tranco_global_rank", "source",
}

_FILENAME_RE = re.compile(
    r"^backlink-prospects-(?P<center>[A-Za-z0-9.\-]+\.[A-Za-z]{2,})-\d{8}T\d{6}Z$"
)
_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}


def _bool(raw: Optional[str]) -> Optional[bool]:
    text = (raw or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def _int(raw: Optional[str]) -> Optional[int]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def center_from_filename(path: Path) -> str:
    """Recover the centre domain from the prospector's export naming convention."""
    match = _FILENAME_RE.match(path.stem)
    return match.group("center").lower() if match else ""


def import_csv(
    csv_path: str | Path,
    center_domain: str = "",
    repo: str = PROSPECTOR_REPO,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read a prospector CSV export and return a contract-C snapshot dict."""
    src = Path(csv_path)
    if not src.is_file():
        raise SnapshotError("Prospect CSV not found", path=str(src))

    payload = src.read_bytes()
    try:
        text = payload.decode("utf-8-sig")  # the export is UTF-8 with a BOM
    except UnicodeDecodeError as exc:
        raise SnapshotError("Prospect CSV is not UTF-8", path=str(src), reason=str(exc)) from exc

    reader = csv.DictReader(io.StringIO(text))
    columns = [name.strip() for name in (reader.fieldnames or [])]
    if "domain" not in columns:
        raise SnapshotError(
            "Prospect CSV is missing the domain column", path=str(src), columns=columns or []
        )

    center = (center_domain or "").strip().lower() or center_from_filename(src)
    if not center:
        raise SnapshotError(
            "Cannot determine the centre domain",
            hint="Pass --center-domain or BDL_CENTER_DOMAIN",
            path=str(src),
        )

    kept: Dict[str, Dict[str, Any]] = {}
    collapsed = 0
    folded_invalid = 0
    skipped_no_domain = 0

    for raw_row in reader:
        row = {(key or "").strip(): (value or "").strip() for key, value in raw_row.items()}
        domain = row.get("domain", "").lower()
        if not domain:
            domain = domain_from_text(row.get("url", ""))
        if not domain:
            skipped_no_domain += 1
            continue

        key = normalize_domain(domain)
        if not key:
            skipped_no_domain += 1
            continue

        valid = _bool(row.get("valid"))
        is_gap_raw = _bool(row.get("is_gap"))
        if is_gap_raw is None:
            is_gap = (not (_bool(row.get("already_have")) or False)) and not (
                _bool(row.get("is_competitor")) or False
            )
        else:
            is_gap = is_gap_raw
        if valid is False and is_gap:
            is_gap = False
            folded_invalid += 1

        url = row.get("url", "")
        if not url:
            url = "https://" + domain

        record = {
            "domain": domain,
            "url": url,
            "type_key": (row.get("type_key") or "unknown").lower(),
            "priority": _int(row.get("priority")) or 0,
            "http_status": _int(row.get("http_status")),
            "acquired": bool(_bool(row.get("already_have"))),
            "is_competitor": bool(_bool(row.get("is_competitor"))),
            "is_gap": bool(is_gap),
            "note": row.get("note", ""),
            "note_en": row.get("note_en", ""),
            "first_seen": None,
            "last_seen": None,
        }

        existing = kept.get(key)
        if existing is not None:
            collapsed += 1
            if record["priority"] <= existing["priority"]:
                continue
        kept[key] = record

    if not kept:
        raise SnapshotError("Prospect CSV contained no usable rows", path=str(src))

    prospects: List[Dict[str, Any]] = [kept[key] for key in sorted(kept)]
    dropped_columns = [name for name in columns if name not in _MAPPED and name]

    if diagnostics is not None:
        diagnostics.update({
            "center_domain": center,
            "rows_read": len(prospects) + collapsed + skipped_no_domain,
            "prospects": len(prospects),
            "collapsed_duplicates": collapsed,
            "skipped_without_domain": skipped_no_domain,
            "is_gap_folded_for_invalid": folded_invalid,
            "dropped_columns": dropped_columns,
            "unmapped_known_columns": [c for c in dropped_columns if c in _KNOWN_UNMAPPED],
        })

    return {
        "center_domain": center,
        "schema_version": CONTRACTS_VERSION,
        "generated_at": _now(),
        "source": {
            "repo": repo,
            "file": src.name,
            "content_hash": _sha256_bytes(payload),
        },
        "prospects": prospects,
    }


def _now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def peek_columns(csv_path: str | Path) -> Tuple[List[str], int]:
    """Return (columns, row count) without building a snapshot. Used by --dry-run."""
    src = Path(csv_path)
    text = src.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return [name.strip() for name in (reader.fieldnames or [])], len(rows)
