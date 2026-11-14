"""Data contracts used across the layers. Plain records, no framework types."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

_HOST_RE = re.compile(r"^(?:https?://)?([^/\s:|`]+)(?::\d+)?", re.I)
_DOMAIN_RE = re.compile(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)


def normalize_domain(value: str) -> str:
    """Reduce a URL, host or bare domain to a comparable registrable-ish host."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    match = _HOST_RE.match(text)
    host = match.group(1) if match else text
    host = host.strip(".").split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_from_text(value: str) -> str:
    match = _DOMAIN_RE.search(value or "")
    return normalize_domain(match.group(1)) if match else ""


def as_dict(record: object) -> dict:
    return asdict(record)


# A domain is not "either a form or an email": the delivery channel is a property
# of the landing page, and one domain may offer both. Counting and cooldowns are
# keyed on (center_domain, target_domain, channel), never on the domain alone.
DELIVERY_CHANNELS = ("form", "email")


def delivery_channels(submission_url: Optional[str], contact_email: Optional[str]) -> List[str]:
    found: List[str] = []
    if submission_url:
        found.append("form")
    if contact_email:
        found.append("email")
    return found


@dataclass
class Snapshot:
    center_domain: str
    schema_version: str
    generated_at: str
    source: dict
    prospects: List["Prospect"]
    file_hash: str


@dataclass
class Prospect:
    domain: str
    url: str
    type_key: str
    priority: int
    http_status: Optional[int]
    acquired: bool
    is_competitor: bool
    is_gap: bool
    note: str = ""
    note_en: str = ""
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None

    @property
    def key(self) -> str:
        return normalize_domain(self.domain)


@dataclass
class ProseContact:
    target_domain: str
    address: Optional[str]
    form_url: Optional[str]
    target_url_hint: Optional[str]
    own_url_hint: Optional[str]
    state: str
    reason: str
    source_file: str
    line: int
    excerpt: str
    content_hash: str


@dataclass
class ManifestRow:
    prospect_id: str
    center_domain: str
    target_domain: str
    candidate_url: str
    target_url: Optional[str]
    submission_url: Optional[str]
    own_url: Optional[str]
    contact_email: Optional[str]
    contact_state: str
    channel_type: str
    type_key: str
    status: str
    reasons: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    idempotency_key: str = ""
    content: Optional[dict] = None
    channels: List[str] = field(default_factory=list)


def absolute_url(value: str, base: str) -> Optional[str]:
    """Resolve a form path or page hint against a site base."""
    text = (value or "").strip().strip("`").strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return base.rstrip("/") + text if base else None
    parsed = urlparse("https://" + text)
    if parsed.netloc and "." in parsed.netloc:
        return "https://" + text
    return None
