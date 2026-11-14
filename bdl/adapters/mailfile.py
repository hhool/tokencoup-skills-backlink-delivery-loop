"""Adapter: write a mail as a file. Nothing here opens a connection.

A ``.eml`` file is the smallest honest unit of outbound work: it is a message a
human can read, inspect in any mail client, and send through whatever channel the
operator already trusts. Producing one is preparation; sending it is transmission,
and transmission stays behind the review gate.

Provenance travels in the headers (``X-BDL-*``) so a message found in a Sent folder
months later still says which target domain it belonged to and which idempotency
key it carried.
"""

from __future__ import annotations

import datetime as dt
import re
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Dict, Optional

_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")


def safe_filename(value: str) -> str:
    cleaned = _SAFE_NAME.sub("-", (value or "").strip().lower()).strip("-.")
    return cleaned or "message"


def build_message(
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    from_name: str = "",
    headers: Optional[Dict[str, str]] = None,
    now: Optional[dt.datetime] = None,
    message_id: str = "",
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = ("%s <%s>" % (from_name, from_addr)) if from_name else from_addr
    message["To"] = to_addr
    message["Subject"] = subject
    message["Date"] = format_datetime(now or dt.datetime.now(dt.timezone.utc))
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(body, subtype="plain", charset="utf-8")
    for key, value in (headers or {}).items():
        if value:
            message[key] = value
    return message


def write_eml(
    out_dir: str | Path,
    target_domain: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    from_name: str = "",
    headers: Optional[Dict[str, str]] = None,
    now: Optional[dt.datetime] = None,
    message_id: str = "",
) -> Path:
    """Write one ``.eml`` and return its path. Refuses to overwrite silently."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    message = build_message(
        from_addr, to_addr, subject, body, from_name=from_name,
        headers=headers, now=now, message_id=message_id,
    )
    path = folder / (safe_filename(target_domain) + ".eml")
    path.write_bytes(bytes(message))
    return path
