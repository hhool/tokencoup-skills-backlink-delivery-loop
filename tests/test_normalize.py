import unittest
from datetime import datetime, timezone

from bdl.adapters.prose import load_prose_contacts
from bdl.adapters.snapshot import load_snapshot
from bdl.errors import PolicyError
from bdl.services.normalize import assert_outbound_copy, build_rows
from tests import HISTORY, PROSE, SNAPSHOT

import json

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


class NormalisationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        history = json.loads(HISTORY.read_text(encoding="utf-8"))["rows"]
        live = {"already-live.com"}
        cls.rows = build_rows(
            load_snapshot(SNAPSHOT),
            load_prose_contacts(PROSE),
            "https://soundbuttons.cc",
            live,
            history,
            NOW,
        )
        cls.by_domain = {row.target_domain: row for row in cls.rows}

    def test_candidate_url_is_never_used_as_target_url(self):
        row = self.by_domain["beebom.com"]
        self.assertEqual(row.candidate_url, "https://beebom.com")
        self.assertEqual(row.target_url, "https://beebom.com/blog/soundboard-review")
        self.assertNotEqual(row.candidate_url, row.target_url)

    def test_ready_rows_keep_a_human_gate(self):
        for domain in ("beebom.com", "assethoard.com", "toxigon.com"):
            self.assertEqual(self.by_domain[domain].status, "ready_for_review")

    def test_missing_landing_page_is_a_gap(self):
        row = self.by_domain["launchingnext.com"]
        self.assertEqual(row.status, "needs_enrichment")
        self.assertIsNone(row.target_url)
        self.assertTrue(any("target_url" in gap for gap in row.gaps))

    def test_reciprocal_is_excluded(self):
        row = self.by_domain["reciprocal-example.com"]
        self.assertEqual(row.status, "blocked")
        self.assertTrue(any("white-hat" in reason for reason in row.reasons))

    def test_competitor_and_acquired_are_blocked(self):
        self.assertEqual(self.by_domain["competitor-example.com"].status, "blocked")
        self.assertEqual(self.by_domain["acquired-example.com"].status, "blocked")

    def test_repeat_window_defers(self):
        self.assertEqual(self.by_domain["defer-example.com"].status, "deferred")

    def test_live_link_blocks(self):
        self.assertEqual(self.by_domain["already-live.com"].status, "blocked")

    def test_idempotency_key_is_stable(self):
        first = {r.prospect_id: r.idempotency_key for r in self.rows}
        second = {r.prospect_id: r.idempotency_key for r in build_rows(
            load_snapshot(SNAPSHOT),
            load_prose_contacts(PROSE),
            "https://soundbuttons.cc",
            {"already-live.com"},
            json.loads(HISTORY.read_text(encoding="utf-8"))["rows"],
            NOW,
        )}
        self.assertEqual(first, second)
        self.assertEqual(len(set(first.values())), len(first))

    def test_outbound_copy_rejects_cjk_and_placeholders(self):
        with self.assertRaises(PolicyError):
            assert_outbound_copy("Hello", "\u4e2d\u6587\u6b63\u6587")
        with self.assertRaises(PolicyError):
            assert_outbound_copy("{{name}}", "Hello")
        assert_outbound_copy("About your soundboard page", "We liked your resource list.")


if __name__ == "__main__":
    unittest.main()
