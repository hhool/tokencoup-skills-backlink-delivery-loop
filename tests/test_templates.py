import json
import tempfile
import unittest
from pathlib import Path

from bdl.errors import PolicyError
from bdl.templates import CHANNEL_TYPES, TemplateSet

BASE_CONTEXT = {
    "center_domain": "soundbuttons.cc",
    "own_site_base": "https://soundbuttons.cc",
    "own_url": "https://soundbuttons.cc/launch",
    "target_domain": "example.com",
    "candidate_url": "https://example.com/resources",
    "target_url": "https://example.com/resources",
    "submission_url": "https://example.com/submit",
    "contact_email": "editor@example.com",
}


class TemplateTests(unittest.TestCase):
    def test_builtin_renders_all_channels(self):
        ts = TemplateSet.builtin()
        for channel in CHANNEL_TYPES:
            rendered = ts.render(channel, BASE_CONTEXT)
            self.assertNotIn("{{", rendered["email"]["subject"])
            self.assertNotIn("{{", rendered["email"]["body"])
            for value in rendered["form"]["fields"].values():
                self.assertNotIn("{{", value)

    def test_rendered_copy_has_no_cjk(self):
        ts = TemplateSet.builtin()
        rendered = ts.render("asset", BASE_CONTEXT)
        self.assertNotRegex(rendered["email"]["body"], r"[\u3400-\u9fff]")

    def test_unknown_channel_falls_back_to_other(self):
        ts = TemplateSet.builtin()
        rendered = ts.render("not_a_channel", BASE_CONTEXT)
        self.assertTrue(rendered["email"]["subject"])

    def test_cjk_template_rejected(self):
        ts = TemplateSet.builtin()
        # "\u4e2d\u6587\u5b57\u7b26\u4e32" is Chinese text, kept as escapes so the
        # English path stays ASCII-only (see bdl/adapters/prose.py).
        ts.templates["asset"]["email"]["subject"] = "\u4e2d\u6587\u5b57\u7b26\u4e32 {{ target_domain }}"
        with self.assertRaises(PolicyError):
            ts.render("asset", BASE_CONTEXT)

    def test_unreplaced_placeholder_in_form_rejected(self):
        ts = TemplateSet.builtin()
        ts.templates["asset"]["form"]["fields"]["extra"] = "{{ missing_var }}"
        with self.assertRaises(PolicyError):
            ts.render("asset", BASE_CONTEXT)

    def test_center_domain_override_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = {"asset": {"email": {"subject": "Custom {{ target_domain }} pitch", "body": "Body"}}}
            (Path(tmp) / "soundbuttons.cc.json").write_text(json.dumps(override), encoding="utf-8")
            ts = TemplateSet.load(tmp, "soundbuttons.cc")
            rendered = ts.render("asset", BASE_CONTEXT)
            self.assertEqual(rendered["email"]["subject"], "Custom example.com pitch")
            # other channels keep builtin defaults
            self.assertIn("guest", ts.render("guest_post", BASE_CONTEXT)["email"]["subject"].lower() or "guest")

    def test_generic_override_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = {"directory": {"form": {"fields": {"url": "{{ own_url }}"}}}}
            (Path(tmp) / "templates.json").write_text(json.dumps(override), encoding="utf-8")
            ts = TemplateSet.load(tmp, "other.cc")
            self.assertEqual(ts.render("directory", BASE_CONTEXT)["form"]["fields"]["url"],
                             "https://soundbuttons.cc/launch")

    def test_unknown_channel_in_override_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "templates.json").write_text(json.dumps({"bogus": {}}), encoding="utf-8")
            with self.assertRaises(Exception):
                TemplateSet.load(tmp, "x")


if __name__ == "__main__":
    unittest.main()
