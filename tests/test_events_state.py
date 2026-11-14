import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bdl.adapters.events import append_event, event_id, load_events, normalise_legacy
from bdl.services.record import build_event, policy_notice
from bdl.services.state import merge_seeds, project, seed_rows_from_manifest

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
CENTER = "soundbuttons.cc"


def ev(target, channel, action, at, **kwargs):
    return build_event(CENTER, target, channel, action, at=at, **kwargs)


def state_of(result, target, channel):
    for row in result["rows"]:
        if row["target_domain"] == target and row["channel"] == channel:
            return row
    raise AssertionError("no row for %s/%s" % (target, channel))


class EventLogTests(unittest.TestCase):
    def test_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            event = ev("a.com", "email", "sent", "2026-11-01T09:00:00Z",
                       address="e@a.com", provider_id="msg_1")
            _, first = append_event(path, event)
            _, second = append_event(path, event)
            self.assertTrue(first)
            self.assertFalse(second, "a repeated event id must be refused")
            self.assertEqual(len(load_events([path])), 1)

    def test_event_id_is_deterministic(self):
        self.assertEqual(
            event_id("c.com", "a.com", "email", "sent", "2026-11-01T09:00:00Z"),
            event_id("c.com", "a.com", "email", "sent", "2026-11-01T09:00:00Z"),
        )
        self.assertNotEqual(
            event_id("c.com", "a.com", "email", "sent", "2026-11-01T09:00:00Z"),
            event_id("c.com", "a.com", "email", "sent", "2026-11-02T09:00:00Z"),
        )

    def test_legacy_history_becomes_events(self):
        rows = [
            {"event": "send", "target_domain": "a.com", "center_domain": CENTER,
             "sent_at": "2026-11-01T09:00:00Z"},
            {"event": "submit", "target_domain": "b.com", "center_domain": CENTER,
             "sent_at": "2026-11-01T09:00:00Z"},
        ]
        mapped = normalise_legacy(rows)
        self.assertEqual(len(mapped), 2)
        self.assertEqual({item["channel"] for item in mapped}, {"email", "form"})
        self.assertTrue(all(item["legacy"] for item in mapped))


