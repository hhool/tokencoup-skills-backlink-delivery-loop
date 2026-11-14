"""Tests for the contract-C producer bridge (prospector store -> snapshot)."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from bdl import CONTRACTS_VERSION
from bdl.adapters.prospector_export import export_snapshot, load_enrichment
from bdl.adapters.snapshot import load_snapshot
from bdl.errors import SnapshotError

PROSPECTOR_DIR = Path("/Users/hou/Desktop/skill-seo-geo/backlink-prospector/data")


def _write_store(tmp: str, center: str, domains: dict) -> str:
    path = os.path.join(tmp, "store-%s.json" % center)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "schema_version": "1.0.0",
            "center_domain": center,
            "domains": domains,
            "acquired_domains": [],
        }, handle, ensure_ascii=False)
    return path


class TestExportSnapshot(unittest.TestCase):
    def test_faithful_field_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _write_store(tmp, "soundbuttons.cc", {
                "example.com": {
                    "domain": "Example.com",
                    "url": "https://example.com",
                    "type_key": "Resource",
                    "priority": 87,
                    "http_status": 200,
                    "acquired": False,
                    "is_competitor": False,
                    "note": "free sample hub",
                },
                "rival.net": {
                    "domain": "rival.net",
                    "url": "",
                    "type_key": "guest",
                    "priority": None,
                    "http_status": 403,
                    "acquired": True,
                    "is_competitor": True,
                    "note": "",
                },
            })
            snap = export_snapshot(store)
            src = Path(store)
            expected_hash = "sha256:" + hashlib.sha256(src.read_bytes()).hexdigest()
            store_file_name = src.name

        self.assertEqual(snap["center_domain"], "soundbuttons.cc")
        self.assertEqual(snap["schema_version"], CONTRACTS_VERSION)
        self.assertEqual(len(snap["prospects"]), 2)

        by_domain = {p["domain"]: p for p in snap["prospects"]}
        first = by_domain["example.com"]
        self.assertEqual(first["url"], "https://example.com")
        self.assertEqual(first["type_key"], "resource")
        self.assertEqual(first["priority"], 87)
        self.assertEqual(first["http_status"], 200)
        self.assertFalse(first["acquired"])
        self.assertFalse(first["is_competitor"])
        self.assertTrue(first["is_gap"])

        rival = by_domain["rival.net"]
        self.assertEqual(rival["url"], "https://rival.net")  # empty url fallback
        self.assertEqual(rival["priority"], 0)  # None -> 0
        self.assertEqual(rival["http_status"], 403)
        self.assertTrue(rival["acquired"])
        self.assertTrue(rival["is_competitor"])
        self.assertFalse(rival["is_gap"])

        self.assertEqual(snap["source"]["content_hash"], expected_hash)
        self.assertEqual(snap["source"]["file"], store_file_name)

    def test_consumer_accepts_produced_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _write_store(tmp, "demo.cc", {
                "a.com": {"domain": "a.com", "url": "https://a.com", "type_key": "directory",
                          "priority": 50, "http_status": 200, "acquired": False,
                          "is_competitor": False},
            })
            snap = export_snapshot(store)
            snap_path = os.path.join(tmp, "snapshot.json")
            with open(snap_path, "w", encoding="utf-8") as handle:
                json.dump(snap, handle, ensure_ascii=False)

            loaded = load_snapshot(snap_path, "demo.cc")
        self.assertEqual(loaded.center_domain, "demo.cc")
        self.assertEqual(len(loaded.prospects), 1)
        self.assertEqual(loaded.prospects[0].type_key, "directory")

    def test_missing_store_raises(self):
        with self.assertRaises(SnapshotError):
            export_snapshot("/no/such/store.json")

    def test_real_prospector_store_exports_and_validates(self):
        store = PROSPECTOR_DIR / "store-soundbuttons.cc.json"
        if not store.is_file():
            self.skipTest("prospector store not present")
        snap = export_snapshot(str(store))
        self.assertEqual(snap["center_domain"], "soundbuttons.cc")
        self.assertGreater(len(snap["prospects"]), 0)

        with tempfile.TemporaryDirectory() as tmp:
            snap_path = os.path.join(tmp, "snap.json")
            with open(snap_path, "w", encoding="utf-8") as handle:
                json.dump(snap, handle, ensure_ascii=False)
            loaded = load_snapshot(snap_path, "soundbuttons.cc")

        domains = [p.domain for p in loaded.prospects]
        self.assertEqual(len(domains), len(set(domains)), "no duplicate prospects")
        # every prospector type_key must be handled by the consumer's normalize map
        from bdl.services.normalize import TYPE_MAP, EXCLUDED_TYPES
        for prospect in loaded.prospects:
            self.assertTrue(
                prospect.type_key in TYPE_MAP or prospect.type_key in EXCLUDED_TYPES,
                "unmapped type_key: %s" % prospect.type_key,
            )


if __name__ == "__main__":
    unittest.main()
