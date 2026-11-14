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
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import ConfigError, DeliveryError, WriteError
from ..models import normalize_domain

SCHEMA_VERSION = "1.0.0"
REGISTRY_NAME = "registry.json"


def fingerprint(method: str, action: str, field_names: List[str]) -> str:
    action_path = "/" + (action or "").split("://", 1)[-1].split("/", 1)[-1].lstrip("/")
    names = ",".join(sorted(name.strip().lower() for name in field_names if name.strip()))
    material = "|".join((method.strip().upper() or "POST", action_path, names))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


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
        "field_map": dict(sorted(field_map.items())),
        "success_selector": success_selector,
        "evidence": evidence,
        "captcha": bool(captcha),
        "requires_account": bool(requires_account),
        "status": "manual_only" if (captcha or requires_account) else "verified",
        "reviewed_by": reviewed_by.strip(),
        "reviewed_at": _now(),
        "notes": notes,
    }
    registry["entries"][key] = entry
    registry["schema_version"] = SCHEMA_VERSION
    save_registry(forms_dir, registry)
    return entry


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