class StateTests(unittest.TestCase):
    def test_send_then_delivery_then_reply(self):
        events = [
            ev("a.com", "email", "queued", "2026-11-01T08:00:00Z"),
            ev("a.com", "email", "sent", "2026-11-01T09:00:00Z",
               address="e@a.com", provider_id="msg_1"),
            ev("a.com", "email", "delivered", "2026-11-01T10:00:00Z", address="e@a.com"),
        ]
        result = project(events, now=NOW, center_domain=CENTER)
        row = state_of(result, "a.com", "email")
        self.assertEqual(row["state"], "delivered")
        self.assertEqual(row["provider_id"], "msg_1")
        self.assertEqual(row["window"]["due_at"], "2026-12-01T09:00:00Z")
        self.assertFalse(row["window"]["expired"])

    def test_send_without_receipt_becomes_unanswered_not_failed(self):
        events = [ev("a.com", "email", "sent", "2026-10-01T09:00:00Z",
                     address="e@a.com", provider_id="msg_1")]
        result = project(events, now=NOW, center_domain=CENTER)
        row = state_of(result, "a.com", "email")
        self.assertEqual(row["state"], "unanswered_no_receipt")
        self.assertIn("unknown, not failed", " ".join(row["notes"]))

    def test_bounce_creates_address_level_cooldown(self):
        events = [
            ev("a.com", "email", "sent", "2026-11-01T09:00:00Z",
               address="e@a.com", provider_id="msg_1"),
            ev("a.com", "email", "bounced", "2026-11-02T09:00:00Z", address="e@a.com"),
        ]
        result = project(events, now=NOW, center_domain=CENTER)
        row = state_of(result, "a.com", "email")
        self.assertEqual(row["state"], "contact_lost")
        self.assertEqual(row["address"], "e@a.com")
        self.assertIn("2027-05-01", " ".join(row["notes"]))

    def test_cooldown_expires_and_allows_retry(self):
        events = [
            ev("a.com", "email", "sent", "2026-01-01T09:00:00Z",
               address="e@a.com", provider_id="msg_1"),
            ev("a.com", "email", "bounced", "2026-01-02T09:00:00Z", address="e@a.com"),
        ]
        result = project(events, now=NOW, contact_lost_days=180, center_domain=CENTER)
        row = state_of(result, "a.com", "email")
        self.assertEqual(row["state"], "unanswered")
        self.assertIn("cooldown elapsed", " ".join(row["notes"]))

    def test_declined_absorbs_later_noise(self):
        events = [
            ev("a.com", "email", "reply", "2026-11-01T09:00:00Z",
               address="e@a.com", reply_class="no"),
            ev("a.com", "email", "bounced", "2026-11-02T09:00:00Z", address="e@a.com"),
        ]
        result = project(events, now=NOW, center_domain=CENTER)
        self.assertEqual(state_of(result, "a.com", "email")["state"], "declined")

    def test_paid_reply_is_refused_but_recorded(self):
        event = ev("a.com", "email", "reply", "2026-11-01T09:00:00Z",
                   address="e@a.com", reply_class="pay")
        self.assertEqual(policy_notice(event)["rule"], "R-510")
        result = project([event], now=NOW, center_domain=CENTER)
        self.assertEqual(state_of(result, "a.com", "email")["state"], "declined_paid")

    def test_form_submission_needs_the_window_then_stays_submitted(self):
        events = [ev("b.com", "form", "submitted", "2026-11-10T09:00:00Z",
                     evidence="https://b.com/thanks")]
        result = project(events, now=NOW, center_domain=CENTER)
        row = state_of(result, "b.com", "form")
        self.assertEqual(row["state"], "submitted")
        self.assertEqual(row["evidence_count"], 1)
        self.assertFalse(row["window"]["expired"])

        # Long after the window, an unseen link is still not a failure (R-512).
        later = datetime(2027, 1, 15, 12, 0, tzinfo=timezone.utc)
        expired = project(events, now=later, center_domain=CENTER)
        row = state_of(expired, "b.com", "form")
        self.assertEqual(row["state"], "submitted")
        self.assertIn("absence is not failure", " ".join(row["notes"]))

    def test_observation_makes_it_live(self):
        events = [ev("b.com", "form", "submitted", "2026-11-01T09:00:00Z",
                     evidence="https://b.com/thanks")]
        observations = [{"center_domain": CENTER, "target_domain": "b.com",
                         "outcome": "found", "observed_at": "2026-11-05T09:00:00Z"}]
        result = project(events, observations, now=NOW, center_domain=CENTER)
        row = state_of(result, "b.com", "form")
        self.assertEqual(row["state"], "live")
        self.assertTrue(row["live"])
        self.assertEqual(result["summary"]["succeeded"], 1)

    def test_live_with_no_event_still_counts(self):
        observations = [{"center_domain": CENTER, "target_domain": "z.com",
                         "outcome": "found", "observed_at": "2026-11-05T09:00:00Z"}]
        result = project([], observations, now=NOW, center_domain=CENTER)
        self.assertEqual(result["summary"]["succeeded"], 1)
        self.assertEqual(result["rows"][0]["channel"], "unknown")

    def test_seeds_skip_blocked_rows_and_use_unknown_channel(self):
        manifest = {
            "center_domain": CENTER,
            "rows": [
                {"target_domain": "ok.com", "status": "needs_enrichment", "channels": []},
                {"target_domain": "no.com", "status": "blocked", "channels": ["email"]},
                {"target_domain": "two.com", "status": "ready_for_review",
                 "channels": ["form", "email"]},
            ],
        }
        seeds = seed_rows_from_manifest(manifest)
        self.assertEqual([(s["target_domain"], s["channel"]) for s in seeds],
                         [("ok.com", "unknown"), ("two.com", "form"), ("two.com", "email")])

        result = project(merge_seeds([], seeds), now=NOW, center_domain=CENTER)
        self.assertEqual(result["summary"]["untouched"], 3)

    def test_summary_splits_delivery_from_outcome(self):
        events = [
            ev("a.com", "email", "sent", "2026-11-14T09:00:00Z",
               address="e@a.com", provider_id="msg_1"),
            ev("b.com", "email", "bounced", "2026-11-14T09:00:00Z", address="e@b.com"),
        ]
        result = project(events, now=NOW, center_domain=CENTER)
        self.assertEqual(result["summary"]["in_delivery"], 1)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["summary"]["succeeded"], 0)


if __name__ == "__main__":
    unittest.main()
