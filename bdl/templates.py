"""Configurable outreach templates, keyed by channel type.

A template set defines, per ``channel_type`` (asset, guest_post, directory,
community, journalist, other), the email copy (subject + body) and the form
field map used to fill a third-party submission form. Templates are plain text
with ``{{ variable }}`` placeholders.

Configuration is layered:

  * Built-in ``DEFAULT_TEMPLATES`` ship with the package so the tool works with
    zero setup.
  * A templates directory (``BDL_TEMPLATES_DIR``) may override any channel. A
    file named ``templates.json`` overrides every centre domain; a file named
    ``<center_domain>.json`` overrides only that centre. Files are merged into
    the defaults - you only need to supply the channels you want to change.

Outbound email copy is still subject to R-522: no CJK and no unreplaced
placeholders. Form field values are data, not prose, so only unreplaced
placeholders are rejected there.

This module renders copy. It never sends anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .errors import ConfigError, PolicyError
from .services.normalize import assert_outbound_copy

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Channel types produced by services/normalize.TYPE_MAP.
CHANNEL_TYPES = ("asset", "guest_post", "directory", "community", "journalist", "other")

DEFAULT_TEMPLATES: Dict[str, dict] = {
    "asset": {
        "email": {
            "subject": "Resource suggestion for {{ target_domain }}",
            "body": (
                "Hello,\n\n"
                "I maintain {{ center_domain }} and put together a guide that I think "
                "would fit the resource list on {{ target_domain }}: {{ own_url }}\n\n"
                "If it is useful, a mention would be appreciated. Thank you for your time.\n\n"
                "{{ center_domain }}"
            ),
        },
        "form": {
            "fields": {
                "website": "{{ target_domain }}",
                "resource_url": "{{ own_url }}",
                "comment": "Suggested resource: {{ own_url }}",
            }
        },
    },
    "guest_post": {
        "email": {
            "subject": "Guest post idea for {{ target_domain }}",
            "body": (
                "Hello {{ target_domain }} team,\n\n"
                "I would like to contribute a guest article to {{ target_domain }}. "
                "I run {{ center_domain }} ({{ own_site_base }}) and can provide an "
                "original piece with a relevant link to {{ own_url }}.\n\n"
                "Let me know if you accept guest contributions.\n\n"
                "{{ center_domain }}"
            ),
        },
        "form": {
            "fields": {
                "name": "{{ center_domain }}",
                "email": "{{ contact_email }}",
                "topic": "Guest post proposal: {{ own_url }}",
                "message": "Original article proposal referencing {{ own_url }}.",
            }
        },
    },
    "directory": {
        "email": {
            "subject": "Directory submission: {{ center_domain }}",
            "body": (
                "Hello,\n\n"
                "Please consider listing {{ center_domain }} ({{ own_site_base }}) in your "
                "directory at {{ target_domain }}.\n\n"
                "Kind regards,\n{{ center_domain }}"
            ),
        },
        "form": {
            "fields": {
                "title": "{{ center_domain }}",
                "url": "{{ own_site_base }}",
                "description": "A resource related to {{ target_domain }}.",
            }
        },
    },
    "community": {
        "email": {
            "subject": "Hello from {{ center_domain }}",
            "body": (
                "Hi,\n\n"
                "I wanted to share {{ own_url }} with the {{ target_domain }} community. "
                "It is relevant to your readers and I hope it is welcome.\n\n"
                "{{ center_domain }}"
            ),
        },
        "form": {
            "fields": {
                "url": "{{ own_url }}",
                "note": "Sharing a relevant resource: {{ own_url }}.",
            }
        },
    },
    "journalist": {
        "email": {
            "subject": "Story lead for {{ target_domain }}",
            "body": (
                "Hello,\n\n"
                "I am reaching out from {{ center_domain }} with a story lead that may "
                "interest {{ target_domain }}. Background and sources are at {{ own_url }}.\n\n"
                "Best,\n{{ center_domain }}"
            ),
        },
        "form": {
            "fields": {
                "topic": "Story lead: {{ own_url }}",
                "details": "Reference material at {{ own_url }}.",
            }
        },
    },
    "other": {
        "email": {
            "subject": "Hello from {{ center_domain }}",
            "body": (
                "Hello,\n\n"
                "I would like to share {{ own_url }} with {{ target_domain }}. "
                "Please let me know if it is a fit.\n\n"
                "{{ center_domain }}"
            ),
        },
        "form": {
            "fields": {
                "url": "{{ own_url }}",
                "message": "Sharing {{ own_url }}.",
            }
        },
    },
}


def _missing_keys(template: str, context: Dict[str, str]) -> List[str]:
    return [m.group(1) for m in _PLACEHOLDER.finditer(template) if m.group(1) not in context]


# Alternative bodies per channel. One body sent verbatim to a hundred directories is
# a footprint, not outreach: the copy has to differ in structure, not just in the
# domain name it inserts. `draft` picks the first variant that is not too similar to
# copy already prepared in the same batch.
BODY_VARIANTS: Dict[str, List[str]] = {
    "asset": [
        "Hi {{ target_domain }} team,\n\n"
        "We run {{ center_domain }} and recently published {{ own_url }}, which covers "
        "the same ground as your resource pages. If it fits an existing list, feel free "
        "to add it.\n\n"
        "Thanks for maintaining the collection.\n\n"
        "{{ center_domain }}",
    ],
    "guest_post": [
        "Hello,\n\n"
        "I write about the topics {{ target_domain }} covers and would like to contribute "
        "one original article, with a link back to {{ own_url }}.\n\n"
        "Happy to send an outline first. Would that work?\n\n"
        "{{ center_domain }}",
    ],
    "directory": [
        "Hi,\n\n"
        "{{ center_domain }} ({{ own_site_base }}) may belong in your directory at "
        "{{ target_domain }}. A short description is available on request.\n\n"
        "Thank you.\n\n"
        "{{ center_domain }}",
    ],
    "community": [
        "Hi everyone,\n\n"
        "Long-time reader, first post: {{ own_url }} might be useful to people here "
        "working on the same problem. Sharing in case it helps.\n\n"
        "{{ center_domain }}",
    ],
    "journalist": [
        "Hello,\n\n"
        "A quick lead for {{ target_domain }}: we have data and worked examples on this "
        "at {{ own_url }}. Happy to provide quotes or background.\n\n"
        "Best,\n{{ center_domain }}",
    ],
    "other": [
        "Hello,\n\n"
        "Reaching out about a possible mention on {{ target_domain }}. Background: "
        "{{ own_url }}.\n\n"
        "If this is not your area, a pointer to the right person would help.\n\n"
        "{{ center_domain }}",
    ],
}


def _render_text(template: str, context: Dict[str, str]) -> str:
    def repl(match: "re.Match[str]") -> str:
        value = context.get(match.group(1))
        if value is None:
            # An undefined key must fail loudly: silently writing "" turns a
            # configuration mistake into a blank sentence that still sends.
            raise PolicyError(
                "Template references an undefined variable",
                field=match.group(1),
                rule="R-522",
            )
        return value

    return _PLACEHOLDER.sub(repl, template)


@dataclass
class TemplateSet:
    templates: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def builtin(cls) -> "TemplateSet":
        templates = _deep_copy(DEFAULT_TEMPLATES)
        for channel, variants in BODY_VARIANTS.items():
            if channel in templates and variants:
                templates[channel].setdefault("email", {})["body_variants"] = list(variants)
        return cls(templates=templates)

    @classmethod
    def load(cls, directory: str | Path, center_domain: str = "") -> "TemplateSet":
        base = cls.builtin()
        source = Path(directory)
        if not source.is_dir():
            return base
        overrides: List[Path] = []
        specific = source / (center_domain + ".json")
        if center_domain and specific.is_file():
            overrides.append(specific)
        generic = source / "templates.json"
        if generic.is_file():
            overrides.append(generic)
        for path in overrides:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(
                    "Cannot read template override", path=str(path), reason=str(exc)
                ) from exc
            if not isinstance(payload, dict):
                raise ConfigError("Template override must be an object", path=str(path))
            for channel, value in payload.items():
                if channel not in CHANNEL_TYPES:
                    raise ConfigError(
                        "Unknown channel type in template override",
                        path=str(path),
                        value=channel,
                    )
                if not isinstance(value, dict):
                    raise ConfigError(
                        "Channel template must be an object", path=str(path), value=channel
                    )
                base.templates[channel] = _merge_channel(base.templates.get(channel, {}), value)
        return base

    def body_variants(self, channel_type: str) -> List[str]:
        """Every body this channel can produce, primary first."""
        channel = self.templates.get(channel_type) or self.templates.get("other", {})
        email = channel.get("email", {})
        primary = str(email.get("body", ""))
        extra = [str(item) for item in (email.get("body_variants") or []) if str(item).strip()]
        ordered = [primary] + [item for item in extra if item.strip() and item != primary]
        return [item for item in ordered if item.strip()] or [primary]

    def render(
        self, channel_type: str, context: Dict[str, str], variant: Optional[int] = None
    ) -> dict:
        channel = self.templates.get(channel_type) or self.templates.get("other", {})
        email = channel.get("email", {})
        form = channel.get("form", {})
        subject_t = str(email.get("subject", ""))
        bodies = self.body_variants(channel_type)
        index = 0 if variant is None else max(0, min(int(variant), len(bodies) - 1))
        body_t = bodies[index]
        missing = (
            _missing_keys(subject_t, context)
            + _missing_keys(body_t, context)
            + _missing_keys(" ".join(str(v) for v in (form.get("fields") or {}).values()), context)
        )
        if missing:
            unique = sorted(set(missing))
            raise PolicyError(
                "Template references undefined variables",
                field=", ".join(unique),
                rule="R-522",
            )
        subject = _render_text(subject_t, context)
        body = _render_text(body_t, context)
        assert_outbound_copy(subject, body)  # R-522: no CJK, no leftover {{ }}
        fields: Dict[str, str] = {}
        for name, value in (form.get("fields") or {}).items():
            fields[name] = _render_text(str(value), context)
        return {
            "channel_type": channel_type,
            "variant": index,
            "variant_count": len(bodies),
            "email": {"subject": subject, "body": body},
            "form": {"fields": fields},
        }

    def render_variants(self, channel_type: str, context: Dict[str, str]) -> List[dict]:
        """Every admissible version of this copy, so a caller can pick one that differs.

        A batch needs this: the first draft is always fine in isolation and only
        suspicious next to the nineteen others.
        """
        rendered: List[dict] = []
        seen = set()
        for index in range(len(self.body_variants(channel_type))):
            try:
                candidate = self.render(channel_type, context, variant=index)
            except PolicyError:
                continue
            body = candidate["email"]["body"]
            if body in seen:
                continue
            seen.add(body)
            rendered.append(candidate)
        return rendered


def fingerprint(text: str, size: int = 5) -> set:
    """Word shingles, for comparing two pieces of copy without a dependency."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def similarity(left: str, right: str, size: int = 5) -> float:
    """Jaccard over word shingles: 1.0 means the same copy, 0.0 means unrelated."""
    first, second = fingerprint(left, size), fingerprint(right, size)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _deep_copy(value: dict) -> dict:
    return json.loads(json.dumps(value))


def _merge_channel(base: dict, override: dict) -> dict:
    merged = _deep_copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged
