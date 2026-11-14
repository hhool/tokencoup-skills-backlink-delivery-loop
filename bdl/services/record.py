"""Build and validate one delivery event.

The rules enforced here are the ones that cannot be recovered later:

  * A "sent" event must carry a provider id. Without it there is no way to poll a
    receipt, so the send is unverifiable and must not be recorded as sent.
  * A "submitted" event must carry evidence. A form submission with no thank-you
    page, status code or screenshot is not proof of delivery.
  * A reply must be classified. "pay" (paid placement) and "reciprocal" (link
    exchange) are refused under R-510: they are recorded so the domain is not
    approached again, and flagged `compliance: refused` so nobody negotiates one.

Nothing here transmits. It turns a human action into a durable, replayable record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..adapters.events import (
    ACTIONS, EVENT_KIND, REFUSED_REPLY_CLASSES, REPLY_CLASSES, event_id,
    optional,
)
from ..errors import DeliveryError
from ..models import DELIVERY_CHANNELS, normalize_domain


def _iso(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryError(
            "Timestamp must be ISO-8601", value=text, hint="e.g. 2026-11-15T09:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_event(
    center_domain: str,
    target_domain: str,
    channel: str,
    action: str,
    at: str = "",
    address: str = "",
    provider_id: str = "",
    evidence: str = "",
    reply_class: str = "",
    note: str = "",
    actor: str = "",
) -> Dict[str, Any]:
    center = normalize_domain(center_domain)
    target = normalize_domain(target_domain)
    if not center:
        raise DeliveryError("A centre domain is required", hint="--center-domain or BDL_CENTER_DOMAIN")
    if not target:
        raise DeliveryError("A target domain is required", hint="--target-domain")
    if channel not in DELIVERY_CHANNELS:
        raise DeliveryError(
            "Unknown delivery channel", value=channel, allowed=list(DELIVERY_CHANNELS)
        )
    if action not in ACTIONS:
        raise DeliveryError("Unknown action", value=action, allowed=list(ACTIONS))

    moment = _iso(at)
    address_value = optional(address)
    provider_value = optional(provider_id)
    evidence_value = optional(evidence)

    if channel == "email" and action in {"sent", "bounced", "delivered", "reply"} and not address_value:
        raise DeliveryError(
            "Email events must record the address",
            hint="--address, so a bounce can be scoped to one mailbox",
            action=action,
        )
    if action == "sent" and not provider_value:
        raise DeliveryError(
            "A send without a provider id cannot be reconciled",
            hint="--provider-id from the sending provider",
            rule="L1",
        )
    if action == "submitted" and not evidence_value:
        raise DeliveryError(
            "A submission without evidence is not a delivery",
            hint="--evidence <screenshot path or thank-you URL>",
            rule="L1",
        )

    compliance = ""
    if action == "reply":
        if reply_class not in REPLY_CLASSES:
            raise DeliveryError(
                "A reply must be classified",
                allowed=list(REPLY_CLASSES),
                hint="yes | no | pay | reciprocal",
            )
        if reply_class in REFUSED_REPLY_CLASSES:
            compliance = "refused"

    event: Dict[str, Any] = {
        "kind": EVENT_KIND,
        "event_id": event_id(center, target, channel, action, moment),
        "at": moment,
        "center_domain": center,
        "target_domain": target,
        "channel": channel,
        "action": action,
        "actor": optional(actor) or "",
    }
    for name, value in (
        ("address", address_value),
        ("provider_id", provider_value),
        ("evidence", evidence_value),
        ("reply_class", optional(reply_class)),
        ("note", optional(note)),
        ("compliance", optional(compliance)),
    ):
        if value:
            event[name] = value
    return event


def policy_notice(event: Dict[str, Any]) -> Dict[str, Any]:
    """Describe a refusal without blocking the record.

    A "we only link if you pay" reply is a fact worth keeping: it is the reason
    this domain must not be approached again. What is refused is complying with
    it, not recording it. So the event is written and flagged.
    """
    if event.get("compliance") != "refused":
        return {}
    return {
        "refused": True,
        "rule": "R-510",
        "reply_class": event.get("reply_class"),
        "message": (
            "Paid placement and link exchange are out of scope. The reply is "
            "recorded so this domain is closed, not negotiated."
        ),
    }
