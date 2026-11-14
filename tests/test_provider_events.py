"""Offline tests for the provider-event reader. The reader is always stubbed."""

import json
import os
import tempfile
import unittest
import urllib.request
from pathlib import Path

from bdl.adapters.provider_events import (
    USER_AGENT,
    classify_status,
    default_reader,
    fixture_reader,
    provider_config,
    read_status,
    register_provider,
    request_url,
)
from bdl.errors import ConfigError, DeliveryError

RESEND = provider_config("resend")


def _reader(status, body):
    def _read(url):
        return status, body if isinstance(body, str) else json.dumps(body)
    return _read


def _boom(url):
    raise DeliveryError("Provider read failed", reason="timeout")


class TestClassifyStatus(unittest.TestCase):
    def test_delivered_and_bounced_are_recordable(self):
        self.assertEqual(classify_status("resend", "delivered"), ("delivered", ""))
        self.assertEqual(classify_status("resend", "bounced")[0], "bounced")

    def test_open_and_click_imply_delivery(self):
        self.assertEqual(classify_status("resend", "opened")[0], "delivered")
        self.assertEqual(classify_status("resend", "clicked")[0], "delivered")

    def test_complaint_is_a_bounce(self):
        self.assertEqual(classify_status("resend", "complained")[0], "bounced")

    def test_delay_is_pending_not_failure(self):
        self.assertEqual(classify_status("resend", "delivery_delayed")[0], "pending")

    def test_unknown_word_stays_unknown(self):
        status, note = classify_status("resend", "quantum_uncertain")
        self.assertEqual(status, "unknown")
        self.assertIn("unmapped", note)

    def test_empty_word_is_unknown(self):
        self.assertEqual(classify_status("resend", "")[0], "unknown")

    def test_status_words_are_case_insensitive(self):
        self.assertEqual(classify_status("Postmark", "Delivered")[0], "delivered")


class TestReadStatus(unittest.TestCase):
    def test_delivered_body_records_action(self):
        record = read_status(
            _reader(200, {"id": "re_1", "last_event": "delivered"}),
            "resend", RESEND, "re_1", "2026-11-15T00:00:00Z",
        )
        self.assertEqual(record["status"], "delivered")
        self.assertEqual(record["action"], "delivered")
        self.assertEqual(record["raw_status"], "delivered")
        self.assertEqual(record["observed_at"], "2026-11-15T00:00:00Z")

    def test_pending_status_records_no_action(self):
        record = read_status(
            _reader(200, {"last_event": "delivery_delayed"}),
            "resend", RESEND, "re_2", "2026-11-15T00:00:00Z",
        )
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["action"], "")

    def test_401_is_unknown_not_bounced(self):
        record = read_status(_reader(401, {"error": "unauthorized"}), "resend", RESEND, "re_3", "t")
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(record["action"], "")
        self.assertIn("refused the key", record["note"])

    def test_404_is_unknown_not_bounced(self):
        record = read_status(_reader(404, {}), "resend", RESEND, "re_4", "t")
        self.assertEqual(record["status"], "unknown")

    def test_429_is_unknown_not_bounced(self):
        record = read_status(_reader(429, {}), "resend", RESEND, "re_5", "t")
        self.assertEqual(record["status"], "unknown")
        self.assertIn("429", record["note"])

    def test_500_is_unknown_not_bounced(self):
        record = read_status(_reader(503, ""), "resend", RESEND, "re_6", "t")
        self.assertEqual(record["status"], "unknown")

    def test_read_failure_is_unknown_not_bounced(self):
        record = read_status(_boom, "resend", RESEND, "re_7", "t")
        self.assertEqual(record["status"], "unknown")
        self.assertIn("timeout", record["note"])

    def test_non_json_body_is_unknown(self):
        record = read_status(_reader(200, "<html>gateway</html>"), "resend", RESEND, "re_8", "t")
        self.assertEqual(record["status"], "unknown")

    def test_json_array_body_is_unknown(self):
        record = read_status(_reader(200, [1, 2]), "resend", RESEND, "re_9", "t")
        self.assertEqual(record["status"], "unknown")


