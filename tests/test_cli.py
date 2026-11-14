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

    def _workspace(self, tmp: str) -> dict:
        return {
            "BDL_HOME": tmp,
            "BDL_CENTER_DOMAIN": "soundbuttons.cc",
            "BDL_AUTO_TRANSMIT": "false",
            "BDL_RECORDS_DIR": str(Path(tmp) / "records"),
            "BDL_OBSERVATION_DIR": str(Path(tmp) / "obs"),
            "BDL_FORMS_DIR": str(Path(tmp) / "forms"),
            "BDL_OUT_DIR": str(Path(tmp) / "out"),
        }

    def test_import_prospects_dry_run_reports_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "backlink-prospects-soundbuttons.cc-20261114T181442Z.csv"
            csv_path.write_bytes((
                "\ufeffurl,domain,type_key,priority,valid,http_status,is_gap,"
                "is_competitor,already_have,authority_tier\n"
                "https://a.com,a.com,resource,70,True,200,True,False,False,Medium\n"
                "https://b.com,b.com,resource,60,True,200,False,False,False,Medium\n"
            ).encode("utf-8"))
            code, payload, _ = self._run(
                ["import-prospects", "--csv", str(csv_path), "--dry-run"],
                self._workspace(tmp),
            )
            self.assertEqual(code, 0)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["prospects"], 2)
            self.assertEqual(payload["center_domain"], "soundbuttons.cc")
            self.assertIn("authority_tier", payload["mapping"]["dropped_columns"])

    def test_import_prospects_writes_a_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "prospects.csv"
            csv_path.write_bytes((
                "\ufeffurl,domain,type_key,priority,valid,http_status,is_gap,"
                "is_competitor,already_have\n"
                "https://a.com,a.com,resource,70,True,200,True,False,False\n"
            ).encode("utf-8"))
            out = Path(tmp) / "snap.json"
            code, payload, _ = self._run(
                ["import-prospects", "--csv", str(csv_path),
                 "--center-domain", "soundbuttons.cc", "--out", str(out)],
                self._workspace(tmp),
            )
            self.assertEqual(code, 0)
            code, validated, _ = self._run(
                ["validate-snapshot", "--file", str(out), "--center-domain", "soundbuttons.cc"],
                self._workspace(tmp),
            )
            self.assertEqual(code, 0)
            self.assertEqual(validated["prospects"], 1)

    def test_record_then_status_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._workspace(tmp)
            code, payload, _ = self._run(
                ["record", "--target-domain", "mixkit.co", "--channel", "email",
                 "--action", "sent", "--address", "e@mixkit.co",
                 "--provider-id", "msg_1", "--at", "2026-11-01T09:00:00Z"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertTrue(payload["appended"])
            self.assertEqual(payload["state"]["state"], "sent")

            code, duplicate, _ = self._run(
                ["record", "--target-domain", "mixkit.co", "--channel", "email",
                 "--action", "sent", "--address", "e@mixkit.co",
                 "--provider-id", "msg_1", "--at", "2026-11-01T09:00:00Z"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertFalse(duplicate["appended"])
            self.assertTrue(duplicate["duplicate"])

            code, status, _ = self._run(["status"], env)
            self.assertEqual(code, 0)
            self.assertEqual(status["summary"]["channels"], 1)
            self.assertEqual(status["rows"][0]["state"], "sent")

    def test_record_rejects_a_send_without_provider_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self._run(
                ["record", "--target-domain", "a.com", "--channel", "email",
                 "--action", "sent", "--address", "e@a.com"],
                self._workspace(tmp),
            )
            self.assertEqual(code, 1)
            self.assertIn("provider id", stderr)

    def test_forms_register_then_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._workspace(tmp)
            code, payload, _ = self._run(
                ["forms", "check", "--action", "https://mixkit.co/submit",
                 "--fields", "name,email,message"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertFalse(payload["known"])

            code, registered, _ = self._run(
                ["forms", "register", "--domain", "mixkit.co",
                 "--action", "https://mixkit.co/submit", "--fields", "name,email,message",
                 "--map", "name=soundbuttons.cc", "--reviewed-by", "hou"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertEqual(registered["entry"]["status"], "verified")

            code, known, _ = self._run(
                ["forms", "check", "--action", "https://mixkit.co/submit",
                 "--fields", "message,name,email"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertTrue(known["known"])
    def test_poll_receipts_records_a_delivery(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            env = self._workspace(tmp)
            sent_at = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._run(
                ["record", "--target-domain", "mixkit.co", "--channel", "email",
                 "--action", "sent", "--address", "e@mixkit.co",
                 "--provider-id", "msg_1", "--at", sent_at],
                env,
            )
            fixture = Path(tmp) / "fixture.jsonl"
            fixture.write_text(json.dumps({"provider_id": "msg_1", "status": "delivered"}) + "\n",
                               encoding="utf-8")
            code, payload, _ = self._run(
                ["poll-receipts", "--provider", "resend", "--fixture", str(fixture),
                 "--auto-confirm"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertEqual(payload["polls"], 1)
            self.assertEqual(payload["by_status"]["delivered"], 1)
            self.assertEqual(payload["events_written"], 1)

            code, status, _ = self._run(["status"], env)
            self.assertEqual(code, 0)
            self.assertEqual(status["rows"][0]["state"], "delivered")

    def test_poll_receipts_needs_no_key_when_nothing_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(self._workspace(tmp), BDL_PROVIDER_API_KEY="")
            code, payload, _ = self._run(
                ["poll-receipts", "--provider", "resend"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertEqual(payload["polls"], 0)

    def test_poll_receipts_refuses_without_provider(self):
        code, _, stderr = self._run(["poll-receipts"], {"BDL_AUTO_TRANSMIT": "false",
                                                        "BDL_PROVIDER": ""})
        self.assertEqual(code, 1)
        self.assertIn("CONFIG_ERROR", stderr)

    def test_poll_receipts_maps_one_status_word(self):
        code, payload, _ = self._run(
            ["poll-receipts", "--provider", "resend", "--map-status", "complained"],
            {"BDL_AUTO_TRANSMIT": "false"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "bounced")


if __name__ == "__main__":
    unittest.main()
