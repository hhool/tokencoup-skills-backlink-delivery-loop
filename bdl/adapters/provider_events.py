"""Adapter: read a sending provider's per-message events (L2 transport receipts).

The ledger records ``sent`` only when a provider id exists - that id is the
handle this module pulls on. One GET per outstanding send answers "did it arrive,
or did it bounce?", which is the only question a send can answer. It never
answers "is the link live": that is L4 and belongs to the observer.

Three rules keep this honest:

  * A read, not a write. Every call is a single authenticated GET. Nothing is
    transmitted to the target site, nothing is resent.
  * Unknown stays unknown. A 401, a 429, a timeout or a body we do not recognise
    is ``unknown``, never ``bounced``. Concluding "the mailbox is dead" from a
    rate-limited API would close a live contact for months.
  * Provider vocabulary never leaks. Each provider has its own status words;
    they are mapped to the four words the ledger understands, and the raw word
    is kept alongside so the mapping stays auditable.

The HTTP call is isolated behind a ``Reader`` callable, so every branch is
unit-tested offline with no network and no credentials.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..errors import ConfigError, DeliveryError

Reader = Callable[[str], Tuple[int, str]]
"""A reader returns (http_status, body) for a signed request URL."""

# ---------------------------------------------------------------------------
# Provider registry. Adding a provider is a data change, not a code change:
# url template, auth shape, and the field that carries the status word.
# ---------------------------------------------------------------------------

PROVIDERS: Dict[str, Dict[str, str]] = {
    "resend": {
        "url": "https://api.resend.com/emails/{id}",
        "auth": "bearer",
        "status_field": "last_event",
    },
    "postmark": {
        "url": "https://api.postmarkapp.com/messages/outbound/{id}/details",
        "auth": "header",
        "auth_header": "X-Postmark-Server-Token",
        "status_field": "Status",
    },
}

# provider status word -> (ledger status, note). A missing word is ``unknown``.
STATUS_MAP: Dict[str, Dict[str, Tuple[str, str]]] = {
    "resend": {
        "sent": ("sent", "accepted by the provider, not yet a delivery"),
        "queued": ("pending", "queued at the provider"),
        "delivered": ("delivered", ""),
        "delivery_delayed": ("pending", "provider reports a delay, still in flight"),
        "bounced": ("bounced", ""),
        "complained": ("bounced", "recipient marked the mail as spam"),
        "opened": ("delivered", "opened implies the mail arrived"),
        "clicked": ("delivered", "clicked implies the mail arrived"),
    },
    "postmark": {
        "queued": ("pending", "queued at the provider"),
        "submitted": ("sent", "accepted by the provider, not yet a delivery"),
        "delivered": ("delivered", ""),
        "bounced": ("bounced", ""),
        "softbounce": ("pending", "soft bounce: retried, not a delivery failure yet"),
        "hardbounce": ("bounced", ""),
        "spamcomplaint": ("bounced", "recipient marked the mail as spam"),
    },
}

# Statuses the ledger accepts as an event. ``sent`` is already recorded by hand at
# send time; ``pending`` / ``unknown`` write nothing.
RECORDABLE = ("delivered", "bounced")

# A default urllib user agent ("Python-urllib/3.x") is refused by the CDN in front
# of the provider APIs with Cloudflare error 1010 ("blocked access based on your
# browser's signature"). That refusal would surface as ``unknown`` forever, so the
# reader identifies itself explicitly.
USER_AGENT = "bdl-receipt-poll/1.0 (+https://github.com/hhool/tokencoup-skills-backlink-delivery-loop)"


def register_provider(name: str, config: Dict[str, str], status_map: Dict[str, Tuple[str, str]]) -> None:
    """Add a provider at runtime without editing this module."""
    key = (name or "").strip().lower()
    if not key:
        raise DeliveryError("A provider name is required")
    for field in ("url", "auth", "status_field"):
        if not config.get(field):
            raise DeliveryError("Provider config is incomplete", provider=key, missing=field)
    PROVIDERS[key] = {**config, "url": config["url"].replace("<id>", "{id}")}
    STATUS_MAP[key] = dict(status_map)


def provider_config(provider: str, base_url: str = "") -> Dict[str, str]:
    key = (provider or "").strip().lower()
    if not key:
        raise ConfigError(
            "No sending provider configured",
            hint="--provider resend, or BDL_PROVIDER",
        )
    if key not in PROVIDERS:
        raise ConfigError(
            "Unknown sending provider",
            provider=key,
            allowed=sorted(PROVIDERS),
            hint="Registering a new provider needs a url template and a status map",
        )
    config = dict(PROVIDERS[key])
    if base_url:
        config["url"] = base_url.rstrip("/") + "/emails/{id}"
    return config


def request_url(config: Dict[str, str], provider_id: str) -> str:
    return config["url"].replace("{id}", str(provider_id))


def default_reader(provider: str, config: Dict[str, str], api_key: str = "") -> Reader:
    """Build a proxy-free urllib reader. The key is read from the environment."""
    secret = (api_key or os.environ.get("BDL_PROVIDER_API_KEY", "")).strip()
    if not secret:
        raise ConfigError(
            "A provider API key is required to read receipts",
            env="BDL_PROVIDER_API_KEY",
            hint="Read-only use; a restricted key with send scope disabled is enough",
        )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(url: str) -> Tuple[int, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if config.get("auth") == "header":
            headers[config.get("auth_header", "Authorization")] = secret
        else:
            headers["Authorization"] = "Bearer " + secret
        request = urllib.request.Request(
            url,
            headers=headers,
            method="GET",
        )
        try:
            with opener.open(request, timeout=20) as response:
                return response.getcode(), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            return exc.code, body
        except Exception as exc:  # timeout, DNS, TLS - all unknown
            raise DeliveryError(
                "Provider read failed",
                provider=provider,
                reason=type(exc).__name__,
                hint="A failed read is unknown, not a bounce",
            ) from exc

    return read


def fixture_reader(path: Union[str, Path]) -> Reader:
    """Read statuses from a local file instead of the network.

    Accepts JSONL rows of ``{"provider_id": "...", "status": "delivered"}`` or
    ``{"provider_id": "...", "http_status": 200, "body": {...}}``. This is how a
    receipt poll is rehearsed and how the offline tests run.
    """
    target = Path(path)
    if not target.is_file():
        raise ConfigError("Fixture file not found", path=str(target))
    rows: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("provider_id"):
            rows.append(payload)
    index = {str(row["provider_id"]): row for row in rows}

    def read(url: str) -> Tuple[int, str]:
        match = url.rstrip("/").rsplit("/", 1)[-1]
        row = index.get(match)
        if row is None:
            return 404, json.dumps({"error": "not found in fixture"})
        if "body" in row:
            body = row["body"]
            return int(row.get("http_status", 200)), (
                body if isinstance(body, str) else json.dumps(body)
            )
        return int(row.get("http_status", 200)), json.dumps({"last_event": row.get("status", "")})

    return read


def _status_word(payload: Dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if isinstance(value, dict):  # postmark nests some detail objects
        value = value.get("Status") or value.get("Type") or ""
    return str(value or "").strip().lower()


def classify_status(provider: str, raw_status: str) -> Tuple[str, str]:
    """Map one provider word onto (ledger status, note). Unknown words stay unknown."""
    key = (provider or "").strip().lower()
    word = (raw_status or "").strip().lower()
    if not word:
        return "unknown", "provider returned no status word"
    mapped = STATUS_MAP.get(key, {}).get(word)
    if mapped is None:
        return "unknown", "unmapped provider status: %s" % word
    return mapped


def read_status(
    reader: Reader,
    provider: str,
    config: Dict[str, str],
    provider_id: str,
    observed_at: str,
) -> Dict[str, Any]:
    """One GET for one message. Always returns a record; never raises on HTTP errors."""
    record: Dict[str, Any] = {
        "provider": provider,
        "provider_id": provider_id,
        "observed_at": observed_at,
        "http_status": None,
        "raw_status": "",
        "status": "unknown",
        "action": "",
        "note": "",
    }
    url = request_url(config, provider_id)
    try:
        status_code, body = reader(url)
    except DeliveryError as exc:
        record["note"] = "read failed: %s" % exc.detail.get("reason", "unknown")
        return record

    record["http_status"] = status_code
    if status_code in {401, 403}:
        record["note"] = "provider refused the key: unknown, not a bounce"
        return record
    if status_code == 404:
        record["note"] = "provider has no record of this id: unknown, not a bounce"
        return record
    if status_code == 429 or status_code >= 500:
        record["note"] = "provider unavailable (%d): unknown, not a bounce" % status_code
        return record
    if status_code >= 400:
        record["note"] = "provider error %d: unknown" % status_code
        return record

    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        record["note"] = "provider body was not JSON: unknown"
        return record
    if not isinstance(payload, dict):
        record["note"] = "provider body was not an object: unknown"
        return record

    raw_status = _status_word(payload, config.get("status_field", "last_event"))
    ledger_status, note = classify_status(provider, raw_status)
    record["raw_status"] = raw_status
    record["status"] = ledger_status
    record["action"] = ledger_status if ledger_status in RECORDABLE else ""
    record["note"] = note
    return record


def summarise(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"delivered": 0, "bounced": 0, "pending": 0, "sent": 0, "unknown": 0}
    for record in records:
        status = str(record.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts
