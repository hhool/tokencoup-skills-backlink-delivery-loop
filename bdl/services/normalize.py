"""Admission, field mapping and manifest row construction.

The upstream `url` is a site entry point, NOT link-ledger.target_url. Using it as
the landing page would make every L4 fetch look at the wrong page.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .. import CONTRACTS_VERSION
from ..errors import PolicyError
from ..models import (
    ManifestRow, ProseContact, Prospect, Snapshot, absolute_url, delivery_channels,
    domain_from_text, normalize_domain,
)
from . import cadence

TYPE_MAP = {
    "industry_media": "asset",
    "guest": "guest_post",
    "resource": "asset",
    "directory": "directory",
    "community": "community",
    "social": "community",
    "partner": "other",
    "authority": "asset",
    "digital_pr": "journalist",
}
EXCLUDED_TYPES = {"reciprocal"}
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def assert_outbound_copy(subject: str, body: str) -> None:
    """R-522: no CJK in a subject or body, and no unreplaced placeholders."""
    for name, text in (("subject", subject), ("body", body)):
        if CJK_RE.search(text or ""):
            raise PolicyError("CJK is prohibited in outbound copy", field=name, rule="R-522")
        if "{{" in (text or ""):
            raise PolicyError("Unreplaced placeholder in outbound copy", field=name, rule="R-522")


def _idempotency_key(center: str, domain: str, channel: str, target_url: str) -> str:
    material = "|".join((center, domain, channel, target_url or "", CONTRACTS_VERSION))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _best_contact(group: List[ProseContact]) -> Tuple[Optional[ProseContact], str]:
    candidates = [item for item in group if item.state == "candidate"]
    if candidates:
        with_address = [item for item in candidates if item.address or item.form_url]
        return (with_address[0] if with_address else candidates[0]), "candidate"
    if any(item.state == "excluded" for item in group):
        return None, "excluded"
    if any(item.state == "ambiguous" for item in group):
        return None, "ambiguous"
    return None, "missing"


def index_contacts(contacts: List[ProseContact]) -> Dict[str, List[ProseContact]]:
    indexed: Dict[str, List[ProseContact]] = {}
    for contact in contacts:
        key = normalize_domain(contact.target_domain)
        if key:
            indexed.setdefault(key, []).append(contact)
    return indexed


def build_rows(
    snapshot: Snapshot,
    contacts: List[ProseContact],
    own_site_base: str,
    live_domains: set,
    history: List[dict],
    now: datetime,
    deferral_days: int = 90,
    templates=None,
    own_landing_url: str = "",
) -> List[ManifestRow]:
    indexed = index_contacts(contacts)
    rows: List[ManifestRow] = []

    for prospect in snapshot.prospects:
        domain = prospect.key
        reasons: List[str] = []
        gaps: List[str] = []
        status = "ready_for_review"

        if prospect.is_competitor:
            reasons.append("competitor domain is out of scope")
            status = "blocked"
        if prospect.acquired:
            reasons.append("already acquired upstream")
            status = "blocked"
        if not prospect.is_gap:
            # The upstream may know things this tool cannot reproduce: that a URL
            # is a documentation or support page rather than a placement surface,
            # or that a domain is a competitor under a name the flags miss.
            # `is_gap=false` is that judgement, and it must not be dropped.
            reasons.append("upstream marked this domain as not an actionable gap")
            status = "blocked"
        if prospect.type_key in EXCLUDED_TYPES:
            reasons.append("type is excluded by white-hat policy")
            status = "blocked"
        if prospect.http_status is not None and not 200 <= prospect.http_status < 300:
            reasons.append("upstream reachability check failed")
            status = "blocked"
        if domain in live_domains:
            reasons.append("link-ledger already has a live link for this domain")
            status = "blocked"

        opened, window_reason = cadence.window_open(domain, history, now, days=deferral_days)
        if not opened and status != "blocked":
            reasons.append(window_reason)
            status = "deferred"

        contact, contact_state = _best_contact(indexed.get(domain, []))
        if contact_state == "excluded" and status != "blocked":
            reasons.append("contact address is explicitly excluded in source prose")
            status = "blocked"

        target_url: Optional[str] = None
        submission_url: Optional[str] = None
        own_url: Optional[str] = None
        contact_email: Optional[str] = None
        contact_source_url: Optional[str] = None
        channel_source = "prose"
        own_url_source = "prose"

        # An observed channel beats a prose-derived one: discovery read the site
        # just now, prose read an operator note some time ago. Both are evidence,
        # but only one of them is current.
        if prospect.submission_url:
            submission_url = prospect.submission_url
            channel_source = "discovery"
        if prospect.contact_email:
            contact_email = prospect.contact_email
            contact_source_url = prospect.contact_source_url
            channel_source = "discovery"

        if contact:
            if contact.target_url_hint and domain_from_text(contact.target_url_hint) == domain:
                target_url = contact.target_url_hint
            if not submission_url and contact.form_url:
                submission_url = absolute_url(contact.form_url, "https://" + domain)
            if contact.own_url_hint:
                own_url = absolute_url(contact.own_url_hint, own_site_base)
                own_url_source = "prose"
            if not contact_email:
                contact_email = contact.address

        # One configured landing page fills own_url for every row. Without it the
        # whole pool stalls on a value that is identical for all of them.
        if not own_url and own_landing_url:
            own_url = own_landing_url
            own_url_source = "config"

        if channel_source == "discovery":
            contact_state = "discovered"

        has_channel = bool(submission_url or contact_email)

        if not target_url:
            if prospect.discovered:
                gaps.append(
                    "target_url: the listing page does not exist until the site publishes "
                    "it; submission is unaffected, L4 waits (fallback=unknown, not absent)"
                )
            else:
                gaps.append("target_url: no landing-page evidence in prose; fallback=none (L4 unverifiable)")
        if not has_channel:
            if prospect.discovered:
                gaps.append(
                    "submission_url/contact_email: discovery observed the site and found "
                    "neither a form nor an address"
                )
            else:
                gaps.append("submission_url/contact_email: no delivery channel in prose; fallback=none")
        if not own_url:
            gaps.append("own_url: no landing-page hint in prose; fallback=none")
        if prospect.http_status is None:
            gaps.append("http_status: not observed upstream; fallback=unknown")

        # Submitting and observing are different gates. A pool directory has no
        # listing page until it publishes one, so requiring target_url to submit
        # would stall every directory row behind an unknowable future value.
        if status not in {"blocked", "deferred"}:
            if not has_channel:
                status = "no_channel" if prospect.discovered else "needs_enrichment"
            elif not own_url:
                status = "needs_enrichment"
            elif target_url:
                status = "ready_for_review"
            else:
                status = "ready_to_submit"

        channel_type = TYPE_MAP.get(prospect.type_key, "other")

        content = None
        if templates and has_channel and own_url:
            context = {
                "center_domain": snapshot.center_domain,
                "own_site_base": own_site_base,
                "own_url": own_url or "",
                "target_domain": domain,
                "candidate_url": prospect.url,
                "target_url": target_url or "",
                "submission_url": submission_url or "",
                "contact_email": contact_email or "",
                "own_url_source": own_url_source,
            }
            try:
                content = templates.render(channel_type, context)
            except PolicyError as exc:
                reasons.append("template render blocked: %s" % exc.to_dict().get("reason", "policy"))
                if status not in {"blocked", "deferred"}:
                    status = "needs_enrichment"

        rows.append(
            ManifestRow(
                prospect_id="%s#%s" % (domain, channel_type),
                center_domain=snapshot.center_domain,
                target_domain=domain,
                candidate_url=prospect.url,
                target_url=target_url,
                submission_url=submission_url,
                own_url=own_url,
                contact_email=contact_email,
                contact_state=contact_state,
                contact_source_url=contact_source_url,
                channel_type=channel_type,
                type_key=prospect.type_key,
                status=status,
                reasons=reasons,
                gaps=gaps,
                idempotency_key=_idempotency_key(
                    snapshot.center_domain, domain, channel_type, target_url or ""
                ),
                content=content,
                channels=delivery_channels(submission_url, contact_email),
            )
        )
    return rows
