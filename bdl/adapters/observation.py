"""Adapter: the local observation ledger and confirm-proposal queue.

This is the "did the link go live?" record. It lives entirely on disk and never
mutates the shared link-ledger directly. When auto-confirm is enabled, a found
observation is marked ``live`` *here* and a confirm proposal is written; a human
(or a separate SEXTANT step) still decides whether to promote the shared ledger
state. That separation keeps R-512's human gate intact.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

from ..errors import WriteError


def _append(path: Path, record: Dict[str, Any]) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        raise WriteError("Cannot append observation", path=str(path), reason=exc.strerror) from exc
    return path


def append_observation(path: Union[str, Path], record: Dict[str, Any]) -> Path:
    return _append(Path(path), record)


def append_proposal(path: Union[str, Path], proposal: Dict[str, Any]) -> Path:
    return _append(Path(path), proposal)


def load_observations(path: Union[str, Path]) -> List[Dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def load_observations_dir(path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read every observation ledger under a directory (or a single file)."""
    target = Path(path)
    if target.is_file():
        return load_observations(target)
    if not target.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for candidate in sorted(target.rglob("*.jsonl")):
        rows.extend(load_observations(candidate))
    return rows


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
