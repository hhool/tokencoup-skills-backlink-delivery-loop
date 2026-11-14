"""Shared helpers for the offline test suite."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SNAPSHOT = ASSETS / "prospect-snapshot.example.json"
PROSE = ASSETS / "prose"
HISTORY = ASSETS / "send-history.example.json"


def snapshot_doc() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def write_doc(path: Path, doc: dict) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path
