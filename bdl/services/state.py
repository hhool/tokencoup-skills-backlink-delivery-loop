"""State projection: fold an event log into one current state per channel.

This is the module that answers "is this domain in delivery, delivered, or
live?". A manifest is a photograph of a moment; state is what the photograph
cannot show. Everything here is a pure function of (events, observations, now) -
no I/O, no network, no mutation - so it is fully unit-testable and replayable.

Keys are (center_domain, target_domain, channel). A domain is not "either a form
or an email": `channels` in the manifest can carry both, and each is tracked
separately for cadence, cooldown and evidence.

Two timers are derived, never stored:

  * receipt window - how long an email or a form submission may stay unanswered
    before it is closed as unanswered. Default 30 days (BDL_RECEIPT_WINDOW_DAYS).
  * contact cooldown - how long a bounced address stays lost. Default 180 days
    (BDL_CONTACT_LOST_DAYS). The cooldown is attached to the ADDRESS, not the
    domain: one dead mailbox does not close the other channel.

"Live" is the only success state and it comes from an observation, never from a
send. A send only proves we transmitted (L1); a live link proves the outcome (L4).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .cadence import parse_ts

EVENT_KIND = "delivery_event"

# Once in one of these, only a live observation can change the verdict.
ABSORBING = {"declined", "declined_paid", "declined_reciprocal", "closed"}

REPLY_STATE = {
    "yes": "replied",
    "no": "declined",
    "pay": "declined_paid",
    "reciprocal": "declined_reciprocal",
}

NEXT_ACTION = {
    "not_started": "enrich: no submission URL or contact address recorded yet",
    "queued": "human: review the draft, transmit, then record the result",
    "sent": "poll provider events for delivered / bounced",
    "delivered": "wait for a reply until the window closes",
    "submitted": "observe the target page for a live link (L4)",
    "replied": "agreed: observe the page, then confirm the link in link-ledger",
    "declined": "closed: do not retry this domain",
    "declined_paid": "closed: paid placement refused (R-510), do not retry",
    "declined_reciprocal": "closed: reciprocal link refused (R-510), do not retry",
    "bounced": "try another address or another channel for this domain",
    "contact_lost": "cooldown: this address is lost until the cooldown ends",
    "unanswered": "no reply inside the window; close or re-approach after cooldown",
    "unanswered_no_receipt": "no provider receipt: treat as unknown, do not resend",
    "live": "confirm in link-ledger (human write, R-512)",
    "closed": "closed by a human",
}


def _key(event: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(event.get("center_domain") or ""),
        str(event.get("target_domain") or ""),
        str(event.get("channel") or ""),
    )


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fold(events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply events in timestamp order. Absorbing states ignore later noise."""
    ordered = sorted(events, key=lambda item: str(item.get("at") or ""))
    state = "not_started"
    since: Optional[str] = None
    address: Optional[str] = None
    provider_id: Optional[str] = None
    evidence: List[str] = []
    notes: List[str] = []
    last_sent_at: Optional[str] = None
    last_submitted_at: Optional[str] = None
    bounced_at: Optional[str] = None
    closed_reason: Optional[str] = None

    for event in ordered:
        action = str(event.get("action") or "")
        at = str(event.get("at") or "") or None
        if event.get("address"):
            address = str(event["address"])
        if event.get("provider_id"):
            provider_id = str(event["provider_id"])
        if event.get("evidence"):
            evidence.append(str(event["evidence"]))
        if event.get("note"):
            notes.append(str(event["note"]))

        if state in ABSORBING:
            continue

        if action == "queued":
            state, since = "queued", at
        elif action == "sent":
            state, since, last_sent_at = "sent", at, at
            if not event.get("provider_id"):
                notes.append("sent without a provider id: receipt is not traceable")
        elif action == "submitted":
            state, since, last_submitted_at = "submitted", at, at
            if not event.get("evidence"):
                notes.append("submitted without evidence: treated as unverified")
        elif action == "delivered":
            state, since = "delivered", at
        elif action == "bounced":
            state, since, bounced_at = "bounced", at, at
        elif action == "reply":
            reply_class = str(event.get("reply_class") or "")
            state = REPLY_STATE.get(reply_class, "replied")
            since = at
            if event.get("compliance") == "refused":
                notes.append("operator refused the reply class (R-510)")
        elif action == "closed":
            state, since = "closed", at
            closed_reason = str(event.get("note") or "closed by a human")

    last_event_at = str(ordered[-1].get("at") or "") if ordered else ""

    return {
        "state": state,
        "state_since": since,
        "address": address,
        "provider_id": provider_id,
        "evidence": evidence,
        "notes": notes,
        "last_sent_at": last_sent_at,
        "last_submitted_at": last_submitted_at,
        "bounced_at": bounced_at,
        "closed_reason": closed_reason,
        "last_event_at": last_event_at or None,
    }


