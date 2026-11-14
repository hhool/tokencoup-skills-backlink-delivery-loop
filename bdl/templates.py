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


def _render_text(template: str, context: Dict[str, str]) -> str:
    def repl(match: "re.Match[str]") -> str:
        return context.get(match.group(1), "")

    return _PLACEHOLDER.sub(repl, template)


@dataclass
class TemplateSet:
    templates: Dict[str, dict] = field(default_factory=dict)

    @classmethod
    def builtin(cls) -> "TemplateSet":
        return cls(templates=_deep_copy(DEFAULT_TEMPLATES))

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

    def render(self, channel_type: str, context: Dict[str, str]) -> dict:
        channel = self.templates.get(channel_type) or self.templates.get("other", {})
        email = channel.get("email", {})
        form = channel.get("form", {})
        subject_t = str(email.get("subject", ""))
        body_t = str(email.get("body", ""))
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
            "email": {"subject": subject, "body": body},
            "form": {"fields": fields},
        }


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
