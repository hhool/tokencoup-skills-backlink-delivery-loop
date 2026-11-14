"""Transport receipts (L2): poll the sending provider and record what happened.

`record --action sent` proves we handed a message to a provider and can name the
message. It proves nothing about arrival. This service closes that gap without
touching the target site:

    sent  --(provider events)-->  delivered  |  bounced  |  still unknown

The poll is bounded on purpose. Only sends that are inside the receipt window and
still unresolved are queried, so a long-forgotten address is not hammered once a
day. A resolution is written once per (provider id, action) - the provider's own
timestamps are not precise enough to be the idempotency key, and a duplicate
``delivered`` event would reset the receipt window anchor.

What this service may and may not conclude:

  * ``delivered`` and ``bounced`` are facts the provider reports, so they become
    events. Only ``bounced`` has a consequence beyond the row: the address enters
    the contact-lost cooldown (state projection, ``BDL_CONTACT_LOST_DAYS``).
  * ``live`` is never in scope. A delivered mail with a live link is still two
    separate proofs, and only the L4 observer can supply the second.
  * A read failure writes nothing. Unknown is not a bounce (R-512 logic applied
    to transport instead of to page fetches).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ..adapters.events import EVENT_KIND, append_event
from ..adapters.observation import append_observation, append_proposal
from ..adapters.provider_events import RECORDABLE, classify_status, read_status
from ..errors import DeliveryError
from .cadence import parse_ts

RESOLVING = ("delivered", "bounced")


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _action(event: Dict[str, Any]) -> str:
    return str(event.get("action") or "")


def resolved_ids(events: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Provider ids that already carry a resolving receipt, keyed by (id, action)."""
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for event in events:
        if str(event.get("kind") or EVENT_KIND) != EVENT_KIND:
            continue
        provider_id = str(event.get("provider_id") or "")
        action = _action(event)
        if provider_id and action in RESOLVING:
            seen[(provider_id, action)] = event
    return seen


