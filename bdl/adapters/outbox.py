"""Adapter: atomic manifest writes and append-only receipts.

Nothing in this module transmits data. It writes local artefacts for a human to
review, and an evidence trail that can be replayed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from ..errors import WriteError

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
    return payload.get("rows", []) if isinstance(payload, dict) else []