def _apply_timers(
    folded: Dict[str, Any],
    channel: str,
    now: datetime,
    receipt_window_days: int,
    contact_lost_days: int,
) -> Dict[str, Any]:
    state = folded["state"]
    notes = list(folded["notes"])
    window = timedelta(days=receipt_window_days)
    anchor: Optional[str] = folded["last_sent_at"] or folded["last_submitted_at"]

    due_at: Optional[str] = None
    expired = False
    anchor_ts = parse_ts(anchor or "")
    if anchor_ts is not None:
        due = anchor_ts + window
        due_at = _iso(due)
        expired = now >= due

    if state in ABSORBING:
        pass
    elif folded["bounced_at"]:
        bounce_ts = parse_ts(folded["bounced_at"])
        if bounce_ts is not None and now < bounce_ts + timedelta(days=contact_lost_days):
            state = "contact_lost"
            notes.append(
                "address lost until %s (%d day cooldown)"
                % (_iso(bounce_ts + timedelta(days=contact_lost_days)), contact_lost_days)
            )
        elif bounce_ts is not None:
            state = "unanswered"
            notes.append("bounce cooldown elapsed; the address may be retried")
    elif expired and state == "sent":
        state = "unanswered_no_receipt"
        notes.append("no provider receipt inside the window: unknown, not failed (R-512)")
    elif expired and state == "delivered":
        state = "unanswered"
        notes.append("delivered but silent inside the window: unanswered, not failed")
    elif expired and state == "submitted":
        notes.append("observation window elapsed; absence is not failure (R-512 / R-513)")
    elif expired and state == "queued":
        notes.append("queued past one window: the draft went stale, re-review it")

    if channel == "form" and state == "submitted" and not folded["evidence"]:
        notes.append("unverified: a submission without evidence is not a delivery")

    return {
        **folded,
        "state": state,
        "notes": notes,
        "window": {
            "days": receipt_window_days,
            "anchor": anchor,
            "due_at": due_at,
            "expired": expired,
        },
    }


