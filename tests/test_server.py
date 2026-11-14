"""The review server: one write path, guarded by the same rules as `record`."""

import json
import tempfile
import threading
import unittest
import urllib.error  # noqa: F401
import urllib.parse
import urllib.request
from pathlib import Path

from bdl.server import ReviewState, serve


MANIFEST = {
    "center_domain": "soundbuttons.cc",
    "batch_id": "t",
    "rows": [
        {
            "prospect_id": "example.com#asset", "center_domain": "soundbuttons.cc",
            "target_domain": "example.com", "status": "ready_to_submit",
            "channel_type": "asset", "channels": ["email"],
            "contact_email": "editor@example.com", "own_url": "https://soundbuttons.cc/boards.html",
            "idempotency_key": "k1",
        },
        {
            "prospect_id": "form.org#asset", "center_domain": "soundbuttons.cc",
            "target_domain": "form.org", "status": "ready_to_submit",
            "channel_type": "asset", "channels": ["form"],
            "submission_url": "https://form.org/contact",
            "own_url": "https://soundbuttons.cc/boards.html",
            "idempotency_key": "k2",
        },
        {
            "prospect_id": "dead.io#asset", "center_domain": "soundbuttons.cc",
            "target_domain": "dead.io", "status": "no_channel",
            "channel_type": "asset", "channels": [],
            "idempotency_key": "k3",
        },
    ],
}

EML = (
    "From: hello@soundbuttons.cc\r\n"
    "To: editor@example.com\r\n"
    "Subject: Resource suggestion\r\n"
    "X-BDL-Target-Domain: example.com\r\n"
    "X-BDL-Idempotency-Key: k1\r\n"
    "\r\n"
    "Hello,\r\n\r\nPlease consider listing soundbuttons.cc.\r\n"
)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "manifest").mkdir()
        (base / "manifest" / "delivery-manifest.json").write_text(json.dumps(MANIFEST))
        (base / "drafts").mkdir()
        (base / "drafts" / "example.com.eml").write_text(EML)
        (base / "records").mkdir()
        self.base = base
        # A real config with the repo's templates and a temp prose dir, so the
        # /import chain runs end to end without touching the repo's data/.
        import os

        repo = Path(__file__).resolve().parent.parent
        prose = base / "prose"
        prose.mkdir()
        (prose / "todo.md").write_text(
            "# TODO\n\n## B-1 mail + form track\n\n"
            "| Target | Landing page | Entry |\n|---|---|---|\n"
            "| example.com | /boards.html | editor@example.com |\n"
            "| new-site.net | /boards.html | hi@new-site.net |\n",
            encoding="utf-8",
        )
        from bdl.config import Config

        self.cfg = Config.from_env({
            "BDL_HOME": str(repo),
            "BDL_CENTER_DOMAIN": "soundbuttons.cc",
            "BDL_FROM_EMAIL": "hello@soundbuttons.cc",
            "BDL_RECORDS_DIR": str(base / "records"),
            "BDL_OUT_DIR": str(base / "out"),
            "BDL_PROSE_DIR": str(prose),
        })
        self.url, self.server = serve(
            manifest_path=str(base / "manifest"),
            drafts_dir=str(base / "drafts"),
            records_dir=str(base / "records"),
            prose_dir=str(prose),
            cfg=self.cfg,
            workdir=str(base / "workdir"),
            port=0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.url.rstrip("/") + path, timeout=5) as r:
            return r.status, r.read().decode("utf-8")

    def _post(self, body):
        request = urllib.request.Request(
            self.url.rstrip("/") + "/record",
            data=urllib.parse.urlencode(body).encode("utf-8"), method="POST",
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=5) as r:
                return r.status, dict(r.headers), r.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8")

    def test_dashboard_lists_rows_and_drafts(self):
        status, markup = self._get("/")
        self.assertEqual(status, 200)
        for needle in ("example.com", "form.org", "dead.io",
                       "ready_to_submit", "no_channel", "Resource suggestion"):
            self.assertIn(needle, markup)

    def test_draft_preview_and_raw_download(self):
        status, markup = self._get("/drafts/example.com")
        self.assertIn("Please consider listing soundbuttons.cc", markup)
        status, raw = self._get("/drafts/example.com/raw")
        self.assertEqual(status, 200)
        self.assertIn("X-BDL-Idempotency-Key: k1", raw)

    def test_record_appends_a_validated_event(self):
        status, headers, _ = self._post({
            "target_domain": "example.com", "channel": "email", "action": "queued",
            "actor": "hou",
        })
        self.assertEqual(status, 303)
        events = (self.base / "records" / "events-soundbuttons.cc.jsonl").read_text()
        event = json.loads(events.strip())
        self.assertEqual(event["action"], "queued")
        self.assertEqual(event["actor"], "hou")

    def test_record_refuses_a_sent_without_provider_id(self):
        status, headers, _ = self._post({
            "target_domain": "example.com", "channel": "email", "action": "sent",
        })
        self.assertEqual(status, 303)
        self.assertIn("error", headers.get("Location", ""))
        self.assertFalse((self.base / "records" / "events-soundbuttons.cc.jsonl").exists())

    def test_record_refuses_an_unknown_action(self):
        status, headers, _ = self._post({
            "target_domain": "example.com", "channel": "email", "action": "live",
        })
        self.assertEqual(status, 303)
        self.assertIn("error", headers.get("Location", ""))

    def test_duplicate_record_is_not_appended_twice(self):
        body = {"target_domain": "example.com", "channel": "email", "action": "queued"}
        self._post(body)
        self._post(body)
        lines = (self.base / "records" / "events-soundbuttons.cc.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)

    def test_projection_shows_the_recorded_state(self):
        self._post({"target_domain": "example.com", "channel": "email", "action": "queued"})
        _, markup = self._get("/")
        self.assertIn("queued", markup)
        state = ReviewState(str(self.base / "manifest"), str(self.base / "drafts"),
                            str(self.base / "records"))
        projected = state.projected()
        self.assertEqual(projected[("example.com", "email")]["state"], "queued")

    def _upload(self, csv_text, center=""):
        boundary = "bdltestboundary"
        parts = []
        if center:
            parts.append(
                "--%s\r\nContent-Disposition: form-data; name=\"center_domain\"\r\n\r\n%s\r\n"
                % (boundary, center).encode("utf-8")
            )
        parts.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"csv\"; filename=\"prospects.csv\"\r\n"
             "Content-Type: text/csv\r\n\r\n" % boundary).encode("utf-8")
            + csv_text.encode("utf-8") + b"\r\n"
        )
        parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
        body = b"".join(parts)
        request = urllib.request.Request(
            self.url.rstrip("/") + "/import",
            data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=15) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers)

