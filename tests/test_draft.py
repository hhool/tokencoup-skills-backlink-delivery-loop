"""Drafting mail: rendering, file shape, and the footprint guard.

The guard is the part worth testing hardest. It is the only thing standing between
"generated 40 pitches" and "sent the same pitch 40 times", and a duplicate is the
one mistake that cannot be undone by editing a file afterwards.
"""

import email
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bdl.errors import PolicyError
from bdl.models import ManifestRow
from bdl.services.draft import draft_batch, footprint_score, masked
from bdl.templates import TemplateSet, similarity

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)
CENTER = "soundbuttons.cc"


def make_row(domain="example.com", status="ready_to_submit", channels=("email",),
             email_addr=None, channel_type="asset", target_url=None):
    return ManifestRow(
        prospect_id=domain + "#" + channel_type,
        center_domain=CENTER,
        target_domain=domain,
        candidate_url="https://" + domain,
        target_url=target_url,
        submission_url=None if "form" not in channels else "https://" + domain + "/submit",
        own_url="https://soundbuttons.cc/free-soundboard",
        contact_email=email_addr if email_addr is not None else "hi@" + domain,
        contact_state="candidate",
        channel_type=channel_type,
        type_key="resource",
        status=status,
        idempotency_key="key-" + domain,
        channels=list(channels),
    )


class SimilarityTests(unittest.TestCase):
    def test_identical_copy_scores_one(self):
        text = "We run a small site and would like to suggest a resource."
        self.assertEqual(similarity(text, text), 1.0)

    def test_unrelated_copy_scores_low(self):
        left = "We run a small site and would like to suggest a resource."
        right = "Completely different words about bicycles, rain and metric spanners."
        self.assertLess(similarity(left, right), 0.2)

    def test_empty_copy_is_not_similar(self):
        self.assertEqual(similarity("", "anything at all here"), 0.0)


class VariantTests(unittest.TestCase):
    def test_every_builtin_channel_offers_a_second_body(self):
        templates = TemplateSet.builtin()
        for channel in ("asset", "guest_post", "directory", "community", "journalist", "other"):
            with self.subTest(channel=channel):
                self.assertGreaterEqual(len(templates.body_variants(channel)), 2)

    def test_variants_render_distinctly(self):
        templates = TemplateSet.builtin()
        context = {
            "center_domain": CENTER,
            "own_site_base": "https://soundbuttons.cc",
            "own_url": "https://soundbuttons.cc/free-soundboard",
            "target_domain": "example.com",
            "candidate_url": "https://example.com",
            "target_url": "",
            "submission_url": "",
            "contact_email": "hi@example.com",
        }
        variants = templates.render_variants("asset", context)
        self.assertGreaterEqual(len(variants), 2)
        self.assertNotEqual(variants[0]["email"]["body"], variants[1]["email"]["body"])

    def test_default_render_is_the_primary_body(self):
        templates = TemplateSet.builtin()
        context = {
            "center_domain": CENTER, "own_site_base": "https://soundbuttons.cc",
            "own_url": "https://soundbuttons.cc/x", "target_domain": "example.com",
            "candidate_url": "https://example.com", "target_url": "",
            "submission_url": "", "contact_email": "hi@example.com",
        }
        self.assertEqual(templates.render("asset", context)["variant"], 0)

    def test_blocks_scale_with_the_batch(self):
        # Seven asset rows in one batch would be seven identical pitches if a
        # channel only offered two bodies. Composition is what makes room.
        templates = TemplateSet.builtin()
        bodies = templates.body_variants("asset")
        self.assertGreaterEqual(len(bodies), 7)
        self.assertLessEqual(len(bodies), 24)
        self.assertEqual(len(set(bodies)), len(bodies))

    def test_seven_asset_rows_all_draft(self):
        rows = [make_row("site%d.com" % index) for index in range(7)]
        with tempfile.TemporaryDirectory() as tmp:
            report = draft_batch(
                rows,
                templates=TemplateSet.builtin(),
                center_domain=CENTER,
                own_site_base="https://soundbuttons.cc",
                from_addr="outreach@soundbuttons.cc",
                out_dir=tmp,
                now=NOW,
            )
            self.assertEqual(report["counts"]["drafted"], 7)
            self.assertEqual(report["counts"]["duplicates_blocked"], 0)
            bodies = {item["body"] for item in report["drafted"]}
            self.assertEqual(len(bodies), 7)


