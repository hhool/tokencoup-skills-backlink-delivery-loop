"""Pure cadence arithmetic. Counts are keyed by target domain, never by page URL."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sent_events(history: List[dict]) -> List[Tuple[str, datetime]]:
    events: List[Tuple[str, datetime]] = []
    for row in history:
        if row.get("event") not in {"send", "submit"}:
            continue
        moment = parse_ts(str(row.get("sent_at", "")))
        if moment is None:
            continue
        from ..models import normalize_domain
        events.append((normalize_domain(str(row.get("target_domain", ""))), moment))
    return events


def window_open(domain: str, history: List[dict], now: datetime, days: int = 90) -> Tuple[bool, str]:
    """A second pitch to the same target domain inside the repeat window is refused."""
    from ..models import normalize_domain
    target = normalize_domain(domain)
    for event_domain, moment in _sent_events(history):
        if event_domain != target:
            continue
        if timedelta(0) <= now - moment < timedelta(days=days):
            return False, "repeat window: %s" % (moment.date().isoformat())
    return True, ""


def used_in_window(domain: str, history: List[dict], now: datetime, hours: int = 24) -> int:
    from ..models import normalize_domain
    target = normalize_domain(domain)
    return sum(
        1
        for event_domain, moment in _sent_events(history)
        if event_domain == target and timedelta(0) <= now - moment < timedelta(hours=hours)
    )


def quota(history: List[dict], now: datetime, ceiling: int = 100) -> Dict[str, int]:
    """Instance override (R-530): a ceiling per target domain per 24 hours."""
    counts: Dict[str, int] = {}
    for domain, moment in _sent_events(history):
        if timedelta(0) <= now - moment < timedelta(hours=24):
            counts[domain] = counts.get(domain, 0) + 1
    return {"ceiling": ceiling, "per_domain": counts}
