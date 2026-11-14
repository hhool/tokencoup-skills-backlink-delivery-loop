"""Adapter: the append-only delivery event log.

Every action a human actually performs - submitted a form, sent a mail, got a
reply, saw a bounce, closed a domain - is an event. The event log is the only
source of truth for "is this domain in delivery or done"; manifests are
snapshots of a moment and can never answer that question.

Two properties matter:

  * Append-only. Nothing is edited or deleted; corrections are new events.
  * Idempotent. ``event_id`` is a hash of (centre, target, channel, action, at),
    so re-running a command with the same timestamp cannot double-count. A
    duplicate is refused, not silently appended.

This module writes only to local disk. It never transmits anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ..errors import WriteError

EVENT_KIND = "delivery_event"

ACTIONS = (
    "queued",      # approved, waiting for a human to transmit
    "sent",        # email accepted by the provider (needs provider_id)
    "submitted",   # form posted and acknowledged (needs evidence)
    "delivered",   # provider reports delivery
    "bounced",     # provider reports a bounce or complaint
    "reply",       # a human reply arrived (needs reply_class)
    "closed",      # a human closed the domain (e.g. manual decision)
)

REPLY_CLASSES = ("yes", "no", "pay", "reciprocal")

# Reply classes that must be refused, not negotiated (R-510: white hat, zero paid).
REFUSED_REPLY_CLASSES = ("pay", "reciprocal")


def event_id(
    center_domain: str,
    target_domain: str,
    channel: str,
    action: str,
    at: str,
) -> str:
    material = "|".join((center_domain, target_domain, channel, action, at))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def append_event(path: Union[str, Path], event: Dict[str, Any]) -> Tuple[Path, bool]:
    """Append one event. Returns (path, appended). A duplicate is not appended."""
    target = Path(path)
    existing = {row.get("event_id") for row in _read_jsonl(target)}
    if event.get("event_id") in existing:
        return target, False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        raise WriteError("Cannot append event", path=str(target), reason=exc.strerror) from exc
    return target, True


def load_events(sources: Sequence[Union[str, Path]]) -> List[Dict[str, Any]]:
    """Read every *.jsonl / *.json event source, ignoring anything missing."""
    rows: List[Dict[str, Any]] = []
    for raw in sources or []:
        candidate = Path(raw)
        if candidate.is_dir():
            paths: Iterable[Path] = sorted(candidate.rglob("*.jsonl")) + sorted(candidate.rglob("*.json"))
        elif candidate.is_file():
            paths = [candidate]
        else:
            continue
        for path in paths:
            if path.suffix == ".json":
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                    rows.extend(row for row in payload["rows"] if isinstance(row, dict))
                elif isinstance(payload, list):
                    rows.extend(row for row in payload if isinstance(row, dict))
                continue
            rows.extend(_read_jsonl(path))
    return rows


def normalise_legacy(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map `send-history` rows (event: send/submit, sent_at) onto delivery events.

    Legacy history predates the event log. It carries enough to seed state, but
    it has no evidence and no provider id, so those events stay flagged.
    """
    mapped: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("kind") == EVENT_KIND:
            mapped.append(row)
            continue
        legacy = str(row.get("event") or "").strip().lower()
        if legacy not in {"send", "submit"}:
            continue
        at = str(row.get("sent_at") or row.get("at") or "")
        if not at:
            continue
        center = str(row.get("center_domain") or "")
        target = str(row.get("target_domain") or "")
        channel = "form" if legacy == "submit" else "email"
        mapped.append({
            "kind": EVENT_KIND,
            "event_id": event_id(center, target, channel, "submitted" if channel == "form" else "sent", at),
            "at": at,
            "center_domain": center,
            "target_domain": target,
            "channel": channel,
            "action": "submitted" if channel == "form" else "sent",
            "address": row.get("address"),
            "provider_id": row.get("provider_id"),
            "evidence": row.get("evidence") or "legacy-history",
            "legacy": True,
        })
    return mapped


def latest_per_key(events: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """The most recent event for each (centre, target, channel). Ordering is by ISO timestamp."""
    latest: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for event in events:
        key = (
            str(event.get("center_domain") or ""),
            str(event.get("target_domain") or ""),
            str(event.get("channel") or ""),
        )
        current = latest.get(key)
        if current is None or str(event.get("at") or "") >= str(current.get("at") or ""):
            latest[key] = event
    return latest


def optional(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None
