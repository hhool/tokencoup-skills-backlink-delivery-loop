"""Fill a third-party submission form without typing it a hundred times.

A form submission is the one delivery channel that cannot be automated end to end
and still be honest: a human judges the CAPTCHA, the terms, and the thank-you page.
What *can* be automated is everything before and after that judgement:

    pack    values per slot, ready for a browser to place in the right inputs
    fill    the userscript places them, by slot, and leaves the human the click
    adopt   the receipt the browser writes back becomes a registry entry, so the
            next form with the same shape and the same wire names fills itself

Three rules keep that from becoming a spam machine:

  * **No value is invented.** An absent `from_email` or landing URL is a refusal,
    not a blank field. A `phone` slot is never filled: a phone number nobody
    answers is worse than a form left alone.
  * **The first encounter is a human's.** With no reviewed map the pack is marked
    `mapping: none`; the browser guesses, a human confirms, and only then is a
    slot map stored.
  * **Nothing is transmitted from here.** No POST, no click. The pack is a file;
    the submission happens in the operator's browser, under their name.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..adapters import forms as registry
from ..errors import DeliveryError, PolicyError
from ..models import ManifestRow, normalize_domain
from ..templates import TemplateSet
from .draft import DEFAULT_SIMILARITY_CEILING, masked
from .normalize import assert_outbound_copy

PACK_KIND = "bdl.prefill_pack"
PACK_SCHEMA_VERSION = "1.0.0"
RECEIPT_KIND = "bdl.submit_receipt"

# The slot vocabulary a form can carry. Anything outside it is a site-specific
# field: it is shown to the human, never guessed at.
SLOTS = ("name", "email", "url", "subject", "message", "company")

# Values that must never be synthesised. A phone number is the only one today:
# there is no plausible substitute for a real one.
NEVER_SYNTHESISED = ("phone",)

READY_STATUSES = ("ready_to_submit", "ready_for_review")


@dataclass
class _Copy:
    """A composed body, compared against copy already prepared in this batch."""

    target_domain: str
    body: str
    masked_body: str = ""


@dataclass
class Pack:
    target_domain: str
    submission_url: str
    values: Dict[str, str]
    fingerprint: str = ""
    shape: str = ""
    mapping: str = "none"  # exact | shape | observed | none
    reused_from: str = ""
    method: str = "post"
    form_fields: List[str] = field(default_factory=list)
    captcha: bool = False
    success_selector: str = ""
    gaps: List[str] = field(default_factory=list)
    variant: int = 0
    max_similarity: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target_domain": self.target_domain,
            "submission_url": self.submission_url,
            "method": self.method,
            "values": dict(sorted(self.values.items())),
            "fingerprint": self.fingerprint,
            "shape": self.shape,
            "mapping": self.mapping,
            "reused_from": self.reused_from,
            "form_fields": list(self.form_fields),
            "captcha": self.captcha,
            "success_selector": self.success_selector,
            "gaps": list(self.gaps),
            "variant": self.variant,
            "max_similarity": round(self.max_similarity, 3),
        }


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def slot_values(
    row: ManifestRow,
    center_domain: str,
    own_landing_url: str,
    from_email: str,
    from_name: str,
    rendered: Dict[str, Any],
) -> Dict[str, str]:
    """What goes in each slot, or nothing when the value would have to be invented."""
    email = rendered.get("email") or {}
    values: Dict[str, str] = {}
    name = (from_name or "").strip() or center_domain
    if name:
        values["name"] = name
    if from_email:
        values["email"] = from_email
    landing = (row.own_url or own_landing_url or "").strip()
    if landing:
        values["url"] = landing
    subject = str(email.get("subject") or "").strip()
    if subject:
        values["subject"] = subject
    body = str(email.get("body") or "").strip()
    if body:
        values["message"] = body
    if center_domain:
        values["company"] = center_domain
    return values


def _guess_method(index: Dict[str, Dict[str, Any]], domain: str) -> str:
    entry = index.get(domain) or {}
    return str(entry.get("method") or "post").lower()


def _snapshot_index(prospects: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """Key discovery facts by target domain, whatever shape they arrive in."""
    index: Dict[str, Dict[str, Any]] = {}
    for item in prospects or []:
        if isinstance(item, dict):
            domain = normalize_domain(str(item.get("domain") or ""))
            if not domain:
                continue
            index[domain] = {
                "submission_url": item.get("submission_url") or "",
                "form_shape": item.get("form_shape") or "",
                "form_fields": list(item.get("form_fields") or []),
            }
        else:  # a Prospect dataclass
            domain = normalize_domain(str(getattr(item, "domain", "") or ""))
            if not domain:
                continue
            index[domain] = {
                "submission_url": getattr(item, "submission_url", None) or "",
                "form_shape": getattr(item, "form_shape", None) or "",
                "form_fields": list(getattr(item, "form_fields", None) or []),
            }
    return index


def _resolve_map(
    forms_dir: str | Path,
    method: str,
    action: str,
    field_names: Sequence[str],
    shape: str,
) -> Dict[str, Any]:
    """Exact review first, then the same plugin elsewhere, then nothing.

    The method is a guess (the snapshot does not carry it), so both POST and GET
    are tried before concluding that no map exists.
    """
    if not field_names:
        return {"match": "none", "known": False, "slot_map": {}, "reused_from": "", "fingerprint": ""}
    tried = {method or "post"}
    tried.update({"post", "get"})
    best: Dict[str, Any] = {"match": "none", "known": False, "slot_map": {}, "reused_from": "", "fingerprint": ""}
    for candidate in sorted(tried):
        found = registry.lookup(forms_dir, candidate.upper(), action, list(field_names), shape)
        if found.get("match") == "exact":
            return found
        if found.get("match") == "shape" and best.get("match") != "shape":
            best = found
    return best


def build_packs(
    rows: Sequence[ManifestRow],
    templates: TemplateSet,
    center_domain: str,
    own_landing_url: str,
    from_email: str,
    from_name: str = "",
    forms_dir: str | Path = "",
    prospects: Iterable[Any] = (),
    ceiling: float = DEFAULT_SIMILARITY_CEILING,
    channel: str = "form",
    write: bool = True,
    out_dir: str | Path = "",
) -> Dict[str, Any]:
    """Compose one pack per submittable row, in order, keeping them distinct."""
    index = _snapshot_index(prospects)
    base = normalize_domain(center_domain)
    accepted: List[_Copy] = []
    packs: List[Pack] = []
    skipped: List[Dict[str, Any]] = []
    batch_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for row in rows:
        if row.status not in READY_STATUSES:
            skipped.append({"target_domain": row.target_domain, "reason": "status_" + row.status})
            continue
        if channel not in (row.channels or []):
            skipped.append({"target_domain": row.target_domain, "reason": "no_form_channel"})
            continue
        if not row.submission_url:
            skipped.append({"target_domain": row.target_domain, "reason": "no_submission_url"})
            continue
        if not from_email:
            skipped.append({
                "target_domain": row.target_domain,
                "reason": "no_from_email",
                "hint": "BDL_FROM_EMAIL, so the form has an address to answer",
            })
            continue

        context = _context(row, base, own_landing_url)
        candidates = templates.render_variants(row.channel_type, context)
        if not candidates:
            skipped.append({"target_domain": row.target_domain, "reason": "template_refused"})
            continue

        chosen, worst, nearest = _select(candidates, accepted, ceiling, list(context.values()))
        if chosen is None:
            skipped.append({"target_domain": row.target_domain, "reason": "template_refused"})
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
        try:
            assert_outbound_copy(
                str((chosen.get("email") or {}).get("subject") or ""),
                str((chosen.get("email") or {}).get("body") or ""),
            )
        except PolicyError as exc:
            skipped.append({
                "target_domain": row.target_domain,
                "reason": "policy",
                "detail": exc.to_dict().get("reason", ""),
            })
            continue

        values = slot_values(row, base, own_landing_url, from_email, from_name, chosen)

        facts = index.get(row.target_domain) or {}
        form_fields = [str(name) for name in facts.get("form_fields") or []]
        shape = str(facts.get("form_shape") or "")
        method = _guess_method(index, row.target_domain)
        found = _resolve_map(forms_dir, method, row.submission_url, form_fields, shape)
        slot_map = dict(found.get("slot_map") or {})

        gaps: List[str] = []
        if not slot_map:
            gaps.append("mapping_unknown")
        else:
            for wire, slot in sorted(slot_map.items()):
                if slot in NEVER_SYNTHESISED:
                    gaps.append("slot_" + slot)
                elif slot and slot not in values:
                    gaps.append("no_value_for_" + slot)
        if not form_fields:
            gaps.append("form_fields_unknown")

        pack = Pack(
            target_domain=row.target_domain,
            submission_url=row.submission_url,
            values=values,
            fingerprint=str(found.get("fingerprint") or ""),
            shape=shape,
            mapping="observed" if slot_map else "none",
            reused_from=str(found.get("reused_from") or ""),
            method=method.upper(),
            form_fields=form_fields,
            captcha=bool((found.get("entry") or {}).get("captcha")),
            success_selector=str((found.get("entry") or {}).get("success_selector") or ""),
            gaps=sorted(set(gaps)),
            variant=int(chosen.get("variant", 0)),
            max_similarity=worst,
        )
        if found.get("match") in {"exact", "shape"}:
            pack.mapping = found["match"]
        packs.append(pack)
        accepted.append(_Copy(
            target_domain=row.target_domain,
            body=str((chosen.get("email") or {}).get("body") or ""),
            masked_body=masked(str((chosen.get("email") or {}).get("body") or ""), list(context.values())),
        ))

    document = {
        "kind": PACK_KIND,
        "schema_version": PACK_SCHEMA_VERSION,
        "generated_at": _now(),
        "batch_id": batch_id,
        "center_domain": base,
        "own_landing_url": own_landing_url,
        "from_email": from_email,
        "from_name": from_name,
        "load_in": "paste this file into the BDL panel of the prefill userscript, once",
        "targets": [pack.as_dict() for pack in packs],
    }
    path = ""
    if write and out_dir:
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        path = str(folder / ("prefill-%s-%s.json" % (base or "center", batch_id)))
        Path(path).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "pack": document,
        "path": path,
        "counts": {
            "packed": len(packs),
            "skipped": len(skipped),
            "needs_mapping": sum(1 for pack in packs if pack.mapping == "none"),
            "reused": sum(1 for pack in packs if pack.mapping in {"exact", "shape"}),
            "with_gaps": sum(1 for pack in packs if pack.gaps),
        },
        "skipped": skipped,
        "ceiling": ceiling,
        "note": (
            "A pack fills fields. It does not submit: the form is posted by a human in "
            "their own browser, and the receipt they save is what registers the map."
        ),
    }


def _select(
    candidates: Sequence[Dict[str, Any]],
    accepted: Sequence[_Copy],
    ceiling: float,
    context_values: Sequence[str],
) -> tuple:
    """First candidate far enough from the copy already in this batch."""
    from .draft import footprint_score

    best = None
    for candidate in candidates:
        body = str((candidate.get("email") or {}).get("body") or "")
        body_masked = masked(body, context_values)
        worst, nearest = 0.0, ""
        for item in accepted:
            score = footprint_score(body, item.body, body_masked, item.masked_body)
            if score > worst:
                worst, nearest = score, item.target_domain
        if worst < ceiling:
            return candidate, worst, nearest
        if best is None or worst < best[1]:
            best = (candidate, worst, nearest)
    return best if best else (None, 0.0, "")


def load_receipt(path: str | Path) -> Dict[str, Any]:
    """Read one receipt file, with the errors a human can act on."""
    target = Path(path)
    if not target.is_file():
        raise DeliveryError("Receipt not found", path=str(target))
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryError("Cannot read the receipt", path=str(target), reason=str(exc)) from exc
    if isinstance(payload, list):
        raise DeliveryError(
            "One receipt per file", path=str(target),
            hint="the userscript writes a single object; split the file if you merged them",
        )
    return payload


def record_command(receipt: Dict[str, Any], center_domain: str, reviewed_by: str = "") -> str:
    """The exact command a human runs to turn a receipt into an event."""
    parts = [
        "bdl record",
        "--action submitted",
        "--channel form",
        "--target-domain %s" % (receipt.get("target_domain") or "<domain>"),
        "--evidence %s" % (receipt.get("response_url") or "<thank-you url>"),
    ]
    if center_domain:
        parts.append("--center-domain %s" % center_domain)
    parts.append("--note 'form receipt %s'" % (receipt.get("receipt_id") or ""))
    if reviewed_by:
        parts.append("--actor %s" % reviewed_by)
    return " ".join(parts)
