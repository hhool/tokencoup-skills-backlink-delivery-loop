"""Draft outbound email: render the copy, avoid a footprint, write a file.

Two jobs, and the second one is the reason this module exists.

**Render.** Subject and body come from the channel template plus the manifest row's
context. Nothing is invented at draft time: a missing slot is a refusal, not a
blank line.

**Avoid a footprint.** The same paragraph sent to forty directories is one
paragraph sent forty times, and search engines are good at noticing. Templates
therefore carry several bodies per channel, and this module picks the first one
whose shingle similarity to copy already drafted in the batch stays under a
threshold. If every variant is too close to something already written, the row is
skipped with a reason instead of being written as a near-duplicate: a duplicated
pitch is worse than a missing one, because it cannot be taken back.

Nothing here transmits. A draft is a file on disk until a human sends it.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from ..adapters.mailfile import write_eml
from ..errors import PolicyError
from ..models import ManifestRow
from ..templates import TemplateSet, similarity
from .normalize import assert_outbound_copy

DEFAULT_SIMILARITY_CEILING = 0.80

# Anything that looks like a host or a URL. Two pitches that differ only here are
# the same pitch: swapping example.com for example.org is not a rewrite.
_REFERENCE = re.compile(r"https?://\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I)


def masked(body: str, values: Iterable[str] = ()) -> str:
    """Strip the site-specific references, so structure is what gets compared."""
    text = body or ""
    for value in sorted({item for item in values if item and len(item) > 3}, key=len, reverse=True):
        text = text.replace(value, " <ref> ")
    return _REFERENCE.sub(" <ref> ", text)


def footprint_score(left: str, right: str, left_masked: str = "", right_masked: str = "") -> float:
    """The worse of the two comparisons.

    Raw similarity catches copy-paste. Masked similarity catches the far more common
    failure: one template with the domain name swapped, which scores around 0.76 raw
    and is still the same paragraph to a duplicate detector.
    """
    score = similarity(left, right)
    if left_masked and right_masked:
        score = max(score, similarity(left_masked, right_masked))
    return score


@dataclass
class Draft:
    target_domain: str
    to_addr: str
    subject: str
    body: str
    variant: int
    variant_count: int
    path: str = ""
    max_similarity: float = 0.0
    nearest: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    masked_body: str = ""  # comparison key; not part of the report


def _context(row: ManifestRow, center_domain: str, own_site_base: str) -> Dict[str, str]:
    return {
        "center_domain": center_domain,
        "own_site_base": own_site_base,
        "own_url": row.own_url or "",
        "target_domain": row.target_domain,
        "candidate_url": row.candidate_url or "",
        "target_url": row.target_url or "",
        "submission_url": row.submission_url or "",
        "contact_email": row.contact_email or "",
    }


def select_variant(
    candidates: Sequence[dict],
    accepted: Sequence[Draft],
    ceiling: float,
    context_values: Sequence[str] = (),
) -> tuple:
    """First candidate far enough from everything already accepted.

    Returns ``(candidate, worst_similarity, nearest_domain)``. When nothing clears
    the ceiling the best candidate is still returned so the caller can report how
    close it came - the number is the useful part of a refusal.
    """
    best = None
    for candidate in candidates:
        body = candidate["email"]["body"]
        body_masked = masked(body, context_values)
        worst, nearest = 0.0, ""
        for draft in accepted:
            score = footprint_score(body, draft.body, body_masked, draft.masked_body or masked(draft.body, context_values))
            if score > worst:
                worst, nearest = score, draft.target_domain
        if worst < ceiling:
            return candidate, worst, nearest
        if best is None or worst < best[1]:
            best = (candidate, worst, nearest)
    return best if best else (None, 0.0, "")


def draft_batch(
    rows: Sequence[ManifestRow],
    templates: TemplateSet,
    center_domain: str,
    own_site_base: str,
    from_addr: str,
    out_dir: str,
    from_name: str = "",
    ceiling: float = DEFAULT_SIMILARITY_CEILING,
    channel: str = "email",
    now: Optional[dt.datetime] = None,
    write: bool = True,
) -> dict:
    """Draft every emailable row, in order, keeping the batch internally distinct."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    accepted: List[Draft] = []
    skipped: List[dict] = []

    for row in rows:
        # Status first: a blocked row is blocked whether or not it has an address,
        # and saying "no email channel" about it hides the real reason.
        if row.status in {"blocked", "deferred", "no_channel", "needs_enrichment"}:
            skipped.append({"target_domain": row.target_domain, "reason": "status_" + row.status})
            continue
        if channel not in row.channels:
            skipped.append({"target_domain": row.target_domain, "reason": "no_email_channel"})
            continue
        if not row.contact_email:
            skipped.append({"target_domain": row.target_domain, "reason": "no_address"})
            continue

        context = _context(row, center_domain, own_site_base)
        candidates = templates.render_variants(row.channel_type, context)
        if not candidates:
            skipped.append({"target_domain": row.target_domain, "reason": "template_refused"})
            continue

        chosen, worst, nearest = select_variant(
            candidates, accepted, ceiling, context_values=list(context.values())
        )
        if chosen is None:
            skipped.append({"target_domain": row.target_domain, "reason": "template_refused"})
            continue

        subject = chosen["email"]["subject"]
        body = chosen["email"]["body"]
        try:
            assert_outbound_copy(subject, body)  # R-522, checked at the boundary too
        except PolicyError as exc:
            skipped.append({
                "target_domain": row.target_domain,
                "reason": "policy",
                "detail": exc.to_dict().get("reason", ""),
            })
            continue

        if worst >= ceiling:
            skipped.append({
                "target_domain": row.target_domain,
                "reason": "near_duplicate",
                "closest": nearest,
                "similarity": round(worst, 3),
                "hint": "add another body variant to this channel, or rewrite the one in use",
            })
            continue

        headers = {
            "X-BDL-Center-Domain": center_domain,
            "X-BDL-Target-Domain": row.target_domain,
            "X-BDL-Channel": channel,
            "X-BDL-Channel-Type": row.channel_type,
            "X-BDL-Idempotency-Key": row.idempotency_key,
        }
        if row.submission_url:
            headers["X-BDL-Submission-URL"] = row.submission_url

        draft = Draft(
            target_domain=row.target_domain,
            to_addr=row.contact_email,
            subject=subject,
            body=body,
            variant=int(chosen.get("variant", 0)),
            variant_count=int(chosen.get("variant_count", 1)),
            max_similarity=round(worst, 3),
            nearest=nearest,
            headers=headers,
            masked_body=masked(body, list(context.values())),
        )
        if write:
            draft.path = str(write_eml(
                out_dir,
                target_domain=row.target_domain,
                from_addr=from_addr,
                to_addr=draft.to_addr,
                subject=subject,
                body=body,
                from_name=from_name,
                headers=headers,
                now=moment,
                message_id="<%s@%s>" % (row.idempotency_key, center_domain),
            ))
        accepted.append(draft)

    return {
        "drafted": [
            {key: value for key, value in draft.__dict__.items() if key != "masked_body"}
            for draft in accepted
        ],
        "skipped": skipped,
        "counts": {
            "drafted": len(accepted),
            "skipped": len(skipped),
            "variants_used": len({draft.variant for draft in accepted}),
            "duplicates_blocked": sum(1 for item in skipped if item["reason"] == "near_duplicate"),
        },
        "ceiling": ceiling,
        "note": (
            "A draft is a file, not a send. Similarity is measured over 5-word shingles "
            "against copy already drafted in this batch; a near duplicate is skipped, "
            "not merged."
        ),
    }