class DraftBatchTests(unittest.TestCase):
    def _draft(self, rows, ceiling=0.80, write=True, **kwargs):
        out = kwargs.pop("out", None) or tempfile.mkdtemp()
        return draft_batch(
            rows,
            templates=kwargs.pop("templates", TemplateSet.builtin()),
            center_domain=CENTER,
            own_site_base="https://soundbuttons.cc",
            from_addr="outreach@soundbuttons.cc",
            out_dir=out,
            ceiling=ceiling,
            now=NOW,
            write=write,
            **kwargs,
        )

    def test_two_domains_get_two_variants(self):
        rows = [make_row("alpha.com"), make_row("beta.com")]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._draft(rows, out=tmp)
            self.assertEqual(report["counts"]["drafted"], 2)
            self.assertEqual(report["counts"]["variants_used"], 2)
            bodies = [item["body"] for item in report["drafted"]]
            self.assertNotEqual(bodies[0], bodies[1])

    def test_a_single_variant_forces_a_refusal_not_a_duplicate(self):
        # One body only: the second domain cannot differ, so it is skipped. This is
        # the case that would otherwise ship forty identical pitches.
        templates = TemplateSet.builtin()
        templates.templates["asset"]["email"]["body_blocks"] = {}  # pin one body
        templates.templates["asset"]["email"]["body_variants"] = [
            templates.templates["asset"]["email"]["body"]
        ]
        rows = [make_row("alpha.com"), make_row("beta.com")]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._draft(rows, templates=templates, out=tmp)
            self.assertEqual(report["counts"]["drafted"], 1)
            self.assertEqual(report["counts"]["duplicates_blocked"], 1)
            skipped = [item for item in report["skipped"] if item["reason"] == "near_duplicate"][0]
            self.assertEqual(skipped["closest"], "alpha.com")
            self.assertIn("variant", skipped["hint"])

    def test_the_same_template_with_a_new_domain_is_a_duplicate(self):
        # This is the case raw Jaccard misses: one body, domain swapped, lands
        # around 0.76 and would slip under an 0.80 ceiling. Masked comparison sees
        # the identical structure and scores it as the same copy.
        templates = TemplateSet.builtin()
        context_a = self._context("alpha.com")
        context_b = self._context("beta.com")
        # `other` is the channel whose primary body names the target domain, which
        # is the case raw similarity is blind to.
        body_a = templates.render("other", context_a)["email"]["body"]
        body_b = templates.render("other", context_b)["email"]["body"]
        raw = similarity(body_a, body_b)
        self.assertLess(raw, 0.80)  # what the naive comparison would have allowed
        score = footprint_score(
            body_a, body_b,
            masked(body_a, list(context_a.values())),
            masked(body_b, list(context_b.values())),
        )
        self.assertGreaterEqual(score, 0.95)

    def test_two_variants_clear_the_ceiling(self):
        templates = TemplateSet.builtin()
        context = self._context("alpha.com")
        variants = templates.render_variants("asset", context)
        score = similarity(variants[0]["email"]["body"], variants[1]["email"]["body"])
        self.assertLess(score, 0.80)

    @staticmethod
    def _context(domain):
        return {
            "center_domain": CENTER,
            "own_site_base": "https://soundbuttons.cc",
            "own_url": "https://soundbuttons.cc/free-soundboard",
            "target_domain": domain,
            "candidate_url": "https://" + domain,
            "target_url": "",
            "submission_url": "",
            "contact_email": "hi@" + domain,
        }

    def test_blocked_rows_are_skipped_for_being_blocked(self):
        rows = [make_row("alpha.com", status="blocked")]
        report = self._draft(rows, write=False)
        self.assertEqual(report["skipped"][0]["reason"], "status_blocked")

    def test_a_form_only_row_is_not_a_mail(self):
        row = make_row("alpha.com", channels=("form",), email_addr=None)
        report = self._draft([row], write=False)
        self.assertEqual(report["skipped"][0]["reason"], "no_email_channel")

    def test_a_missing_address_is_named(self):
        row = make_row("alpha.com", channels=("email",), email_addr=None)
        row.contact_email = ""
        report = self._draft([row], write=False)
        self.assertIn(report["skipped"][0]["reason"], {"no_email_channel", "no_address"})

    def test_cjk_copy_is_never_drafted(self):
        templates = TemplateSet.builtin()
        cjk = "\u4f60\u597d\uff0c\u8fd9\u662f {{ target_domain }} \u7684\u5185\u5bb9\u3002"
        templates.templates["asset"]["email"]["body_blocks"] = {}
        templates.templates["asset"]["email"]["body"] = cjk
        templates.templates["asset"]["email"]["body_variants"] = [cjk]
        report = self._draft([make_row("alpha.com")], templates=templates, write=False)
        self.assertEqual(report["counts"]["drafted"], 0)
        self.assertIn(report["skipped"][0]["reason"], {"policy", "template_refused"})

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._draft([make_row("alpha.com")], out=tmp, write=False)
            self.assertEqual(report["counts"]["drafted"], 1)
            self.assertEqual(list(Path(tmp).glob("*.eml")), [])
            self.assertEqual(report["drafted"][0]["path"], "")


