import tempfile
import unittest
from pathlib import Path

from bdl.adapters.forms import check, fingerprint, listing, register, show
from bdl.errors import DeliveryError

CENTER = "soundbuttons.cc"


class FingerprintTests(unittest.TestCase):
    def test_field_order_does_not_matter(self):
        self.assertEqual(
            fingerprint("POST", "https://x.com/submit", ["name", "email", "message"]),
            fingerprint("post", "https://x.com/submit", ["message", "email", "name"]),
        )

    def test_a_different_field_set_is_a_different_form(self):
        self.assertNotEqual(
            fingerprint("POST", "https://x.com/submit", ["name", "email", "message"]),
            fingerprint("POST", "https://x.com/submit", ["name", "email"]),
        )

    def test_a_different_action_is_a_different_form(self):
        self.assertNotEqual(
            fingerprint("POST", "https://x.com/contact", ["name", "email"]),
            fingerprint("POST", "https://x.com/submit", ["name", "email"]),
        )


class RegistryTests(unittest.TestCase):
    def _register(self, directory, **kwargs):
        base = dict(
            domain="mixkit.co",
            method="POST",
            action="https://mixkit.co/submit",
            field_names=["name", "email", "message"],
            field_map={"name": CENTER, "email": "hello@" + CENTER},
            reviewed_by="hou",
            success_selector="#thanks",
        )
        base.update(kwargs)
        return register(directory, **base)

    def test_unseen_form_requires_human_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = check(tmp, "POST", "https://mixkit.co/submit",
                           ["name", "email", "message"])
        self.assertFalse(result["known"])
        self.assertEqual(result["status"], "needs_human_verify")

    def test_register_then_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = self._register(tmp)
            self.assertEqual(entry["status"], "verified")
            found = check(tmp, "POST", "https://mixkit.co/submit",
                          ["message", "email", "name"])
            self.assertTrue(found["known"])
            self.assertEqual(found["entry"]["reviewed_by"], "hou")
            self.assertEqual(len(listing(tmp)), 1)
            self.assertEqual(show(tmp, key=entry["fingerprint"])[0]["domain"], "mixkit.co")

    def test_register_requires_a_named_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeliveryError):
                self._register(tmp, reviewed_by="   ")

    def test_register_requires_field_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DeliveryError):
                self._register(tmp, field_names=[])

    def test_captcha_form_is_manual_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = self._register(tmp, captcha=True)
        self.assertEqual(entry["status"], "manual_only")

    def test_registry_persists_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._register(tmp)
            listing_rows = listing(tmp)
            self.assertEqual(listing_rows[0]["fingerprint"], first["fingerprint"])
            self.assertTrue((Path(tmp) / "registry.json").is_file())


if __name__ == "__main__":
    unittest.main()
