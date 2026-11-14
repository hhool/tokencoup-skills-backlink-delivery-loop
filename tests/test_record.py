import unittest

from bdl.errors import DeliveryError
from bdl.services.record import build_event, policy_notice

CENTER = "soundbuttons.cc"


class RecordValidationTests(unittest.TestCase):
    def test_send_requires_provider_id(self):
        with self.assertRaises(DeliveryError):
            build_event(CENTER, "a.com", "email", "sent", address="e@a.com")

    def test_submission_requires_evidence(self):
        with self.assertRaises(DeliveryError):
            build_event(CENTER, "a.com", "form", "submitted")

    def test_email_events_require_an_address(self):
        with self.assertRaises(DeliveryError):
            build_event(CENTER, "a.com", "email", "bounced")

    def test_reply_requires_a_class(self):
        with self.assertRaises(DeliveryError):
            build_event(CENTER, "a.com", "email", "reply", address="e@a.com")

    def test_unknown_channel_and_action_are_rejected(self):
        with self.assertRaises(DeliveryError):
            build_event(CENTER, "a.com", "carrier_pigeon", "sent")
        with self.assertRaises(DeliveryError):
            build_event(CENTER, "a.com", "email", "teleported")

    def test_timestamp_is_normalised_to_utc(self):
        event = build_event(CENTER, "a.com", "form", "submitted",
                            at="2026-11-15T17:00:00+08:00", evidence="x.png")
        self.assertEqual(event["at"], "2026-11-15T09:00:00Z")

    def test_domains_are_normalised(self):
        event = build_event("SoundButtons.CC", "https://www.A.com/path", "form", "queued")
        self.assertEqual(event["center_domain"], "soundbuttons.cc")
        self.assertEqual(event["target_domain"], "a.com")

    def test_paid_and_reciprocal_are_flagged_not_blocked(self):
        for reply_class in ("pay", "reciprocal"):
            event = build_event(CENTER, "a.com", "email", "reply",
                                address="e@a.com", reply_class=reply_class)
            self.assertEqual(event["compliance"], "refused")
            notice = policy_notice(event)
            self.assertTrue(notice["refused"])
            self.assertEqual(notice["rule"], "R-510")

    def test_plain_reply_has_no_notice(self):
        event = build_event(CENTER, "a.com", "email", "reply",
                            address="e@a.com", reply_class="yes")
        self.assertEqual(policy_notice(event), {})

    def test_queued_needs_no_evidence(self):
        event = build_event(CENTER, "a.com", "form", "queued")
        self.assertEqual(event["action"], "queued")
        self.assertNotIn("evidence", event)


if __name__ == "__main__":
    unittest.main()
