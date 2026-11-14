"""Offline tests for the receipt poll: no network, no credentials, no transmission."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bdl.adapters.events import load_events
from bdl.adapters.provider_events import provider_config
from bdl.services.receipts import (
    pending_sends,
    plan_events,
    poll_receipts,
    resolved_ids,
)
from bdl.services.record import build_event
from bdl.services.state import project

NOW = datetime(2026, 11, 15, 0, 0, 0, tzinfo=timezone.utc)
CONFIG = provider_config("resend")


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sent(provider_id="re_1", target="host.com", address="ed@host.com", days_ago=1, center="soundbuttons.cc"):
    return build_event(
        center_domain=center,
        target_domain=target,
        channel="email",
        action="sent",
        at=_iso(days_ago),
        address=address,
        provider_id=provider_id,
    )


def _reader(mapping):
    """A stub reader: provider id -> (http_status, body)."""
    def _read(url):
        provider_id = url.rstrip("/").rsplit("/", 1)[-1]
        if provider_id not in mapping:
            return 404, json.dumps({"error": "not found"})
        value = mapping[provider_id]
        if isinstance(value, tuple):
            return value
        return 200, json.dumps({"last_event": value})
    return _read


class TestPendingSends(unittest.TestCase):
    def test_picks_a_recent_named_send(self):
        polls, already = pending_sends([_sent()], NOW, 30)
        self.assertEqual(len(polls), 1)
        self.assertEqual(polls[0]["provider_id"], "re_1")
        self.assertEqual(polls[0]["address"], "ed@host.com")
        self.assertEqual(already, 0)

    def test_skips_a_send_outside_the_window(self):
        polls, _ = pending_sends([_sent(days_ago=45)], NOW, 30)
        self.assertEqual(polls, [])

    def test_skips_an_already_resolved_send(self):
        events = [
            _sent(),
            build_event("soundbuttons.cc", "host.com", "email", "delivered",
                        at=_iso(0), address="ed@host.com", provider_id="re_1"),
        ]
        polls, already = pending_sends(events, NOW, 30)
        self.assertEqual(polls, [])
        self.assertEqual(already, 1)

    def test_skips_a_send_without_provider_id(self):
        event = {"kind": "delivery_event", "at": _iso(1), "center_domain": "soundbuttons.cc",
                 "target_domain": "host.com", "channel": "email", "action": "sent",
                 "address": "ed@host.com"}
        polls, _ = pending_sends([event], NOW, 30)
        self.assertEqual(polls, [])

    def test_polls_each_provider_id_once(self):
        events = [_sent(provider_id="re_1", days_ago=5), _sent(provider_id="re_1", days_ago=2)]
        polls, _ = pending_sends(events, NOW, 30)
        self.assertEqual(len(polls), 1)
        self.assertEqual(polls[0]["sent_at"], _iso(2))

    def test_limit_truncates(self):
        events = [_sent(provider_id="re_%d" % i) for i in range(5)]
        polls, _ = pending_sends(events, NOW, 30, limit=2)
        self.assertEqual(len(polls), 2)

    def test_sorted_oldest_first(self):
        events = [_sent(provider_id="re_new", days_ago=1), _sent(provider_id="re_old", days_ago=9)]
        polls, _ = pending_sends(events, NOW, 30)
        self.assertEqual([p["provider_id"] for p in polls], ["re_old", "re_new"])


class TestPlanEvents(unittest.TestCase):
    def test_delivered_becomes_an_event(self):
        records = [{"center_domain": "soundbuttons.cc", "target_domain": "host.com",
                    "channel": "email", "address": "ed@host.com", "provider_id": "re_1",
                    "action": "delivered", "raw_status": "delivered", "note": ""}]
        fresh, skipped = plan_events(records, [], NOW)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["action"], "delivered")
        self.assertEqual(fresh[0]["provider_id"], "re_1")
        self.assertEqual(fresh[0]["actor"], "provider-poll")
        self.assertEqual(skipped, [])

    def test_duplicate_action_is_skipped(self):
        events = [build_event("soundbuttons.cc", "host.com", "email", "delivered",
                              at=_iso(0), address="ed@host.com", provider_id="re_1")]
        records = [{"center_domain": "soundbuttons.cc", "target_domain": "host.com",
                    "channel": "email", "address": "ed@host.com", "provider_id": "re_1",
                    "action": "delivered"}]
        fresh, skipped = plan_events(records, events, NOW)
        self.assertEqual(fresh, [])
        self.assertEqual(skipped[0]["reason"], "already recorded")

    def test_unresolved_status_plans_nothing(self):
        records = [{"center_domain": "soundbuttons.cc", "target_domain": "host.com",
                    "channel": "email", "address": "ed@host.com", "provider_id": "re_1",
                    "action": "", "status": "unknown"}]
        fresh, skipped = plan_events(records, [], NOW)
        self.assertEqual(fresh, [])
        self.assertEqual(skipped, [])

    def test_missing_address_is_skipped_not_guessed(self):
        records = [{"center_domain": "soundbuttons.cc", "target_domain": "host.com",
                    "channel": "email", "address": "", "provider_id": "re_1",
                    "action": "bounced"}]
        fresh, skipped = plan_events(records, [], NOW)
        self.assertEqual(fresh, [])
        self.assertIn("no address", skipped[0]["reason"])


class TestPollReceipts(unittest.TestCase):
    def _events_dir(self, tmp):
        return Path(tmp) / "records"

    def _write_sent(self, tmp, **kwargs):
        directory = self._events_dir(tmp)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "events-soundbuttons.cc.jsonl"
        path.write_text(json.dumps(_sent(**kwargs)) + "\n", encoding="utf-8")
        return directory

    def test_auto_confirm_writes_the_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write_sent(tmp)
            result = poll_receipts(
                events=load_events([directory]),
                reader=_reader({"re_1": "delivered"}),
                provider="resend", config=CONFIG, now=NOW,
                events_dir=directory,
                receipt_path=Path(tmp) / "obs" / "receipts.jsonl",
                proposal_path=Path(tmp) / "obs" / "proposals.jsonl",
                auto_confirm=True,
            )
            self.assertEqual(result["polls"], 1)
            self.assertEqual(result["by_status"]["delivered"], 1)
            self.assertEqual(result["events_written"], 1)
            events = load_events([directory])
            actions = [event["action"] for event in events]
            self.assertIn("delivered", actions)
            state = project(events, now=NOW)
            self.assertEqual(state["rows"][0]["state"], "delivered")

    def test_bounce_promotes_the_address_to_contact_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write_sent(tmp)
            poll_receipts(
                events=load_events([directory]),
                reader=_reader({"re_1": "bounced"}),
                provider="resend", config=CONFIG, now=NOW,
                events_dir=directory,
                receipt_path=Path(tmp) / "obs" / "receipts.jsonl",
                proposal_path=Path(tmp) / "obs" / "proposals.jsonl",
                auto_confirm=True,
            )
            state = project(load_events([directory]), now=NOW, contact_lost_days=180)
            self.assertEqual(state["rows"][0]["state"], "contact_lost")

    def test_default_writes_proposals_not_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write_sent(tmp)
            proposal = Path(tmp) / "obs" / "proposals.jsonl"
            result = poll_receipts(
                events=load_events([directory]),
                reader=_reader({"re_1": "delivered"}),
                provider="resend", config=CONFIG, now=NOW,
                events_dir=directory,
                receipt_path=Path(tmp) / "obs" / "receipts.jsonl",
                proposal_path=proposal,
                auto_confirm=False,
            )
            self.assertEqual(result["events_written"], 0)
            self.assertEqual(result["proposals"], 1)
            events = load_events([directory])
            self.assertNotIn("delivered", [event["action"] for event in events])
            lines = proposal.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0])["action"], "delivered")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write_sent(tmp)
            out = Path(tmp) / "obs"
            result = poll_receipts(
                events=load_events([directory]),
                reader=_reader({"re_1": "delivered"}),
                provider="resend", config=CONFIG, now=NOW,
                events_dir=directory,
                receipt_path=out / "receipts.jsonl",
                proposal_path=out / "proposals.jsonl",
                auto_confirm=True, dry_run=True,
            )
            self.assertEqual(result["by_status"]["delivered"], 1)
            self.assertEqual(result["events_written"], 0)
            self.assertFalse((out / "receipts.jsonl").exists())
            self.assertEqual(len(load_events([directory])), 1)

    def test_second_poll_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write_sent(tmp)
            out = Path(tmp) / "obs"
            kwargs = dict(
                provider="resend", config=CONFIG, now=NOW,
                events_dir=directory,
                receipt_path=out / "receipts.jsonl",
                proposal_path=out / "proposals.jsonl",
                auto_confirm=True,
            )
            poll_receipts(events=load_events([directory]), reader=_reader({"re_1": "delivered"}), **kwargs)
            second = poll_receipts(
                events=load_events([directory]), reader=_reader({"re_1": "delivered"}), **kwargs
            )
            self.assertEqual(second["polls"], 0)
            self.assertEqual(second["already_resolved"], 1)
            delivered = [e for e in load_events([directory]) if e["action"] == "delivered"]
            self.assertEqual(len(delivered), 1)

    def test_unknown_status_records_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._write_sent(tmp)
            result = poll_receipts(
                events=load_events([directory]),
                reader=_reader({"re_1": (401, "{}")}),
                provider="resend", config=CONFIG, now=NOW,
                events_dir=directory,
                receipt_path=Path(tmp) / "obs" / "receipts.jsonl",
                proposal_path=Path(tmp) / "obs" / "proposals.jsonl",
                auto_confirm=True,
            )
            self.assertEqual(result["by_status"]["unknown"], 1)
            self.assertEqual(result["events_written"], 0)
            self.assertEqual(len(load_events([directory])), 1)

    def test_nothing_to_poll_still_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = poll_receipts(
                events=[], reader=_reader({}), provider="resend", config=CONFIG, now=NOW,
                events_dir=Path(tmp), receipt_path=Path(tmp) / "r.jsonl",
                proposal_path=Path(tmp) / "p.jsonl", auto_confirm=True,
            )
            self.assertEqual(result["polls"], 0)
            self.assertEqual(result["events_planned"], 0)


class TestResolvedIds(unittest.TestCase):
    def test_indexes_by_id_and_action(self):
        events = [build_event("soundbuttons.cc", "host.com", "email", "bounced",
                              at=_iso(1), address="ed@host.com", provider_id="re_9")]
        index = resolved_ids(events)
        self.assertIn(("re_9", "bounced"), index)
        self.assertNotIn(("re_9", "delivered"), index)


if __name__ == "__main__":
    unittest.main()
