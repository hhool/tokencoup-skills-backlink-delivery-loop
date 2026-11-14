"""Adapter: read replies from files, never from a network connection.

A reply is evidence, and evidence arrives as bytes the operator already has: a
``.eml`` the mail client exported, an ``.mbox`` archive, a folder of exports.
Parsing happens locally with the standard library; nothing here opens a socket.

The parsed form keeps what classification needs and nothing else:

  * headers that tie a message to a target (``In-Reply-To``, ``References``, ``From``);
  * the plain-text body, with HTML reduced to text rather than skipped - most
    business mail is HTML-only, and skipping it would classify on the signature;
  * delivery status notifications, because a bounce read from the mailbox is the
    same L2 fact a provider API would report, with the DSN ``Status`` code attached.
"""

from __future__ import annotations

import email
import email.policy
import mailbox as mboxlib
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Union

_DSN_REPORT_TYPE = re.compile(r"delivery-status", re.I)
_DSN_AGENT = re.compile(r"mailer-?daemon|postmaster|mail delivery", re.I)
_DSN_STATUS = re.compile(r"^(\d+\.\d+\.\d+)", re.M)


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())


def _html_to_text(body: str) -> str:
    parser = _TextOnly()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        return body
    return "\n".join(parser.parts)


def _decode(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _part_text(part: Any) -> str:
    try:
        payload = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            payload = payload.decode(charset, "replace")
    if not isinstance(payload, str):
        return ""
    if part.get_content_type() == "text/html":
        return _html_to_text(payload)
    return payload


def parse_message(message: Any, source: str = "") -> Dict[str, Any]:
    """One parsed message: headers, text body, and DSN facts if it is a bounce."""
    _, address = parseaddr(str(message.get("From") or ""))
    subject = _decode(message.get("Subject"))
    message_id = str(message.get("Message-ID") or "").strip()
    in_reply_to = str(message.get("In-Reply-To") or "").strip()
    references = str(message.get("References") or "").strip()

    date = ""
    raw_date = message.get("Date")
    if raw_date:
        try:
            moment = parsedate_to_datetime(str(raw_date))
            date = moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            date = ""

    body_parts: List[str] = []
    report_type = ""
    dsn_status = ""
    for part in message.walk():
        content_type = part.get_content_type()
        # A delivery-status part reports multipart but carries the per-recipient
        # facts in its own sub-blocks, so it is read before the multipart skip.
        if content_type == "message/delivery-status":
            report_type = "delivery-status"
            for block in part.get_payload() or []:
                try:
                    for key, value in block.items():
                        if str(key).lower() == "status" and not dsn_status:
                            dsn_status = str(value).strip()
                except AttributeError:
                    continue
            continue
        if part.is_multipart():
            if _DSN_REPORT_TYPE.search(str(part.get_param("report-type") or "")):
                report_type = "delivery-status"
            continue
        if content_type in {"text/plain", "text/html"}:
            text = _part_text(part)
            if text.strip():
                body_parts.append(text.strip())

    text = "\n\n".join(body_parts)
    from_value = str(message.get("From") or "")
    is_dsn = bool(report_type) or bool(_DSN_AGENT.search(from_value))
    if is_dsn and not dsn_status:
        found = _DSN_STATUS.search(text)
        dsn_status = found.group(1) if found else ""

    return {
        "source_file": source,
        "from": from_value,
        "address": address,
        "subject": subject,
        "date": date,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "text": text[:100_000],
        "is_dsn": is_dsn,
        "dsn_status": dsn_status,
    }


def read_messages(path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read one ``.eml``, one ``.mbox``, or a directory of ``.eml`` exports."""
    target = Path(path)
    if not target.exists():
        return []
    parsed: List[Dict[str, Any]] = []
    if target.is_dir():
        for item in sorted(target.iterdir()):
            if item.suffix.lower() in {".eml", ".mbox"}:
                parsed.extend(read_messages(item))
        return parsed
    if target.suffix.lower() == ".mbox":
        box = mboxlib.mbox(str(target))
        try:
            for message in box:
                parsed.append(parse_message(message, source=str(target)))
        finally:
            box.close()
        return parsed
    data = target.read_bytes()
    message = email.message_from_bytes(data, policy=email.policy.default)
    parsed.append(parse_message(message, source=str(target)))
    return parsed
