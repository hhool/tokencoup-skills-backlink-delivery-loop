import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bdl.adapters.csv_import import center_from_filename, import_csv
from bdl.errors import SnapshotError
from bdl.models import Prospect, Snapshot
from bdl.services.normalize import build_rows

HEADER = (
    "url,domain,type_key,type_label_zh,type_label_en,authority_tier,relevance,"
    "authority_score,obtainability,priority,valid,http_status,is_gap,is_competitor,"
    "already_have,tranco_global_rank,source,note,note_en\n"
)

ROWS = (
    "https://good.com,good.com,resource,label,Resource,Medium,31,20,26,77,True,200,"
    "True,False,False,,seed,note,note\n"
    "https://rival.com,rival.com,resource,label,Resource,Medium,31,20,26,77,True,200,"
    "False,False,False,,seed,note,note\n"
    "https://dead.com,dead.com,resource,label,Resource,Medium,31,20,26,69,False,502,"
    "True,False,False,,seed,note,note\n"
    "https://have.com,have.com,guest,label,Guest post,Medium,23,20,22,65,True,200,"
    "True,False,True,,seed,note,note\n"
    "https://low.com,low.com,guest,label,Guest post,Medium,23,20,22,50,True,200,"
    "True,False,False,,seed,note,note\n"
    "https://low.com,low.com,guest,label,Guest post,Medium,23,20,22,90,True,200,"
    "True,False,False,,seed,note,note\n"
)


def write_csv(directory: Path, name: str = "backlink-prospects-soundbuttons.cc-20261114T181442Z.csv") -> Path:
    path = directory / name
    path.write_bytes(("\ufeff" + HEADER + ROWS).encode("utf-8"))
    return path


class CsvImportTests(unittest.TestCase):
    def test_maps_columns_and_keeps_is_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(Path(tmp))
            diag = {}
            snapshot = import_csv(path, diagnostics=diag)

        self.assertEqual(snapshot["center_domain"], "soundbuttons.cc")
        self.assertEqual(snapshot["source"]["file"], path.name)
        by_domain = {item["domain"]: item for item in snapshot["prospects"]}

        self.assertTrue(by_domain["good.com"]["is_gap"])
        self.assertFalse(by_domain["rival.com"]["is_gap"], "upstream is_gap must pass through")
        # A contradictory upstream row (already_have=true but is_gap=true) keeps its
        # is_gap value; admission blocks it on `acquired` regardless.
        self.assertTrue(by_domain["have.com"]["acquired"], "already_have maps to acquired")

        self.assertFalse(by_domain["dead.com"]["is_gap"], "an invalid record is not a gap")
        self.assertEqual(by_domain["dead.com"]["http_status"], 502)

        self.assertEqual(diag["is_gap_folded_for_invalid"], 1)
        self.assertEqual(diag["collapsed_duplicates"], 1)
        self.assertIn("authority_tier", diag["dropped_columns"])

    def test_dedupe_keeps_highest_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = import_csv(write_csv(Path(tmp)))

        low = [item for item in snapshot["prospects"] if item["domain"] == "low.com"]
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["priority"], 90)

    def test_content_hash_covers_the_file_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(Path(tmp))
            expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot = import_csv(path)
        self.assertEqual(snapshot["source"]["content_hash"], expected)

    def test_center_domain_from_explicit_argument_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = import_csv(write_csv(Path(tmp)), center_domain="other.example")
        self.assertEqual(snapshot["center_domain"], "other.example")

    def test_filename_fallback_and_failure(self):
        self.assertEqual(
            center_from_filename(Path("backlink-prospects-a.example-20261114T181442Z.csv")),
            "a.example",
        )
        self.assertEqual(center_from_filename(Path("prospects.csv")), "")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prospects.csv"
            path.write_text(HEADER + ROWS, encoding="utf-8")
            with self.assertRaises(SnapshotError):
                import_csv(path)

    def test_missing_domain_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("url,type_key\nhttps://x.com,resource\n", encoding="utf-8")
            with self.assertRaises(SnapshotError):
                import_csv(path, center_domain="soundbuttons.cc")

    def test_imported_snapshot_feeds_admission(self):
        """is_gap=false must block the row, not resurrect it."""
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = import_csv(write_csv(Path(tmp)))

        model = Snapshot(
            center_domain=snapshot["center_domain"],
            schema_version=snapshot["schema_version"],
            generated_at=snapshot["generated_at"],
            source=snapshot["source"],
            prospects=[Prospect(**item) for item in snapshot["prospects"]],
            file_hash="sha256:" + "0" * 64,
        )
        rows = build_rows(model, [], "https://soundbuttons.cc", set(), [],
                          datetime(2026, 11, 15, tzinfo=timezone.utc))
        by_domain = {row.target_domain: row for row in rows}

        self.assertEqual(by_domain["rival.com"].status, "blocked")
        self.assertIn("not an actionable gap", by_domain["rival.com"].reasons[-1])
        self.assertEqual(by_domain["have.com"].status, "blocked")
        self.assertEqual(by_domain["dead.com"].status, "blocked")
        self.assertEqual(by_domain["good.com"].status, "needs_enrichment")


