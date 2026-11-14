"""Observation window for the submitted -> live transition.

A short window can never prove failure. Missing links inside the window keep the
row in submitted; only a human may promote it to live (R-512).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

OFFSETS_DAYS = (1, 3, 7, 14, 30)
MAX_CHECKS = 6
UNKNOWN_HTTP = {403, 408, 425, 429, 451, 500, 502, 503, 504}


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plan(submitted_at: str, max_checks: int = MAX_CHECKS) -> List[str]:
    from .cadence import parse_ts
    start = parse_ts(submitted_at)
    if start is None:
        raise ValueError("submitted_at must be an ISO-8601 timestamp")
    offsets = OFFSETS_DAYS[: min(max_checks, len(OFFSETS_DAYS), MAX_CHECKS)]
    return [_iso(start + timedelta(days=day)) for day in offsets]


def next_check_due(schedule: List[str], now: datetime) -> Optional[str]:
    from .cadence import parse_ts
    for entry in schedule:
        moment = parse_ts(entry)
        if moment and moment > now:
            return entry
    return None


def classify(outcome: str, http_status: int | None) -> str:
    """403 / timeout / robots / JS-only injection are unknown, never absent."""
    if http_status in UNKNOWN_HTTP:
        return "unknown"
    if outcome == "found" and http_status == 200:
        return "found"
    if outcome == "absent" and http_status == 200:
        return "absent"
    return "unknown"


def terminal_state(observations: List[dict]) -> str:
    """The machine never declares failure from a short window."""
    if any(item.get("outcome") == "found" for item in observations):
        return "pending_human_confirmation"
    return "awaiting_review"
