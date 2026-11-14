"""L4 observation: fetch a target page and decide whether a backlink is live.

This is the "local detection" half of the original goal - confirm whether a
placed link is actually present on the third-party page. It is strictly read-only:
a single GET, no authentication, no cookies, and absolutely no transmission.

The network call is isolated behind a ``Fetcher`` callable so every branch of the
classification logic is unit-tested offline. The default fetcher uses urllib and
refuses to route through any proxy (a localhost/loopback proxy previously caused
silent 502s).

Classification follows the window rules: a 403, timeout or any fetch failure is
``unknown`` (never ``absent``); only a successful 2xx fetch with no anchor to the
own host is ``absent``; an anchor to the own host is ``found``.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, List, Optional, Tuple

from ..errors import DeliveryError
from ..models import normalize_domain

Fetcher = Callable[[str], Tuple[int, str]]
"""A fetcher returns (http_status, body) or raises FetchError."""


class FetchError(Exception):
    def __init__(self, kind: str, status: Optional[int] = None, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind  # "timeout" | "robots" | "http"
        self.status = status


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: List[dict] = []
        self._in_a = False
        self._href: Optional[str] = None
        self._rel: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag != "a":
            return
        meta = dict(attrs)
        self._in_a = True
        self._href = meta.get("href")
        self._rel = (meta.get("rel") or "").lower()
        self._text = []

    def handle_endtag(self, tag) -> None:
        if tag == "a" and self._in_a:
            self.anchors.append({
                "href": self._href,
                "rel": self._rel or "",
                "anchor_text": "".join(self._text).strip(),
            })
            self._in_a = False
            self._href = None
            self._rel = None
            self._text = []

    def handle_data(self, data) -> None:
        if self._in_a:
            self._text.append(data)


def find_backlink(html: str, own_url: str) -> Optional[dict]:
    """Return the first anchor whose host matches the own host, else None."""
    own_host = normalize_domain(own_url)
    if not own_host:
        return None
    parser = _AnchorParser()
    parser.feed(html or "")
    for anchor in parser.anchors:
        href = (anchor.get("href") or "").strip()
        if not href.startswith("http://") and not href.startswith("https://"):
            continue  # relative or in-page anchors cannot carry a cross-host link
        if normalize_domain(href) == own_host:
            return anchor
    return None


def default_fetcher() -> Fetcher:
    """Build a proxy-free urllib fetcher for real observation runs."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(url: str) -> Tuple[int, str]:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "bdl-observe/0.1 (+local backlink verification)"},
        )
        try:
            with opener.open(request, timeout=20) as response:
                return response.getcode(), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise FetchError("robots", exc.code)
            raise FetchError("http", exc.code)
        except Exception:
            raise FetchError("timeout")

    return fetch


def observe_target(fetcher: Fetcher, target_url: str, own_url: str) -> dict:
    """Run one L4 observation and return a structured classification record."""
    try:
        status, body = fetcher(target_url)
    except FetchError as exc:
        return {
            "target_url": target_url,
            "own_url": own_url,
            "http_status": exc.status,
            "outcome": "unknown",
            "found_href": None,
            "rel": None,
            "anchor_text": None,
            "evidence": "fetch failed: %s" % exc.kind,
        }

    found = find_backlink(body, own_url)
    if found:
        return {
            "target_url": target_url,
            "own_url": own_url,
            "http_status": status,
            "outcome": "found",
            "found_href": found.get("href"),
            "rel": found.get("rel"),
            "anchor_text": found.get("anchor_text"),
            "evidence": "anchor to own host found on page",
        }
    return {
        "target_url": target_url,
        "own_url": own_url,
        "http_status": status,
        "outcome": "absent",
        "found_href": None,
        "rel": None,
        "anchor_text": None,
        "evidence": "page fetched (2xx) but no anchor to own host",
    }