class DiscoveryColumnTests(unittest.TestCase):
    """Channel columns survive the CSV, but only values that look like evidence."""

    DISCOVERY_HEADER = (
        "url,domain,type_key,priority,http_status,valid,already_have,is_competitor,"
        "is_gap,submission_url,contact_email,contact_source,contact_source_url\n"
    )

    ROWS = (
        # A clean row: form URL and an address with its discovery page.
        "https://formy.com,formy.com,resource,50,200,True,False,False,True,"
        "https://formy.com/submit,,,"
        "https://formy.com/contact\n"
        # An address row with provenance.
        "https://maily.com,maily.com,resource,50,200,True,False,False,True,"
        ",hello@maily.com,mailto,https://maily.com/about\n"
        # Junk is dropped, not cleaned up: relative URL, bad source word,
        # address without a real domain, discovery page without an address.
        "https://junky.com,junky.com,resource,50,200,True,False,False,True,"
        "/submit,hello@junky,magic_page,about.html\n"
        "https://lone.com,lone.com,resource,50,200,True,False,False,True,"
        "https://lone.com/submit,,,https://lone.com/contact\n"
    )

    def _import(self, tmp: Path):
        path = tmp / "prospects.csv"
        path.write_text(self.DISCOVERY_HEADER + self.ROWS, encoding="utf-8")
        return import_csv(path, center_domain="soundbuttons.cc")

    def test_clean_values_reach_the_discovery_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._import(Path(tmp))
        by_domain = {item["domain"]: item for item in snapshot["prospects"]}

        self.assertEqual(by_domain["formy.com"]["submission_url"], "https://formy.com/submit")
        self.assertEqual(by_domain["maily.com"]["contact_email"], "hello@maily.com")
        self.assertEqual(by_domain["maily.com"]["contact_source"], "mailto")
        self.assertEqual(by_domain["maily.com"]["contact_source_url"], "https://maily.com/about")

    def test_malformed_values_are_dropped_not_coerced(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._import(Path(tmp))
        junky = {item["domain"]: item for item in snapshot["prospects"]}["junky.com"]
        self.assertNotIn("submission_url", junky)
        self.assertNotIn("contact_email", junky)
        self.assertNotIn("contact_source", junky)
        self.assertNotIn("contact_source_url", junky)

    def test_a_page_without_an_address_is_not_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._import(Path(tmp))
        lone = {item["domain"]: item for item in snapshot["prospects"]}["lone.com"]
        self.assertEqual(lone["submission_url"], "https://lone.com/submit")
        self.assertNotIn("contact_source_url", lone)

    def test_discovery_rows_flow_into_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._import(Path(tmp))
        model = Snapshot(
            center_domain=snapshot["center_domain"],
            schema_version=snapshot["schema_version"],
            generated_at=snapshot["generated_at"],
            source=snapshot["source"],
            prospects=[Prospect(**item) for item in snapshot["prospects"]],
            file_hash="sha256:" + "0" * 64,
        )
        rows = build_rows(model, [], "https://soundbuttons.cc", set(), [],
                          datetime(2026, 11, 15, tzinfo=timezone.utc))
        by_domain = {row.target_domain: row for row in rows}
        self.assertEqual(by_domain["maily.com"].contact_source_url, "https://maily.com/about")
        self.assertEqual(by_domain["formy.com"].submission_url, "https://formy.com/submit")


if __name__ == "__main__":
    unittest.main()
