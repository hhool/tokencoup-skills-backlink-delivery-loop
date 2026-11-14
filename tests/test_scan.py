import tempfile
import unittest
from pathlib import Path

from bdl.models import ManifestRow
from bdl.services.observe import FetchError
from bdl.services.scan import scan_rows


def make_row(target_domain="example.com", status="ready_for_review", target_url="https://example.com/p",
             own_url="https://soundbuttons.cc/x", channel_type="asset"):
    return ManifestRow(
        prospect_id="%s#%s" % (target_domain, channel_type),
        center_domain="soundbuttons.cc",
        target_domain=target_domain,
        candidate_url="https://example.com",
        target_url=target_url,
        submission_url="https://example.com/submit",
        own_url=own_url,
        contact_email="editor@example.com",
        contact_state="candidate",
        channel_type=channel_type,
        type_key="resource",
        status=status,
    )


def fetcher_returning(status, body):
    def fetch(url):
        return status, body
    return fetch


def fetcher_raising(kind):
    def fetch(url):
        raise FetchError(kind)
    return fetch


class ScanTests(unittest.TestCase):
    def test_found_without_auto_confirm_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "obs.jsonl"
            prop = Path(tmp) / "prop.jsonl"
            body = '<a href="https://soundbuttons.cc/x">launch</a>'
            result = scan_rows(
                [make_row()], fetcher_returning(200, body), False, "soundbuttons.cc",
                str(obs), str(prop),
            )
            self.assertEqual(result["scanned"], 1)
            self.assertEqual(result["found"], 1)
            self.assertEqual(result["confirmed"], 0)
            from bdl.adapters.observation import load_observations
            rows = load_observations(obs)
            self.assertEqual(rows[0]["state"], "pending_human_confirmation")
            self.assertEqual(len(load_observations(prop)), 0)

    def test_found_with_auto_confirm_marks_live_and_queues_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "obs.jsonl"
            prop = Path(tmp) / "prop.jsonl"
            body = '<a href="https://soundbuttons.cc/x">launch</a>'
            result = scan_rows(
                [make_row()], fetcher_returning(200, body), True, "soundbuttons.cc",
                str(obs), str(prop),
            )
            self.assertEqual(result["confirmed"], 1)
            from bdl.adapters.observation import load_observations
            rows = load_observations(obs)
            self.assertEqual(rows[0]["state"], "live")
            self.assertEqual(len(load_observations(prop)), 1)

    def test_absent_and_unknown(self):
        def mixed_fetcher(url):
            if "timeout" in url:
                raise FetchError("timeout")
            return 200, "<html>no link</html>"

        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "obs.jsonl"
            prop = Path(tmp) / "prop.jsonl"
            result = scan_rows(
                [make_row(target_domain="absent.com"),
                 make_row(target_domain="timeout.com", target_url="https://timeout.example.com/p")],
                mixed_fetcher, False, "soundbuttons.cc", str(obs), str(prop),
            )
            self.assertEqual(result["absent"], 1)
            self.assertEqual(result["unknown"], 1)

    def test_non_observable_rows_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs = Path(tmp) / "obs.jsonl"
            prop = Path(tmp) / "prop.jsonl"
            blocked = make_row(status="blocked")
            no_target = make_row(target_url=None, own_url=None)
            result = scan_rows(
                [blocked, no_target], fetcher_returning(200, "x"), False,
                "soundbuttons.cc", str(obs), str(prop),
            )
            self.assertEqual(result["scanned"], 0)


if __name__ == "__main__":
    unittest.main()
