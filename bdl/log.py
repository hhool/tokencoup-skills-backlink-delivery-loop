"""Structured JSON logging. Addresses and secrets are never written verbatim."""

from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
from typing import Any, IO, Optional

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def new_batch_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]


def mask(value: str) -> str:
    """Return a stable, non-reversible fingerprint for a sensitive value."""
    if not value:
        return ""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class JsonLogger:
    def __init__(
        self,
        batch_id: str,
        level: str = "INFO",
        stream: Optional[IO[str]] = None,
    ) -> None:
        self.batch_id = batch_id
        self.threshold = _LEVELS.get(level.upper(), 20)
        self.stream = stream or sys.stderr

    def emit(self, event: str, level: str = "INFO", **fields: Any) -> None:
        if _LEVELS.get(level.upper(), 20) < self.threshold:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": level.upper(),
            "event": event,
            "batch_id": self.batch_id,
            **fields,
        }
        self.stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.stream.flush()
