"""Offline tests for the L4 observer. The fetcher is always stubbed."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from bdl.services.observe import FetchError, find_backlink, observe_target


def _fetcher(status: int, body: str):
    def _fetch(url: str):
        return status, body
    return _fetch


class TestFindBacklink(unittest.TestCase):
    def test_matches_absolute_anchor_to_own_host(self):
        html = '<a href="https://soundbuttons.cc/x" rel="nofollow">SoundButtons</a>'
        hit = find_backlink(html, "https://soundbuttons.cc")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["rel"], "nofollow")
        self.assertEqual(hit["anchor_text"], "SoundButtons")

    def test_ignores_other_host(self):
        html = '<a href="https://example.com/x">other</a>'
        self.assertIsNone(find_backlink(html, "https://soundbuttons.cc"))

    def test_skips_relative_href(self):
        html = '<a href="/internal">self</a>'
        self.assertIsNone(find_backlink(html, "https://soundbuttons.cc"))

    def test_www_prefix_is_normalized(self):
        html = '<a href="https://www.soundbuttons.cc/y">x</a>'
        hit = find_backlink(html, "https://soundbuttons.cc")
        self.assertIsNotNone(hit)


class TestObserveTarget(unittest.TestCase):
    def test_found_on_2xx(self):
        html = '<p><a href="https://soundbuttons.cc/launch">launch</a></p>'
        out = observe_target(_fetcher(200, html), "https://host.com/p", "https://soundbuttons.cc")
        self.assertEqual(out["outcome"], "found")
        self.assertEqual(out["http_status"], 200)
        self.assertEqual(out["found_href"], "https://soundbuttons.cc/launch")

    def test_absent_on_2xx_no_anchor(self):
        html = "<p>no links here</p>"
        out = observe_target(_fetcher(200, html), "https://host.com/p", "https://soundbuttons.cc")
        self.assertEqual(out["outcome"], "absent")
        self.assertIsNone(out["found_href"])

    def test_unknown_on_timeout(self):
        def _boom(url: str):
            raise FetchError("timeout")
        out = observe_target(_boom, "https://host.com/p", "https://soundbuttons.cc")
        self.assertEqual(out["outcome"], "unknown")
        self.assertEqual(out["evidence"], "fetch failed: timeout")

    def test_unknown_on_403_robots(self):
        def _boom(url: str):
            raise FetchError("robots", 403)
        out = observe_target(_boom, "https://host.com/p", "https://soundbuttons.cc")
        self.assertEqual(out["outcome"], "unknown")
        self.assertEqual(out["http_status"], 403)

    def test_record_appends_jsonl(self):
        html = '<a href="https://soundbuttons.cc/z">z</a>'
        with tempfile.TemporaryDirectory() as tmp:
            rec = os.path.join(tmp, "obs.jsonl")
            out = observe_target(_fetcher(200, html), "https://host.com/p", "https://soundbuttons.cc")
            Path(rec).write_text(json.dumps(out) + "\n", encoding="utf-8")
            lines = Path(rec).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["outcome"], "found")


if __name__ == "__main__":
    unittest.main()
