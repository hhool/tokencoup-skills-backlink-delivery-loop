import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bdl.cli import main
from tests import HISTORY, PROSE, SNAPSHOT


class CliTests(unittest.TestCase):
    def _run(self, argv, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(argv)
        text = stdout.getvalue().strip()
        return code, (json.loads(text) if text else {}), stderr.getvalue()

    def test_doctor_reports_no_auto_transmit(self):
        code, payload, _ = self._run(["doctor"], {"BDL_AUTO_TRANSMIT": "false", "BDL_TIER": "B"})
        self.assertEqual(code, 0)
        self.assertFalse(payload["auto_transmit"])

    def test_auto_transmit_true_fails_fast(self):
        code, _, stderr = self._run(["doctor"], {"BDL_AUTO_TRANSMIT": "true"})
        self.assertEqual(code, 1)
        self.assertIn("CONFIG_ERROR", stderr)

    def test_validate_snapshot(self):
        code, payload, _ = self._run(
            ["validate-snapshot", "--file", str(SNAPSHOT), "--center-domain", "soundbuttons.cc"],
            {"BDL_AUTO_TRANSMIT": "false"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["prospects"], 9)

    def test_build_manifest_writes_review_artefact(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            env = {
                "BDL_HOME": tmp,
                "BDL_PROSE_DIR": str(PROSE),
                "BDL_OUT_DIR": str(out),
                "BDL_CENTER_DOMAIN": "soundbuttons.cc",
                "BDL_OWN_SITE_BASE": "https://soundbuttons.cc",
                "BDL_AUTO_TRANSMIT": "false",
                "BDL_TIER": "B",
            }
            code, payload, _ = self._run(
                [
                    "build-manifest",
                    "--snapshot", str(SNAPSHOT),
                    "--history", str(HISTORY),
                ],
                env,
            )
            self.assertEqual(code, 0)
            self.assertEqual(payload["summary"]["total"], 9)
            manifest = json.loads((out / "delivery-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["policy"]["auto_transmit"])
            self.assertTrue(manifest["policy"]["human_review_required"])
            self.assertEqual(len(manifest["rows"]), 9)
            self.assertTrue((out / "receipts.jsonl").is_file())

    def test_plan_window_is_deterministic(self):
        code, payload, _ = self._run(
            ["plan-window", "--submitted-at", "2026-11-14T12:00:00Z"],
            {"BDL_AUTO_TRANSMIT": "false"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["schedule"][0], "2026-11-15T12:00:00Z")
        self.assertEqual(payload["terminal_state"], "awaiting_review")

    def test_classify_403_is_unknown(self):
        code, payload, _ = self._run(
            ["classify", "--outcome", "absent", "--http-status", "403"],
            {"BDL_AUTO_TRANSMIT": "false"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["outcome"], "unknown")

    def test_lookup_reconciles_history(self):
        code, payload, _ = self._run(
            ["lookup", "--target-domain", "defer-example.com", "--history", str(HISTORY)],
            {"BDL_AUTO_TRANSMIT": "false"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["target_domain"], "defer-example.com")

    def test_package_command_reports_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "SKILL.md").write_text("# package fixture", encoding="utf-8")
            Path(tmp, "extra.txt").write_text("payload", encoding="utf-8")
            code, payload, _ = self._run(
                ["package", "--out", str(Path(tmp) / "dist")],
                {"BDL_HOME": tmp, "BDL_AUTO_TRANSMIT": "false"},
            )
            self.assertEqual(code, 0)
            self.assertGreater(payload["bytes"], 0)
            self.assertTrue(Path(payload["archive"]).is_file())

    def test_render_template_preview(self):
        code, payload, _ = self._run(
            ["render-template", "--channel-type", "asset", "--context-json",
             json.dumps({"target_domain": "x.com", "center_domain": "c.com",
                         "own_site_base": "https://c.com", "own_url": "https://c.com/p",
                         "candidate_url": "https://x.com", "target_url": "https://x.com",
                         "submission_url": "https://x.com/s", "contact_email": "e@x.com"})],
            {"BDL_AUTO_TRANSMIT": "false"},
        )
        self.assertEqual(code, 0)
        self.assertNotIn("{{", payload["content"]["email"]["subject"])
        self.assertIn("x.com", payload["content"]["email"]["subject"])


if __name__ == "__main__":
    unittest.main()
