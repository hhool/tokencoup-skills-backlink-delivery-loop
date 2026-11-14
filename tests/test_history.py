import json
import tempfile
import unittest
from pathlib import Path

from bdl.adapters.outbox import collect_history, lookup_history


def _history_file(directory: Path, name: str, rows: list) -> None:
    (directory / name).write_text(json.dumps({"rows": rows}), encoding="utf-8")


class HistoryAggregationTests(unittest.TestCase):
    def test_collect_from_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _history_file(d, "a.json", [{"target_domain": "x.com", "event": "send"}])
            _history_file(d, "b.json", [{"target_domain": "y.com", "event": "submit"}])
            rows = collect_history([str(d / "a.json"), str(d / "b.json")])
            self.assertEqual(len(rows), 2)

    def test_collect_from_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _history_file(d, "a.json", [{"target_domain": "x.com", "event": "send"}])
            _history_file(d, "b.jsonl", [{"target_domain": "y.com", "event": "submit"}])
            rows = collect_history([str(d)])
            self.assertEqual(len(rows), 2)

    def test_collect_jsonl_one_object_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "c.jsonl").write_text(
                json.dumps({"target_domain": "x.com", "event": "send"}) + "\n"
                + json.dumps({"target_domain": "y.com", "event": "submit"}) + "\n", encoding="utf-8")
            rows = collect_history([str(d / "c.jsonl")])
            self.assertEqual(len(rows), 2)

    def test_missing_source_is_ignored(self):
        rows = collect_history(["/nonexistent/path/history.json"])
        self.assertEqual(rows, [])

    def test_lookup_filters_by_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _history_file(d, "a.json", [
                {"target_domain": "x.com", "event": "send", "sent_at": "2026-01-01T00:00:00Z"},
                {"target_domain": "y.com", "event": "send", "sent_at": "2026-01-02T00:00:00Z"},
            ])
            events = lookup_history("x.com", [str(d / "a.json")])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["target_domain"], "x.com")

    def test_lookup_normalizes_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _history_file(d, "a.json", [{"target_domain": "WWW.X.com", "event": "send"}])
            events = lookup_history("x.com", [str(d / "a.json")])
            self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
