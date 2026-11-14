"""Adapter: turn operator prose into structured, traceable contact candidates.

Prose is a derived source, never a source of truth. Every candidate carries its
file, line, excerpt and a hash of the source file. A changed hash invalidates the
candidate; a human review is still required before any use (R-530 tier B).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..models import ProseContact, domain_from_text

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FORM_PATH_RE = re.compile(r"(/(?:contact|contact-us|submit|submit-site)[^\s`|)]*)", re.I)
PAGE_HINT_RE = re.compile(r"([a-z0-9.-]+\.[a-z]{2,}/[^\s`|)]*)", re.I)
OWN_HINT_RE = re.compile(r"(/[^\s`|)]+)")
HTTP_URL_RE = re.compile(r"https?://[^\s`|)]+")

# Source-language markers are data, not prose. Kept as escapes so the codebase
# stays ASCII-only; the Chinese mirrors live in *.zh.md.
EXCLUSION_MARKERS = (
    "\u4e0d\u505a", "\u51bb\u7ed3", "\u4e0d\u6295", "\u522b\u518d\u56de\u5934",
    "\u53ea\u6709\u5ba2\u670d", "\u6392\u9664",
    "do not", "excluded", "frozen", "blocked",
)
AMBIGUOUS_MARKERS = (
    "\u8054\u7cfb\u8868\u5355/\u90ae\u7bb1", "\u65e0\u516c\u5f00\u90ae\u7bb1",
    "\u9700\u6ce8\u518c", "\u5f85\u786e\u8ba4",
    "tbd", "unknown", "no public email",
)
_TARGET_HEADERS = ("\u76ee\u6807", "target", "domain")
_OWN_HEADERS = ("\u843d\u5730\u9875", "own", "landing")
_ENTRY_HEADERS = ("\u5165\u53e3", "\u90ae\u7bb1", "email", "entry", "contact")


def _has_any(text: str, markers: Tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def _clean(value: str) -> str:
    return value.replace("`", "").replace("**", "").strip()


def _state_for(section: str, row: str) -> Tuple[str, str]:
    context = section + " " + row
    if _has_any(context, EXCLUSION_MARKERS):
        return "excluded", "exclusion section or marker in source"
    if _has_any(context, AMBIGUOUS_MARKERS):
        return "ambiguous", "placeholder or unresolved entry in source"
    return "candidate", ""


def _target_url_hint(text: str) -> Optional[str]:
    match = HTTP_URL_RE.search(text)
    if match:
        return match.group(0).rstrip(".,;)")
    match = PAGE_HINT_RE.search(text)
    if match:
        return "https://" + match.group(1).rstrip(".,;)")
    return None


def _own_url_hint(text: str) -> Optional[str]:
    match = OWN_HINT_RE.search(_clean(text))
    return match.group(1).rstrip(".,;)") if match else None


def _columns(header: List[str]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for index, cell in enumerate(header):
        lowered = cell.lower()
        for key, markers in (
            ("target", _TARGET_HEADERS),
            ("own", _OWN_HEADERS),
            ("entry", _ENTRY_HEADERS),
        ):
            if key not in found and any(marker in lowered for marker in markers):
                found[key] = index
    return found


def _contact(
    target_text: str,
    entry_text: str,
    own_text: str,
    section: str,
    raw_line: str,
    source_file: str,
    line_number: int,
    content_hash: str,
) -> Optional[ProseContact]:
    domain = domain_from_text(target_text)
    address_match = EMAIL_RE.search(entry_text)
    form_match = FORM_PATH_RE.search(entry_text) or HTTP_URL_RE.search(entry_text)
    form_url = form_match.group(0).rstrip(".,;)") if form_match else None
    if form_url and not (form_url.startswith("http") or form_url.startswith("/")):
        form_url = None
    if not domain and not (address_match or form_url):
        return None
    state, reason = _state_for(section, raw_line)
    return ProseContact(
        target_domain=domain,
        address=address_match.group(0) if address_match else None,
        form_url=form_url,
        target_url_hint=_target_url_hint(target_text),
        own_url_hint=_own_url_hint(own_text),
        state=state,
        reason=reason,
        source_file=source_file,
        line=line_number,
        excerpt=_clean(raw_line)[:240],
        content_hash=content_hash,
    )


def parse_prose(text: str, source_file: str) -> List[ProseContact]:
    content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    lines = text.splitlines()
    contacts: List[ProseContact] = []
    table_lines: Set[int] = set()
    sections: Dict[int, str] = {}
    section = ""

    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("#"):
            section = line.lstrip("#").strip()
        sections[number] = section

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.lstrip().startswith("|"):
            index += 1
            continue

        block: List[Tuple[int, str]] = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            block.append((index + 1, lines[index]))
            table_lines.add(index + 1)
            index += 1

        cells = [
            [cell.strip() for cell in raw.strip().strip("|").split("|")]
            for _, raw in block
        ]
        if len(cells) < 2:
            continue
        columns = _columns(cells[0])
        for row_index in range(1, len(cells)):
            row = cells[row_index]
            if all(set(cell) <= set("-: ") for cell in row):
                continue
            line_number, raw_line = block[row_index]

            def cell(key: str) -> str:
                position = columns.get(key)
                return row[position] if position is not None and position < len(row) else ""

            record = _contact(
                target_text=cell("target"),
                entry_text=cell("entry"),
                own_text=cell("own"),
                section=sections.get(line_number, ""),
                raw_line=raw_line,
                source_file=source_file,
                line_number=line_number,
                content_hash=content_hash,
            )
            if record:
                contacts.append(record)

    for number, line in enumerate(lines, start=1):
        if number in table_lines:
            continue
        match = EMAIL_RE.search(line)
        if not match:
            continue
        state, reason = _state_for(sections.get(number, ""), line)
        domain = domain_from_text(line.replace(match.group(0), ""))
        contacts.append(
            ProseContact(
                target_domain=domain,
                address=match.group(0),
                form_url=None,
                target_url_hint=_target_url_hint(line),
                own_url_hint=None,
                state=state,
                reason=reason or "email found outside a table",
                source_file=source_file,
                line=number,
                excerpt=_clean(line)[:240],
                content_hash=content_hash,
            )
        )

    unique: Dict[Tuple[str, str, str, str], ProseContact] = {}
    for record in contacts:
        key = (record.target_domain, record.address or "", record.form_url or "", record.state)
        unique.setdefault(key, record)
    return list(unique.values())


def load_prose_contacts(prose_dir: str | Path) -> List[ProseContact]:
    directory = Path(prose_dir)
    if not directory.is_dir():
        return []
    results: List[ProseContact] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() not in {".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        results.extend(parse_prose(text, path.name))
    return results
