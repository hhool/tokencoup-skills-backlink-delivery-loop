import tempfile
import unittest
from pathlib import Path

from bdl.adapters.observation import append_observation, append_proposal, load_observations, now_iso
from bdl.models import ManifestRow


class ObservationLedgerTests(unittest.TestCase):
    def test_append_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "obs.jsonl"
            append_observation(path, {"target_domain": "x.com", "outcome": "found", "state": "live"})
            append_observation(path, {"target_domain": "y.com", "outcome": "absent", "state": "pending"})
            rows = load_observations(path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["target_domain"], "x.com")

    def test_load_missing_returns_empty(self):
        self.assertEqual(load_observations("/nonexistent/obs.jsonl"), [])

    def test_proposal_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prop.jsonl"
            append_proposal(path, {"target_domain": "x.com", "proposed_state": "live"})
            rows = load_observations(path)
            self.assertEqual(rows[0]["proposed_state"], "live")

    def test_now_iso_is_utc(self):
        self.assertTrue(now_iso().endswith("Z"))


if __name__ == "__main__":
    unittest.main()