CSV = (
    "domain,url,type_key,priority,http_status,valid,already_have,is_competitor,is_gap\n"
    "example.com,https://example.com,resource,3,200,true,false,false,true\n"
    "new-site.net,https://new-site.net,resource,3,200,true,false,false,true\n"
    "rival.com,https://rival.com,partner,2,200,true,false,true,false\n"
    "absent.org,https://absent.org,resource,3,200,true,false,false,true\n"
)

class ImportChainTest(ServerTest):
    """The CSV upload -> snapshot -> manifest -> drafts chain."""

    def test_import_builds_a_manifest_and_drafts(self):
        status, headers = self._upload(CSV)
        self.assertEqual(status, 303)
        location = headers.get("Location", "")
        self.assertIn("flash", location)
        # The dashboard now shows the imported batch.
        status, markup = self._get("/")
        self.assertIn("new-site.net", markup)
        self.assertIn("rival.com", markup)
        # The prose contacts turned two rows into emailable drafts.
        drafts = self.base / "workdir" / "drafts"
        self.assertTrue((drafts / "example.com.eml").is_file())
        self.assertTrue((drafts / "new-site.net.eml").is_file())
        body = (drafts / "example.com.eml").read_text(encoding="utf-8")
        self.assertIn("editor@example.com", body)

    def test_import_is_idempotent_per_upload_and_keeps_the_ledger(self):
        self._upload(CSV)
        self._upload(CSV)
        # Two runs, two manifest batches, and no transmission of anything.
        outs = list((self.base / "workdir" / "out").iterdir())
        self.assertEqual(len(outs), 2)
        self.assertFalse((self.base / "records" / "events-soundbuttons.cc.jsonl").exists())

    def test_import_refuses_a_csv_without_a_domain_column(self):
        status, headers = self._upload("nope,not,a,csv\n1,2,3,4\n")
        self.assertEqual(status, 303)
        self.assertIn("error", headers.get("Location", ""))
        # The old dashboard state is untouched.
        status, markup = self._get("/")
        self.assertIn("form.org", markup)

    def test_import_page_renders(self):
        status, markup = self._get("/import")
        self.assertEqual(status, 200)
        self.assertIn("import-prospects", markup)