def pending_sends(
    events: Sequence[Dict[str, Any]],
    now: datetime,
    receipt_window_days: int = 30,
    limit: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Sends worth asking about: sent, named, unresolved, inside the window.

    Returns (polls, already_resolved). A send with no provider id is not pollable
    and is left alone - it is already flagged in the state notes.
    """
    resolved = resolved_ids(events)
    window = timedelta(days=receipt_window_days)
    polls: List[Dict[str, Any]] = []
    seen_ids: set = set()
    already = 0

    ordered = sorted(events, key=lambda item: str(item.get("at") or ""), reverse=True)
    for event in ordered:
        if str(event.get("kind") or EVENT_KIND) != EVENT_KIND:
            continue
        if _action(event) != "sent":
            continue
        provider_id = str(event.get("provider_id") or "")
        if not provider_id:
            continue
        if any((provider_id, action) in resolved for action in RESOLVING):
            already += 1
            continue
        if provider_id in seen_ids:
            continue
        sent_at = parse_ts(str(event.get("at") or ""))
        if sent_at is None or (now - sent_at) > window:
            continue
        seen_ids.add(provider_id)
        polls.append({
            "center_domain": str(event.get("center_domain") or ""),
            "target_domain": str(event.get("target_domain") or ""),
            "channel": str(event.get("channel") or "email"),
            "address": str(event.get("address") or ""),
            "provider_id": provider_id,
            "sent_at": str(event.get("at") or ""),
        })
        if limit and len(polls) >= limit:
            break

    polls.sort(key=lambda item: item["sent_at"])
    return polls, already


def poll(
    polls: Sequence[Dict[str, Any]],
    reader,
    provider: str,
    config: Dict[str, str],
    now: datetime,
) -> List[Dict[str, Any]]:
    """One GET per pollable send. Records carry the row identity back with them."""
    observed_at = _iso(now)
    records: List[Dict[str, Any]] = []
    for item in polls:
        record = read_status(reader, provider, config, item["provider_id"], observed_at)
        records.append({**item, **record})
    return records


def plan_events(
    records: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    now: datetime,
    actor: str = "provider-poll",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Turn resolving receipts into events. Duplicates and unusable rows are reported."""
    from .record import build_event

    resolved = resolved_ids(events)
    moment = _iso(now)
    fresh: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    planned: set = set()

    for record in records:
        action = str(record.get("action") or "")
        if action not in RECORDABLE:
            continue
        provider_id = str(record.get("provider_id") or "")
        key = (provider_id, action)
        if key in resolved or key in planned:
            skipped.append({"provider_id": provider_id, "action": action, "reason": "already recorded"})
            continue
        if not record.get("address"):
            skipped.append({
                "provider_id": provider_id,
                "action": action,
                "reason": "no address on the send, cannot scope the receipt",
            })
            continue
        note = str(record.get("note") or "")
        try:
            event = build_event(
                center_domain=str(record.get("center_domain") or ""),
                target_domain=str(record.get("target_domain") or ""),
                channel=str(record.get("channel") or "email"),
                action=action,
                at=moment,
                address=str(record.get("address") or ""),
                provider_id=provider_id,
                note=note or "provider status: %s" % (record.get("raw_status") or "unknown"),
                actor=actor,
            )
        except DeliveryError as exc:
            skipped.append({"provider_id": provider_id, "action": action, "reason": exc.message})
            continue
        fresh.append(event)
        planned.add(key)

    return fresh, skipped


def poll_receipts(
    events: Sequence[Dict[str, Any]],
    reader,
    provider: str,
    config: Dict[str, str],
    now: Optional[datetime] = None,
    events_dir: Union[str, Path] = "",
    receipt_path: Union[str, Path] = "",
    proposal_path: Union[str, Path] = "",
    auto_confirm: bool = False,
    dry_run: bool = False,
    receipt_window_days: int = 30,
    limit: int = 0,
    actor: str = "provider-poll",
) -> Dict[str, Any]:
    """Plan the polls, fetch them, then record or queue the receipts."""
    moment = now or datetime.now(timezone.utc)
    polls, already = pending_sends(events, moment, receipt_window_days, limit)
    return execute_polls(
        events=events,
        polls=polls,
        already_resolved=already,
        reader=reader,
        provider=provider,
        config=config,
        now=moment,
        events_dir=events_dir,
        receipt_path=receipt_path,
        proposal_path=proposal_path,
        auto_confirm=auto_confirm,
        dry_run=dry_run,
        actor=actor,
    )


def execute_polls(
    events: Sequence[Dict[str, Any]],
    polls: Sequence[Dict[str, Any]],
    already_resolved: int,
    reader,
    provider: str,
    config: Dict[str, str],
    now: Optional[datetime] = None,
    events_dir: Union[str, Path] = "",
    receipt_path: Union[str, Path] = "",
    proposal_path: Union[str, Path] = "",
    auto_confirm: bool = False,
    dry_run: bool = False,
    actor: str = "provider-poll",
) -> Dict[str, Any]:
    """The half that needs a reader. Split out so an empty ledger needs no credentials."""
    moment = now or datetime.now(timezone.utc)
    records = poll(polls, reader, provider, config, moment)
    fresh, skipped = plan_events(records, events, moment, actor)

    written: List[str] = []
    if not dry_run and receipt_path:
        for record in records:
            append_observation(receipt_path, {
                "kind": "provider_receipt",
                "center_domain": record.get("center_domain"),
                "target_domain": record.get("target_domain"),
                "channel": record.get("channel"),
                "address": record.get("address"),
                "provider": record.get("provider"),
                "provider_id": record.get("provider_id"),
                "sent_at": record.get("sent_at"),
                "observed_at": record.get("observed_at"),
                "http_status": record.get("http_status"),
                "raw_status": record.get("raw_status"),
                "status": record.get("status"),
                "action": record.get("action"),
                "note": record.get("note"),
            })

    if not dry_run:
        if auto_confirm:
            for event in fresh:
                path = Path(events_dir) / ("events-%s.jsonl" % (event["center_domain"] or "unknown"))
                _, appended = append_event(path, event)
                if appended:
                    written.append(str(path))
        elif proposal_path:
            for event in fresh:
                append_proposal(proposal_path, {
                    "kind": "receipt_proposal",
                    "center_domain": event["center_domain"],
                    "target_domain": event["target_domain"],
                    "channel": event["channel"],
                    "action": event["action"],
                    "address": event.get("address"),
                    "provider_id": event.get("provider_id"),
                    "proposed_at": _iso(moment),
                    "basis": "provider reported %s; record it unless you know better"
                    % event["action"],
                })

    counts: Dict[str, int] = {"delivered": 0, "bounced": 0, "pending": 0, "sent": 0, "unknown": 0}
    for record in records:
        status = str(record.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1

    return {
        "polls": len(polls),
        "already_resolved": already_resolved,
        "by_status": counts,
        "events_planned": len(fresh),
        "events_written": len(written),
        "proposals": 0 if auto_confirm or dry_run else len(fresh),
        "skipped": skipped,
        "auto_confirm": auto_confirm,
        "dry_run": dry_run,
        "records": records,
        "receipt_ledger": str(receipt_path) if receipt_path and not dry_run else "",
        "proposal_queue": str(proposal_path) if (proposal_path and not auto_confirm and not dry_run) else "",
    }


def classify_one(provider: str, raw_status: str) -> Dict[str, str]:
    """Small helper for the CLI and for operators checking a mapping by hand."""
    status, note = classify_status(provider, raw_status)
    return {"raw_status": raw_status, "status": status, "note": note}