class EmlFileTests(unittest.TestCase):
    def test_the_file_is_a_readable_message_with_provenance(self):
        row = make_row("alpha.com")
        with tempfile.TemporaryDirectory() as tmp:
            report = draft_batch(
                [row],
                templates=TemplateSet.builtin(),
                center_domain=CENTER,
                own_site_base="https://soundbuttons.cc",
                from_addr="outreach@soundbuttons.cc",
                from_name="Sound Buttons",
                out_dir=tmp,
                now=NOW,
            )
            path = Path(report["drafted"][0]["path"])
            self.assertTrue(path.is_file())
            message = email.message_from_bytes(path.read_bytes())
            self.assertEqual(message["To"], "hi@alpha.com")
            self.assertEqual(message["From"], "Sound Buttons <outreach@soundbuttons.cc>")
            self.assertIn("alpha.com", message["Subject"])
            self.assertEqual(message["X-BDL-Target-Domain"], "alpha.com")
            self.assertEqual(message["X-BDL-Idempotency-Key"], "key-alpha.com")
            self.assertEqual(message["Message-ID"], "<key-alpha.com@soundbuttons.cc>")
            body = message.get_payload(decode=True).decode("utf-8")
            self.assertIn("soundbuttons.cc", body)
            self.assertNotIn("{{", body)

    def test_an_attachmentless_plain_text_message_is_still_valid_rfc822(self):
        row = make_row("beta.com")
        with tempfile.TemporaryDirectory() as tmp:
            draft_batch(
                [row], templates=TemplateSet.builtin(), center_domain=CENTER,
                own_site_base="https://soundbuttons.cc",
                from_addr="outreach@soundbuttons.cc", out_dir=tmp, now=NOW,
            )
            message = email.message_from_bytes((Path(tmp) / "beta.com.eml").read_bytes())
            self.assertEqual(message.get_content_type(), "text/plain")
            self.assertIsNotNone(message["Date"])


class ManifestRoundTripTests(unittest.TestCase):
    """A manifest row is a plain dict; drafting must accept what build-manifest wrote."""

    def test_rows_survive_json(self):
        row = make_row("alpha.com")
        payload = json.loads(json.dumps(row.__dict__))
        rebuilt = ManifestRow(**payload)
        self.assertEqual(rebuilt.channels, ["email"])
        report = draft_batch(
            [rebuilt], templates=TemplateSet.builtin(), center_domain=CENTER,
            own_site_base="https://soundbuttons.cc", from_addr="a@b.com",
            out_dir=tempfile.mkdtemp(), now=NOW,
        )
        self.assertEqual(report["counts"]["drafted"], 1)


if __name__ == "__main__":
    unittest.main()
