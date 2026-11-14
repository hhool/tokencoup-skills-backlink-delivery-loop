import tempfile
import unittest
from pathlib import Path

from bdl.adapters.snapshot import load_snapshot
from bdl.errors import SnapshotError
from tests import SNAPSHOT, snapshot_doc, write_doc


class SnapshotContractTests(unittest.TestCase):
    def test_valid_export_is_accepted(self):
        snap = load_snapshot(SNAPSHOT, "soundbuttons.cc")
        self.assertEqual(snap.center_domain, "soundbuttons.cc")
        self.assertEqual(len(snap.prospects), 9)
        self.assertTrue(snap.file_hash.startswith("sha256:"))

    def test_unknown_top_level_field_is_rejected(self):
        doc = snapshot_doc()
        doc["unexpected"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = write_doc(Path(tmp) / "s.json", doc)
            with self.assertRaises(SnapshotError) as ctx:
                load_snapshot(path)
            self.assertIn("unexpected", ctx.exception.detail["fields"])

    def test_unsupported_version_is_rejected(self):
        doc = snapshot_doc()
        doc["schema_version"] = "9.9.9"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_doc(Path(tmp) / "s.json", doc)
            with self.assertRaises(SnapshotError):
                load_snapshot(path)

    def test_bad_source_hash_is_rejected(self):
        doc = snapshot_doc()
        doc["source"]["content_hash"] = "md5:abc"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_doc(Path(tmp) / "s.json", doc)
            with self.assertRaises(SnapshotError):
                load_snapshot(path)

    def test_center_domain_mismatch_is_rejected(self):
        with self.assertRaises(SnapshotError):
            load_snapshot(SNAPSHOT, "another-example.com")

    def test_duplicate_domain_is_rejected(self):
        doc = snapshot_doc()
        doc["prospects"].append(dict(doc["prospects"][0]))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_doc(Path(tmp) / "s.json", doc)
            with self.assertRaises(SnapshotError):
                load_snapshot(path)

    def test_missing_file_is_a_typed_error(self):
        with self.assertRaises(SnapshotError):
            load_snapshot("/nonexistent/snapshot.json")


if __name__ == "__main__":
    unittest.main()