class TestProviderConfig(unittest.TestCase):
    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ConfigError):
            provider_config("carrier-pigeon")

    def test_empty_provider_is_refused(self):
        with self.assertRaises(ConfigError):
            provider_config("")

    def test_base_url_override_rewrites_template(self):
        config = provider_config("resend", "https://api.example.test")
        self.assertEqual(request_url(config, "re_1"), "https://api.example.test/emails/re_1")

    def test_postmark_uses_its_own_status_field(self):
        config = provider_config("postmark")
        self.assertEqual(config["status_field"], "Status")

    def test_register_provider_accepts_id_placeholder(self):
        register_provider(
            "acme",
            {"url": "https://acme.test/v1/messages/<id>", "auth": "bearer", "status_field": "state"},
            {"done": ("delivered", "")},
        )
        self.assertEqual(request_url(provider_config("acme"), "abc"), "https://acme.test/v1/messages/abc")
        self.assertEqual(classify_status("acme", "done")[0], "delivered")

    def test_register_provider_rejects_incomplete_config(self):
        with self.assertRaises(DeliveryError):
            register_provider("half", {"url": "https://x.test/{id}"}, {})


class TestFixtureReader(unittest.TestCase):
    def test_reads_status_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.jsonl"
            path.write_text(
                json.dumps({"provider_id": "re_1", "status": "delivered"}) + "\n"
                + json.dumps({"provider_id": "re_2", "status": "bounced"}) + "\n",
                encoding="utf-8",
            )
            reader = fixture_reader(path)
            record = read_status(reader, "resend", RESEND, "re_1", "t")
            self.assertEqual(record["status"], "delivered")
            missing = read_status(reader, "resend", RESEND, "re_9", "t")
            self.assertEqual(missing["http_status"], 404)

    def test_missing_fixture_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            fixture_reader(os.path.join(tempfile.gettempdir(), "no-such-fixture.jsonl"))


class TestDefaultReader(unittest.TestCase):
    """The reader builds a request but never opens a socket: the opener is replaced."""

    def _capture(self, status=200, body='{"last_event":"delivered"}'):
        captured = {}

        class _Response:
            def __init__(self, code, text):
                self._code, self._text = code, text

            def getcode(self):
                return self._code

            def read(self):
                return self._text.encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Opener:
            def open(self, request, timeout=None):
                captured["headers"] = dict(request.headers)
                captured["url"] = request.full_url
                return _Response(status, body)

        original = urllib.request.build_opener
        urllib.request.build_opener = lambda *a, **k: _Opener()
        self.addCleanup(setattr, urllib.request, "build_opener", original)
        return captured

    def test_sends_an_explicit_user_agent(self):
        # A default urllib agent is refused by the provider's CDN with Cloudflare
        # 1010, which the ledger would record as unknown forever.
        captured = self._capture()
        default_reader("resend", RESEND, api_key="re_test")(
            "https://api.resend.com/emails/re_1"
        )
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers["user-agent"], USER_AGENT)
        self.assertEqual(headers["authorization"], "Bearer re_test")

    def test_postmark_uses_its_own_auth_header(self):
        captured = self._capture()
        default_reader("postmark", provider_config("postmark"), api_key="pm_test")(
            "https://api.postmarkapp.com/messages/outbound/1/details"
        )
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers["x-postmark-server-token"], "pm_test")
        self.assertNotIn("authorization", headers)

    def test_missing_key_is_a_config_error(self):
        original = os.environ.pop("BDL_PROVIDER_API_KEY", None)
        self.addCleanup(
            lambda: os.environ.__setitem__("BDL_PROVIDER_API_KEY", original)
            if original is not None
            else None
        )
        with self.assertRaises(ConfigError):
            default_reader("resend", RESEND)


if __name__ == "__main__":
    unittest.main()