def project(
    events: Sequence[Dict[str, Any]],
    observations: Sequence[Dict[str, Any]] = (),
    now: Optional[datetime] = None,
    receipt_window_days: int = 30,
    contact_lost_days: int = 180,
    center_domain: str = "",
) -> Dict[str, Any]:
    """Fold events into one row per (centre, target, channel), then overlay observations."""
    moment = now or datetime.now(timezone.utc)

    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for event in events:
        if str(event.get("kind") or EVENT_KIND) != EVENT_KIND:
            continue
        if center_domain and str(event.get("center_domain") or "") not in {"", center_domain}:
            continue
        grouped.setdefault(_key(event), []).append(event)

    live: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for observation in observations:
        if str(observation.get("outcome") or "") != "found":
            continue
        key = (
            str(observation.get("center_domain") or ""),
            str(observation.get("target_domain") or ""),
        )
        live[key] = observation

    rows: List[Dict[str, Any]] = []
    for (center, target, channel), group in sorted(grouped.items()):
        folded = _fold(group)
        folded = _apply_timers(folded, channel, moment, receipt_window_days, contact_lost_days)
        observation = live.get((center, target))
        state = folded["state"]
        notes = folded["notes"]
        if observation is not None and state != "live":
            state = "live"
            notes = notes + [
                "live link observed at %s" % str(observation.get("observed_at") or "unknown time")
            ]

        rows.append({
            "center_domain": center,
            "target_domain": target,
            "channel": channel,
            "state": state,
            "state_since": folded["state_since"],
            "last_event_at": folded["last_event_at"],
            "address": folded["address"],
            "provider_id": folded["provider_id"],
            "evidence_count": len(folded["evidence"]),
            "window": folded["window"],
            "next_action": NEXT_ACTION.get(state, "review manually"),
            "notes": notes,
            "live": observation is not None,
        })

    # A live link can exist with no recorded event at all (someone placed it by
    # hand). It must still count as success, so it gets its own row.
    covered = {(row["center_domain"], row["target_domain"]) for row in rows}
    for (center, target), observation in sorted(live.items()):
        if (center, target) in covered:
            continue
        if center_domain and center not in {"", center_domain}:
            continue
        rows.append({
            "center_domain": center,
            "target_domain": target,
            "channel": "unknown",
            "state": "live",
            "state_since": observation.get("observed_at"),
            "last_event_at": None,
            "address": None,
            "provider_id": None,
            "evidence_count": 0,
            "window": {"days": receipt_window_days, "anchor": None, "due_at": None, "expired": False},
            "next_action": NEXT_ACTION["live"],
            "notes": ["live link with no recorded delivery event"],
            "live": True,
        })

    counts = Counter(row["state"] for row in rows)
    in_delivery = {"queued", "sent", "delivered", "submitted", "replied"}
    succeeded = {"live"}
    failed = {"bounced", "contact_lost", "declined", "declined_paid", "declined_reciprocal", "unanswered"}

    return {
        "center_domain": center_domain or (rows[0]["center_domain"] if rows else ""),
        "generated_at": _iso(moment),
        "windows": {"receipt_days": receipt_window_days, "contact_lost_days": contact_lost_days},
        "summary": {
            "channels": len(rows),
            "by_state": dict(sorted(counts.items())),
            "in_delivery": sum(1 for row in rows if row["state"] in in_delivery),
            "succeeded": sum(1 for row in rows if row["state"] in succeeded),
            "failed": sum(1 for row in rows if row["state"] in failed),
            "untouched": sum(1 for row in rows if row["state"] == "not_started"),
        },
        "rows": rows,
    }


def seed_rows_from_manifest(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a manifest into not_started state rows, so untouched targets are visible."""
    seeded: List[Dict[str, Any]] = []
    for row in manifest.get("rows", []):
        # A blocked row is not a target: seeding it as `not_started` would inflate
        # the "untouched" count with rows that were deliberately excluded.
        if row.get("status") == "blocked":
            continue
        # A row with no known channel is still a target: it belongs in the report
        # as `not_started` with an "enrich" next action, not missing entirely.
        channels = row.get("channels") or ["unknown"]
        for channel in channels:
            seeded.append({
                "kind": EVENT_KIND,
                "event_id": "",
                "at": "",
                "center_domain": row.get("center_domain") or manifest.get("center_domain") or "",
                "target_domain": row.get("target_domain") or "",
                "channel": channel,
                "action": "seed",
            })
    return seeded


def merge_seeds(events: Iterable[Dict[str, Any]], seeds: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Seeds only contribute keys that have no real event yet."""
    combined = list(events)
    present = {_key(event) for event in combined}
    for seed in seeds:
        if _key(seed) not in present:
            # A seed with no events folds to not_started; drop the marker event so
            # the state is genuinely "no events yet" rather than a fake action.
            combined.append({**seed, "action": "seed"})
    return combined
