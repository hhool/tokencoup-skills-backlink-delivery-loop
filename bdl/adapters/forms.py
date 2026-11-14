"""Adapter: the form fingerprint registry.

The first time a submission form is seen, a human has to read it: which field is
the name, which is the message, what counts as a successful submission. That
reading is an asset, and it is stored here so the next encounter is a one-click
prefill instead of another manual pass.

The fingerprint is a hash of (method, action path, sorted field names) - NOT the
domain. A site's contact form and its "submit a resource" form have different
fields, and keying on the domain alone would happily write a pitch into an
"Order number" box.

A registry entry is only ever created with `reviewed_by` set. There is no path
that auto-registers a form: an unverified field map is worse than no field map.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import ConfigError, DeliveryError, WriteError
from ..models import normalize_domain

SCHEMA_VERSION = "1.1.0"
REGISTRY_NAME = "registry.json"

_DIGITS = re.compile(r"\d+")


def fingerprint(method: str, action: str, field_names: List[str]) -> str:
    action_path = "/" + (action or "").split("://", 1)[-1].split("/", 1)[-1].lstrip("/")
    names = ",".join(sorted(name.strip().lower() for name in field_names if name.strip()))
    material = "|".join((method.strip().upper() or "POST", action_path, names))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def signature(field_names: List[str]) -> str:
    """The wire names with their counters blanked: ``wpforms[fields][0]`` -> ``wpforms[fields][#]``.

    This is what makes one reviewed map serve the next site running the same plugin.
    A shape alone cannot: it says "this form has name, email, url, message" but not
    which wire field carries which slot. Two sites on WPForms have identical names,
    so the mapping transfers; two bespoke forms with the same slots do not.
    """
    parts = []
    for name in field_names or []:
        lowered = (name or "").strip().lower()
        if not lowered:
            continue
        parts.append(_DIGITS.sub("#", lowered))
    return ",".join(sorted(parts))


def _registry_path(forms_dir: str | Path) -> Path:
    return Path(forms_dir) / REGISTRY_NAME


def load_registry(forms_dir: str | Path) -> Dict[str, Any]:
    path = _registry_path(forms_dir)
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("Cannot read the form registry", path=str(path), reason=str(exc)) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        raise ConfigError("Form registry is malformed", path=str(path))
    payload.setdefault("schema_version", SCHEMA_VERSION)
    return payload


def save_registry(forms_dir: str | Path, registry: Dict[str, Any]) -> Path:
    path = _registry_path(forms_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        raise WriteError("Cannot write the form registry", path=str(path), reason=exc.strerror) from exc
    return path


def check(
    forms_dir: str | Path,
    method: str,
    action: str,
    field_names: List[str],
) -> Dict[str, Any]:
    """Resolve a form to its fingerprint and say whether it has been verified."""
    key = fingerprint(method, action, field_names)
    entry = load_registry(forms_dir)["entries"].get(key)
    if entry is None:
        return {
            "fingerprint": key,
            "known": False,
            "status": "needs_human_verify",
            "next_action": "open the form, map each field, then run `forms register`",
        }
    return {
        "fingerprint": key,
        "known": True,
        "status": entry.get("status", "verified"),
        "entry": entry,
        "next_action": "prefill from the registered field map and submit with a human click",
    }


def lookup(
    forms_dir: str | Path,
    method: str,
    action: str,
    field_names: List[str],
    shape: str = "",
) -> Dict[str, Any]:
    """Find the best map for a form: this exact form, or the same plugin elsewhere.

    Three outcomes, in descending order of trust:

      ``exact``   the same method + action + field names were reviewed before.
      ``shape``   a different site, same form shape AND same wire-name signature -
                  the same plugin, so the same field carries the same slot.
      ``none``    nothing to reuse; the browser-side heuristics guess, and a human
                  confirms what they guessed.
    """
    key = fingerprint(method, action, field_names)
    entries = load_registry(forms_dir)["entries"]
    entry = entries.get(key)
    if entry is not None:
        return {
            "fingerprint": key,
            "match": "exact",
            "known": True,
            "slot_map": dict(entry.get("slot_map") or {}),
            "reused_from": entry.get("domain", ""),
            "entry": entry,
        }

    wanted_signature = signature(field_names)
    if shape and wanted_signature:
        candidates = [
            item
            for item in entries.values()
            if item.get("shape") == shape
            and item.get("signature") == wanted_signature
            and (item.get("slot_map") or {})
        ]
        if candidates:
            # Newest review wins: a later reading of the same plugin is the one
            # that saw its current field set.
            best = sorted(candidates, key=lambda item: str(item.get("reviewed_at") or ""))[-1]
            return {
                "fingerprint": key,
                "match": "shape",
                "known": True,
                "slot_map": dict(best.get("slot_map") or {}),
                "reused_from": best.get("domain", ""),
                "entry": best,
            }

    return {"fingerprint": key, "match": "none", "known": False, "slot_map": {}, "reused_from": ""}



def register(
    forms_dir: str | Path,
    domain: str,
    method: str,
    action: str,
    field_names: List[str],
    field_map: Dict[str, str],
    reviewed_by: str,
    success_selector: str = "",
    evidence: str = "",
    captcha: bool = False,
    requires_account: bool = False,
    notes: str = "",
    shape: str = "",
    slot_map: Optional[Dict[str, str]] = None,
    submitted_at: str = "",
) -> Dict[str, Any]:
    if not (reviewed_by or "").strip():
        raise DeliveryError(
            "A first-seen form must be verified by a named human",
            hint="--reviewed-by <name>",
        )
    target = normalize_domain(domain)
    if not target:
        raise DeliveryError("A target domain is required", hint="--domain")
    if not field_names:
        raise DeliveryError("The form field names are required", hint="--fields name,email,message")

    key = fingerprint(method, action, field_names)
    registry = load_registry(forms_dir)
    entry = {
        "fingerprint": key,
        "domain": target,
        "method": (method or "POST").strip().upper(),
        "action": action,
        "fields": sorted(name.strip().lower() for name in field_names if name.strip()),
        "signature": signature(field_names),
        "shape": shape,
        # What each wire field means. This is the part that transfers to the next
        # site running the same plugin; the values below do not.
        "slot_map": dict(sorted((slot_map or {}).items())),
        "field_map": dict(sorted(field_map.items())),
        "success_selector": success_selector,
        "evidence": evidence,
        "captcha": bool(captcha),
        "requires_account": bool(requires_account),
        "status": "manual_only" if (captcha or requires_account) else "verified",
        "reviewed_by": reviewed_by.strip(),
        "reviewed_at": _now(),
        "submitted_at": submitted_at,
        "notes": notes,
    }
    registry["entries"][key] = entry
    registry["schema_version"] = SCHEMA_VERSION
    save_registry(forms_dir, registry)
    return entry


def adopt_receipt(forms_dir: str | Path, receipt: Dict[str, Any], reviewed_by: str) -> Dict[str, Any]:
    """Register a form from what the browser reported after a human submitted it.

    The receipt is evidence of three things only: the field names as rendered, the
    slot each field carried, and the page the site answered with. It is not proof
    that a link went live - that stays the L4 observer's job - so the evidence is
    stored as a submission acknowledgement, `status: verified`, never `live`.
    """
    if not isinstance(receipt, dict):
        raise DeliveryError("A receipt must be a JSON object")
    if str(receipt.get("kind") or "") != "bdl.submit_receipt":
        raise DeliveryError(
            "Not a submission receipt", value=str(receipt.get("kind") or ""),
            hint="expected kind = bdl.submit_receipt, as written by the prefill userscript",
        )
    if not receipt.get("submitted_by_human"):
        raise DeliveryError(
            "A receipt must be the result of a human clicking submit",
            rule="R-520",
            hint="the userscript sets submitted_by_human only after a human click",
        )
    fields = [str(name) for name in (receipt.get("field_names") or []) if str(name).strip()]
    slot_map = {
        str(name): str(slot)
        for name, slot in (receipt.get("slot_map") or {}).items()
        if str(name).strip() and str(slot).strip()
    }
    if not fields:
        raise DeliveryError("The receipt lists no field names", hint="field_names")
    if not slot_map:
        raise DeliveryError(
            "The receipt maps no field to a slot",
            hint="slot_map, so the next encounter can be filled without a human reading it",
        )
    response_url = str(receipt.get("response_url") or "")
    if not response_url:
        raise DeliveryError(
            "A submission with no response page is not evidence",
            hint="response_url; reload the acknowledgement page so the receipt can capture it",
            rule="L1",
        )

    return register(
        forms_dir,
        domain=str(receipt.get("target_domain") or ""),
        method=str(receipt.get("method") or "POST"),
        action=str(receipt.get("submission_url") or ""),
        field_names=fields,
        field_map={name: str(value) for name, value in (receipt.get("values") or {}).items()},
        reviewed_by=reviewed_by,
        success_selector=str(receipt.get("success_selector") or ""),
        evidence=response_url,
        captcha=bool(receipt.get("captcha_present")),
        shape=str(receipt.get("shape") or ""),
        slot_map=slot_map,
        submitted_at=str(receipt.get("posted_at") or ""),
        notes="adopted from a browser receipt",
    )



def listing(forms_dir: str | Path, domain: str = "") -> List[Dict[str, Any]]:
    entries = load_registry(forms_dir)["entries"].values()
    wanted = normalize_domain(domain)
    rows = [entry for entry in entries if not wanted or entry.get("domain") == wanted]
    return sorted(rows, key=lambda entry: (entry.get("domain", ""), entry.get("fingerprint", "")))


def show(forms_dir: str | Path, key: str = "", domain: str = "") -> List[Dict[str, Any]]:
    if key:
        entry: Optional[Dict[str, Any]] = load_registry(forms_dir)["entries"].get(key)
        if entry is None:
            raise DeliveryError("No such form fingerprint", fingerprint=key)
        return [entry]
    return listing(forms_dir, domain)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
