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
        self.url, self.server = serve(
            manifest_path=str(base / "manifest"),
            drafts_dir=str(base / "drafts"),
            records_dir=str(base / "records"),
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
