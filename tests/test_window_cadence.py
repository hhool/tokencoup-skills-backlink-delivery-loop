import unittest
from datetime import datetime, timezone

from bdl.services import cadence, window

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
HISTORY = [
    {"target_domain": "a.com", "event": "send", "sent_at": "2026-11-14T09:00:00Z"},
    {"target_domain": "a.com", "event": "send", "sent_at": "2026-10-01T09:00:00Z"},
    {"target_domain": "b.com", "event": "send", "sent_at": "2026-11-14T08:00:00Z"},
]


class WindowTests(unittest.TestCase):
    def test_plan_uses_five_offsets(self):
        schedule = window.plan("2026-11-14T12:00:00Z")
        self.assertEqual(len(schedule), 5)
        self.assertEqual(schedule[0], "2026-11-15T12:00:00Z")

    def test_plan_is_capped(self):
        self.assertEqual(len(window.plan("2026-11-14T12:00:00Z", 2)), 2)

    def test_next_check_due_is_future(self):
        due = window.next_check_due(window.plan("2026-11-14T12:00:00Z"), NOW)
        self.assertEqual(due, "2026-11-15T12:00:00Z")

    def test_unknown_is_not_absent(self):
        self.assertEqual(window.classify("absent", 403), "unknown")
        self.assertEqual(window.classify("timeout", 200), "unknown")
        self.assertEqual(window.classify("absent", 200), "absent")
        self.assertEqual(window.classify("found", 200), "found")

    def test_short_window_never_declares_failure(self):
        self.assertEqual(window.terminal_state([{"outcome": "absent"}]), "awaiting_review")
        self.assertEqual(
            window.terminal_state([{"outcome": "found"}]),
            "pending_human_confirmation",
        )


class CadenceTests(unittest.TestCase):
    def test_quota_counts_by_domain(self):
        result = cadence.quota(HISTORY, NOW)
        self.assertEqual(result["ceiling"], 100)
        self.assertEqual(result["per_domain"].get("a.com"), 1)
        self.assertEqual(result["per_domain"].get("b.com"), 1)

    def test_ninety_day_window_closes(self):
        opened, reason = cadence.window_open("a.com", HISTORY, NOW)
        self.assertFalse(opened)
        self.assertIn("repeat window", reason)

    def test_unrelated_domain_is_open(self):
        opened, _ = cadence.window_open("c.com", HISTORY, NOW)
        self.assertTrue(opened)

    def test_used_in_window(self):
        self.assertEqual(cadence.used_in_window("a.com", HISTORY, NOW), 1)


if __name__ == "__main__":
    unittest.main()
