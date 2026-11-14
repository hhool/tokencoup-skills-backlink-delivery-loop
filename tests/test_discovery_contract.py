"""Contract C 1.1.0: the discovery block, and what admission does with it.

The block is additive, so a 1.0.0 snapshot must keep validating. But the fields
only exist from 1.1.0 onward: a producer that writes them under a 1.0.0 label is
mislabelled, and the consumer refuses rather than guessing which version to trust.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bdl import CONTRACTS_VERSION
from bdl.adapters.prospector_export import export_snapshot, load_enrichment
from bdl.adapters.snapshot import load_snapshot
from bdl.errors import SnapshotError
from bdl.models import Prospect, Snapshot
from bdl.services.normalize import build_rows
from tests import snapshot_doc, write_doc

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)
SHAPE = "sha256:" + "a" * 64

DISCOVERY = {
    "submission_url": "https://example.com/submit-a-link",
    "form_shape": SHAPE,
    "form_fields": ["name", "email", "url", "message"],
    "contact_email": "editor@example.com",
    "contact_source": "mailto",
    "contact_source_url": "https://example.com/contact",
    "enriched_at": "2026-11-15T02:00:00Z",
}


def _load(doc: dict):
    with tempfile.TemporaryDirectory() as tmp:
        return load_snapshot(write_doc(Path(tmp) / "s.json", doc))


def _v11(doc: dict, index: int = 0, **fields) -> dict:
    doc["schema_version"] = "1.1.0"
    doc["prospects"][index].update(fields)
    return doc


class DiscoveryContractTests(unittest.TestCase):
    def test_discovery_requires_the_newer_version(self):
        doc = snapshot_doc()
        doc["prospects"][0].update(DISCOVERY)  # still labelled 1.0.0
        with self.assertRaises(SnapshotError) as ctx:
            _load(doc)
        self.assertIn("1.1.0", json.dumps(ctx.exception.detail))

    def test_discovery_fields_are_read(self):
        snap = _load(_v11(snapshot_doc(), **DISCOVERY))
        self.assertEqual(snap.schema_version, "1.1.0")
        prospect = snap.prospects[0]
        self.assertEqual(prospect.submission_url, DISCOVERY["submission_url"])
        self.assertEqual(prospect.form_shape, SHAPE)
        self.assertEqual(prospect.form_fields, DISCOVERY["form_fields"])
        self.assertEqual(prospect.contact_email, "editor@example.com")
        self.assertEqual(prospect.contact_source, "mailto")
        self.assertTrue(prospect.discovered)

    def test_a_one_zero_zero_document_still_validates(self):
        snap = _load(snapshot_doc())
        self.assertEqual(snap.schema_version, "1.0.0")
        self.assertIsNone(snap.prospects[0].submission_url)
        self.assertFalse(snap.prospects[0].discovered)

    def test_provenance_without_an_address_is_rejected(self):
        doc = _v11(snapshot_doc(), contact_source="mailto")
        with self.assertRaises(SnapshotError):
            _load(doc)

    def test_unknown_provenance_value_is_rejected(self):
        doc = _v11(snapshot_doc(), contact_email="a@b.com", contact_source="guessed")
        with self.assertRaises(SnapshotError):
            _load(doc)

    def test_malformed_shape_is_rejected(self):
        doc = _v11(snapshot_doc(), form_shape="sha256:short")
        with self.assertRaises(SnapshotError):
            _load(doc)

    def test_form_fields_must_be_strings(self):
        doc = _v11(snapshot_doc(), form_fields=["name", 7])
        with self.assertRaises(SnapshotError):
            _load(doc)

    def test_null_discovery_fields_are_absent_not_empty(self):
        doc = _v11(snapshot_doc(), submission_url=None, contact_email=None,
                   enriched_at="2026-11-15T02:00:00Z")
        prospect = _load(doc).prospects[0]
        self.assertIsNone(prospect.submission_url)
        self.assertTrue(prospect.discovered)


class ExportWithEnrichmentTests(unittest.TestCase):
    def _store(self, tmp: str, center: str, domains: dict) -> str:
        path = Path(tmp) / ("store-%s.json" % center)
        path.write_text(
            json.dumps({"schema_version": "1.0.0", "center_domain": center, "domains": domains}),
            encoding="utf-8",
        )
        return str(path)

    def _overlay(self, tmp: str, domains: dict) -> str:
        path = Path(tmp) / "enrichment.json"
        path.write_text(
            json.dumps({"center_domain": "soundbuttons.cc", "domains": domains}),
            encoding="utf-8",
        )
        return str(path)

    def test_overlay_fields_reach_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "soundbuttons.cc", {
                "example.com": {"domain": "example.com", "url": "https://example.com",
                                "type_key": "directory", "priority": 60},
            })
            overlay = self._overlay(tmp, {
                "example.com": {"domain": "example.com", **DISCOVERY},
            })
            diag: dict = {}
            snap = export_snapshot(store, diagnostics=diag, enrichment_path=overlay)
            self.assertEqual(snap["schema_version"], CONTRACTS_VERSION)
            prospect = snap["prospects"][0]
            self.assertEqual(prospect["submission_url"], DISCOVERY["submission_url"])
            self.assertEqual(diag["enriched"], 1)
            self.assertEqual(diag["with_channel"], 1)

    def test_export_without_overlay_leaves_the_block_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, "soundbuttons.cc", {
                "example.com": {"domain": "example.com", "url": "https://example.com",
                                "type_key": "directory", "priority": 60},
            })
            snap = export_snapshot(store)
            self.assertNotIn("submission_url", snap["prospects"][0])

    def test_overlay_is_keyed_on_the_registrable_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = self._overlay(tmp, {
                "WWW.Example.COM": {"domain": "www.example.com", **DISCOVERY},
            })
            index = load_enrichment(overlay)
            self.assertIn("example.com", index)

    def test_missing_overlay_is_a_typed_error(self):
        with self.assertRaises(SnapshotError):
            load_enrichment("/nonexistent/enrichment.json")


def _snapshot(**prospect_fields) -> Snapshot:
    base = dict(
        domain="example.com",
        url="https://example.com",
        type_key="directory",
        priority=60,
        http_status=200,
        acquired=False,
        is_competitor=False,
        is_gap=True,
    )
    base.update(prospect_fields)
    return Snapshot(
        center_domain="soundbuttons.cc",
        schema_version="1.1.0",
        generated_at="2026-11-15T02:00:00Z",
        source={"repo": "x", "file": "y", "content_hash": "sha256:" + "b" * 64},
        prospects=[Prospect(**base)],
        file_hash="sha256:" + "c" * 64,
    )


class AdmissionPrefersDiscoveryTests(unittest.TestCase):
    def _row(self, snapshot, own_landing_url=""):
        rows = build_rows(
            snapshot, [], "https://soundbuttons.cc", set(), [], NOW,
            own_landing_url=own_landing_url,
        )
        return rows[0]

    def test_a_discovered_channel_makes_the_row_submittable(self):
        row = self._row(_snapshot(submission_url="https://example.com/submit",
                                  enriched_at="2026-11-15T02:00:00Z"))
        self.assertEqual(row.submission_url, "https://example.com/submit")
        self.assertEqual(row.contact_state, "discovered")
        self.assertIn("form", row.channels)

    def test_submitting_does_not_wait_for_a_listing_page(self):
        # A directory has no listing page until it publishes one. Requiring
        # target_url here would stall every directory row on an unknowable value.
        row = self._row(_snapshot(contact_email="a@example.com",
                                  contact_source="mailto",
                                  enriched_at="2026-11-15T02:00:00Z"),
                        own_landing_url="https://soundbuttons.cc/free-soundboard")
        self.assertEqual(row.status, "ready_to_submit")
        self.assertIsNone(row.target_url)
        self.assertTrue(any("listing page" in gap for gap in row.gaps))

    def test_observation_is_ready_when_a_landing_page_is_known(self):
        row = self._row(_snapshot(submission_url="https://example.com/submit",
                                  enriched_at="2026-11-15T02:00:00Z"),
                        own_landing_url="https://soundbuttons.cc/free-soundboard")
        self.assertEqual(row.status, "ready_to_submit")
        self.assertEqual(row.own_url, "https://soundbuttons.cc/free-soundboard")

    def test_a_discovered_dead_end_is_not_endless_work(self):
        row = self._row(_snapshot(enriched_at="2026-11-15T02:00:00Z"))
        self.assertEqual(row.status, "no_channel")
        self.assertTrue(any("observed the site" in gap for gap in row.gaps))

    def test_an_unlooked_domain_is_still_enrichable(self):
        row = self._row(_snapshot())
        self.assertEqual(row.status, "needs_enrichment")
        self.assertTrue(any("no delivery channel in prose" in gap for gap in row.gaps))

    def test_configured_landing_url_fills_every_row(self):
        row = self._row(_snapshot(contact_email="a@example.com"),
                        own_landing_url="https://soundbuttons.cc/free-soundboard")
        self.assertEqual(row.own_url, "https://soundbuttons.cc/free-soundboard")

    def test_prose_still_wins_when_discovery_found_nothing(self):
        # Discovery is a snapshot of the site at one moment; an operator note that
        # names an address is still evidence, and the row stays submittable.
        row = self._row(_snapshot(enriched_at="2026-11-15T02:00:00Z"),
                        own_landing_url="https://soundbuttons.cc/x")
        self.assertEqual(row.status, "no_channel")
        self.assertEqual(row.channels, [])


if __name__ == "__main__":
    unittest.main()
