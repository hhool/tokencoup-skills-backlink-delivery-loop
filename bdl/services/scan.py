"""Auto-scan: fetch each target page and decide whether the backlink is live.

This is the "observing" half of the loop. It is strictly read-only: a single GET
per target, no authentication, no cookies, no transmission. The network call is
isolated behind an injectable ``Fetcher`` so the whole scan is unit-tested
offline against synthetic pages.

For every manifest row that has a resolvable ``target_url`` and ``own_url`` the
scanner runs one L4 observation, records it to the local observation ledger, and
classifies the outcome. With ``auto_confirm`` enabled, a ``found`` observation
is marked ``live`` locally and a confirm proposal is queued - but the shared
link-ledger is never written here (R-512 human gate stays in force).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from ..adapters.observation import append_observation, append_proposal, now_iso
from ..errors import DeliveryError
from ..models import ManifestRow
from .observe import Fetcher, observe_target

# Rows in these states carry a delivery channel and are worth observing.
OBSERVABLE_STATUSES = {"ready_for_review", "needs_enrichment"}


def _is_observable(row: ManifestRow) -> bool:
    return (
        row.status in OBSERVABLE_STATUSES
        and bool(row.target_url)
        and bool(row.own_url)
    )


def scan_rows(
    rows: List[ManifestRow],
    fetcher: Fetcher,
    auto_confirm: bool,
    center_domain: str,
    observation_path: str,
    proposal_path: str,
) -> Dict[str, object]:
    counts: Dict[str, int] = {"scanned": 0, "found": 0, "absent": 0, "unknown": 0, "confirmed": 0}
    proposals: List[Dict[str, object]] = []

    for row in rows:
        if not _is_observable(row):
            continue
        record = observe_target(fetcher, row.target_url, row.own_url)
        counts["scanned"] += 1

        outcome = record.get("outcome")
        if outcome == "found":
            counts["found"] += 1
        elif outcome == "absent":
            counts["absent"] += 1
        else:
            counts["unknown"] += 1

        state = "live" if (auto_confirm and outcome == "found") else "pending_human_confirmation"
        if state == "live":
            counts["confirmed"] += 1

        observation = {
            "center_domain": center_domain,
            "target_domain": row.target_domain,
            "channel_type": row.channel_type,
            "prospect_id": row.prospect_id,
            "idempotency_key": row.idempotency_key,
            "target_url": record.get("target_url"),
            "own_url": record.get("own_url"),
            "http_status": record.get("http_status"),
            "outcome": outcome,
            "state": state,
            "found_href": record.get("found_href"),
            "rel": record.get("rel"),
            "anchor_text": record.get("anchor_text"),
            "evidence": record.get("evidence"),
            "auto_confirm": auto_confirm,
            "observed_at": now_iso(),
        }
        append_observation(observation_path, observation)

        if state == "live":
            proposal = {
                "center_domain": center_domain,
                "target_domain": row.target_domain,
                "prospect_id": row.prospect_id,
                "idempotency_key": row.idempotency_key,
                "target_url": record.get("target_url"),
                "found_href": record.get("found_href"),
                "anchor_text": record.get("anchor_text"),
                "proposed_state": "live",
                "proposed_at": now_iso(),
                "basis": "auto-confirmed L4 observation; pending link-ledger promotion",
            }
            append_proposal(proposal_path, proposal)
            proposals.append(proposal)

    return {
        "scanned": counts["scanned"],
        "found": counts["found"],
        "absent": counts["absent"],
        "unknown": counts["unknown"],
        "confirmed": counts["confirmed"],
        "auto_confirm": auto_confirm,
        "observation_ledger": observation_path,
        "proposal_queue": proposal_path,
        "proposals": proposals,
    }
