"""Adapter: atomic manifest writes and append-only receipts.

Nothing in this module transmits data. It writes local artefacts for a human to
review, and an evidence trail that can be replayed.

History is a derived, replayable record. A single `build-manifest` run may draw
on several sources (a primary JSON file, a directory of dated JSONL files, or a
glob) - `collect_history` aggregates them all behind one interface.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence, Union

from ..errors import WriteError


def _decode_history(payload: Any) -> List[dict]:
    if isinstance(payload, dict):
        rows = payload.get("rows")
        return rows if isinstance(rows, list) else []
    if isinstance(payload, list):
        return payload
    return []

_MANIFEST_KEYS = {
    "center_domain", "batch_id", "generated_at", "schema_version",
    "policy", "summary", "rows", "gaps",
}


def atomic_write_json(path: str | Path, payload: Dict[str, Any]) -> Path:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError as exc:
        raise WriteError("Cannot write artefact", path=str(target), reason=exc.strerror) from exc
    return target


def write_manifest(path: str | Path, manifest: Dict[str, Any]) -> Path:
    extra = set(manifest) - _MANIFEST_KEYS
    if extra:
        raise WriteError("Manifest contains unknown keys", fields=sorted(extra))
    return atomic_write_json(path, manifest)


def append_receipt(path: str | Path, record: Dict[str, Any]) -> Path:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        raise WriteError("Cannot append receipt", path=str(target), reason=exc.strerror) from exc
    return target


def read_history(path: str | Path) -> list:
    target = Path(path)
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _decode_history(payload)


def collect_history(sources: Sequence[Union[str, Path]]) -> List[dict]:
    """Aggregate rows from many history sources.

    Each source may be:
      * a JSON file with ``{"rows": [...]}`` or a bare list;
      * a directory - every ``*.json``/``*.jsonl`` file inside is read;
      * a glob pattern - every matching file is read.

    Missing or unreadable sources are silently ignored (history is a soft
    signal; an absent log must never abort planning).
    """
    rows: List[dict] = []
    for raw in sources or []:
        candidate = Path(raw)
        paths: List[Path] = []
        if candidate.is_dir():
            paths = sorted(candidate.rglob("*.json")) + sorted(candidate.rglob("*.jsonl"))
        elif candidate.exists():
            paths = [candidate]
        else:
            paths = [Path(p) for p in sorted(glob.glob(str(raw)))]
        for target in paths:
            if not target.is_file():
                continue
            text = ""
            try:
                text = target.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                # a .jsonl file: one object per line
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                continue
            rows.extend(_decode_history(payload))
    return rows


def lookup_history(domain: str, sources: Sequence[Union[str, Path]]) -> List[dict]:
    """Return every send/observe event recorded for one target domain."""
    from ..models import normalize_domain

    target = normalize_domain(domain)
    if not target:
        return []
    rows = collect_history(sources)
    return [row for row in rows if normalize_domain(str(row.get("target_domain", ""))) == target]
