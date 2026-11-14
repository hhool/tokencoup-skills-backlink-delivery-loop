"""Prefill packs and the receipt that closes the loop.

The value of this module is not "it types for you". It is that the second form of
a known shape costs nothing, and that the first one leaves behind a record of what
was filled. So the tests concentrate on three things: a pack never invents a value,
a receipt cannot be adopted without a human and a response page, and a map reviewed
on one site is reused only where the wire names actually match.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bdl.adapters import forms as registry
from bdl.errors import DeliveryError
from bdl.models import ManifestRow
from bdl.services.prefill import PACK_KIND, RECEIPT_KIND, build_packs, load_receipt
from bdl.templates import TemplateSet

NOW = datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc)
CENTER = "soundbuttons.cc"
FROM = "hello@" + CENTER
LANDING = "https://soundbuttons.cc/free-soundboard"


def make_row(domain="example.com", status="ready_to_submit", channels=("form",),
             channel_type="asset", submission_url=None, own_url=LANDING):
    return ManifestRow(
        prospect_id=domain + "#" + channel_type,
        center_domain=CENTER,
        target_domain=domain,
        candidate_url="https://" + domain,
        target_url=None,
        submission_url=submission_url if submission_url is not None else "https://" + domain + "/submit",
        own_url=own_url,
        contact_email="hi@" + domain,
        contact_state="candidate",
        channel_type=channel_type,
        type_key="resource",
        status=status,
        idempotency_key="key-" + domain,
        channels=list(channels),
    )


def build(rows, forms_dir="", prospects=(), from_email=FROM, write=False, out_dir=""):
    return build_packs(
        rows,
        templates=TemplateSet.builtin(),
        center_domain=CENTER,
        own_landing_url=LANDING,
        from_email=from_email,
        from_name="Sound Buttons",
        forms_dir=forms_dir,
        prospects=prospects,
        write=write,
        out_dir=out_dir,
    )


class PackTests(unittest.TestCase):
    def test_pack_carries_every_slot_the_form_can_ask_for(self):
        report = build([make_row()])
        self.assertEqual(1, report["counts"]["packed"])
        values = report["pack"]["targets"][0]["values"]
        for slot in ("name", "email", "url", "message", "subject"):
            self.assertTrue(values.get(slot), slot)

    def test_no_address_means_no_pack(self):
        report = build([make_row()], from_email="")
        self.assertEqual(0, report["counts"]["packed"])
        self.assertEqual("no_from_email", report["skipped"][0]["reason"])

    def test_a_row_without_a_form_url_is_skipped(self):
        row = make_row(channels=("email",))
        report = build([row])
        self.assertEqual("no_form_channel", report["skipped"][0]["reason"])

    def test_blocked_status_is_reported_before_anything_else(self):
        report = build([make_row(status="blocked")])
        self.assertEqual("status_blocked", report["skipped"][0]["reason"])

    def test_two_packs_do_not_repeat_the_same_message(self):
        report = build([make_row("a.com"), make_row("b.com"), make_row("c.com")])
        messages = [item["values"]["message"] for item in report["pack"]["targets"]]
        self.assertEqual(len(messages), len(set(messages)))

    def test_unknown_mapping_is_declared_not_hidden(self):
        report = build([make_row()])
        pack = report["pack"]["targets"][0]
        self.assertEqual("none", pack["mapping"])
        self.assertIn("mapping_unknown", pack["gaps"])

    def test_phone_is_never_filled(self):
        # A form whose only mappable field is a phone number must say so.
        with tempfile.TemporaryDirectory() as tmp:
            registry.register(
                tmp, domain="example.com", method="POST", action="https://example.com/submit",
                field_names=["your_name", "your_phone"],
                field_map={}, slot_map={"your_name": "name", "your_phone": "phone"},
                shape="sha256:" + "a" * 64, reviewed_by="hou",
            )
            report = build(
                [make_row()], forms_dir=tmp,
                prospects=[{"domain": "example.com", "form_fields": ["your_name", "your_phone"],
                            "form_shape": "sha256:" + "a" * 64}],
            )
        pack = report["pack"]["targets"][0]
        self.assertIn("slot_phone", pack["gaps"])
        self.assertNotIn("phone", pack["values"])


class ReuseTests(unittest.TestCase):
    def _reviewed(self, directory, domain="first.com", shape="sha256:" + "b" * 64):
        return registry.register(
            directory, domain=domain, method="POST",
            action="https://%s/wp-admin/admin-ajax.php" % domain,
            field_names=["wpforms[fields][0]", "wpforms[fields][1]", "wpforms[fields][2]"],
            field_map={}, shape=shape,
            slot_map={
                "wpforms[fields][0]": "name",
                "wpforms[fields][1]": "email",
                "wpforms[fields][2]": "url",
            },
            reviewed_by="hou",
        )

    def test_same_plugin_on_another_site_reuses_the_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._reviewed(tmp)
            shape = "sha256:" + "b" * 64
            report = build(
                [make_row("second.com")], forms_dir=tmp,
                prospects=[{
                    "domain": "second.com",
                    "form_fields": ["wpforms[fields][0]", "wpforms[fields][1]", "wpforms[fields][2]"],
                    "form_shape": shape,
                }],
            )
        pack = report["pack"]["targets"][0]
        self.assertEqual("shape", pack["mapping"])
        self.assertEqual("first.com", pack["reused_from"])

    def test_different_wire_names_do_not_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._reviewed(tmp)
            report = build(
                [make_row("second.com")], forms_dir=tmp,
                prospects=[{
                    "domain": "second.com",
                    # same slots, but a bespoke form: nothing tells us which field is which
                    "form_fields": ["f_1", "f_2", "f_3"],
                    "form_shape": "sha256:" + "b" * 64,
                }],
            )
        self.assertEqual("none", report["pack"]["targets"][0]["mapping"])

    def test_the_same_form_again_is_an_exact_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry.register(
                tmp, domain="example.com", method="POST",
                action="https://example.com/submit",
                field_names=["your_name", "your_email", "your_message"],
                field_map={}, slot_map={"your_name": "name", "your_email": "email",
                                        "your_message": "message"},
                shape="sha256:" + "c" * 64, reviewed_by="hou",
            )
            report = build(
                [make_row()], forms_dir=tmp,
                prospects=[{"domain": "example.com",
                            "form_fields": ["your_name", "your_email", "your_message"],
                            "form_shape": "sha256:" + "c" * 64}],
            )
        self.assertEqual("exact", report["pack"]["targets"][0]["mapping"])


class ReceiptTests(unittest.TestCase):
    def _receipt(self, **overrides):
        payload = {
            "kind": RECEIPT_KIND,
            "schema_version": "1.0.0",
            "receipt_id": "abc12345",
            "center_domain": CENTER,
            "target_domain": "example.com",
            "method": "POST",
            "submission_url": "https://example.com/submit",
            "response_url": "https://example.com/thanks",
            "posted_at": "2026-11-15T04:00:00Z",
            "field_names": ["your_name", "your_email", "your_message"],
            "slot_map": {"your_name": "name", "your_email": "email", "your_message": "message"},
            "values": {"name": "Sound Buttons"},
            "shape": "sha256:" + "d" * 64,
            "captcha_present": False,
            "submitted_by_human": True,
        }
        payload.update(overrides)
        return payload

    def test_a_receipt_registers_the_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = registry.adopt_receipt(tmp, self._receipt(), "hou")
            self.assertEqual("verified", entry["status"])
            self.assertEqual("hou", entry["reviewed_by"])
            self.assertEqual("email", entry["slot_map"]["your_email"])
            found = registry.lookup(
                tmp, "POST", "https://example.com/submit",
                ["your_name", "your_email", "your_message"],
            )
        self.assertEqual("exact", found["match"])

    def test_a_receipt_without_a_response_page_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeliveryError) as caught:
                registry.adopt_receipt(tmp, self._receipt(response_url=""), "hou")
        self.assertEqual("L1", caught.exception.to_dict()["error"]["detail"].get("rule"))

    def test_a_receipt_that_was_not_a_human_click_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeliveryError) as caught:
                registry.adopt_receipt(tmp, self._receipt(submitted_by_human=False), "hou")
        self.assertEqual("R-520", caught.exception.to_dict()["error"]["detail"].get("rule"))

    def test_a_receipt_without_a_slot_map_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeliveryError):
                registry.adopt_receipt(tmp, self._receipt(slot_map={}), "hou")

    def test_only_a_receipt_kind_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeliveryError):
                registry.adopt_receipt(tmp, self._receipt(kind="bdl.prefill_pack"), "hou")

    def test_receipt_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text(json.dumps(self._receipt()), encoding="utf-8")
            self.assertEqual("example.com", load_receipt(path)["target_domain"])

    def test_a_missing_receipt_is_a_clean_error(self):
        with self.assertRaises(DeliveryError):
            load_receipt("/nonexistent/receipt.json")


class PackFileTests(unittest.TestCase):
    def test_a_written_pack_names_itself_and_its_centre(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build([make_row()], write=True, out_dir=tmp)
            path = Path(report["path"])
            document = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(path.name.startswith("prefill-" + CENTER))
        self.assertEqual(PACK_KIND, document["kind"])
        self.assertEqual(CENTER, document["center_domain"])


if __name__ == "__main__":
    unittest.main()
