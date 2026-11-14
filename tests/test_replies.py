"""Reply reading and classification.

Two risks dominate here, and the tests take them head-on. First, a mis-match: a
reply filed against the wrong domain produces an event that says "example.com
declined" when example.com said nothing of the kind. Second, a mis-class: "we'd
love to add it, there is a $50 fee" must land on `pay`, because recording it as
`yes` starts a negotiation the policy forbids (R-510).
"""

import json
import tempfile
import unittest
from pathlib import Path

from bdl.adapters.mailbox import read_messages
from bdl.services.replies import classify, match_target, propose, propose_from_path

EML = """From: Editor <editor@example.com>
To: hello@soundbuttons.cc
Subject: Re: Resource suggestion for example.com
Date: Sun, 15 Nov 2026 08:00:00 +0000
Message-ID: <reply-1@example.com>
In-Reply-To: <key-example.com@soundbuttons.cc>

We had a look and your guide has been added to the page.
Thanks for the submission.
"""

PAY_EML = """From: Editor <editor@example.com>
Subject: Re: Resource suggestion
Date: Sun, 15 Nov 2026 08:00:00 +0000
Content-Type: text/html; charset=utf-8

<html><body><p>Great site! We would love to add it.</p>
<p>Note there is a $50 listing fee to cover review.</p></body></html>
"""

FREE_EML = """From: Editor <editor@example.com>
Subject: Re: Resource suggestion

Happy to include your page, and there is no fee for non-commercial sites.
Your link has been added.
"""

NEGOTIATE_EML = """From: Editor <editor@example.com>
Subject: partnership

We do link exchanges - you add a link to us and we add one to you.
"""

REJECT_EML = """From: Editor <editor@example.com>
Subject: Re: Resource suggestion

Unfortunately we are not accepting new resources at the moment.
"""

BOUNCE_EML = """From: Mail Delivery System <mailer-daemon@example.com>
Subject: Undelivered Mail
Content-Type: multipart/report; report-type=delivery-status;
    boundary="=_boundary"

--=_boundary
Content-Type: text/plain

The address hi@example.com does not exist.

--=_boundary
Content-Type: message/delivery-status

Reporting-MTA: dns; mx.example.com

Final-Recipient: rfc822; hi@example.com
Action: failed
Status: 5.1.1

--=_boundary--
"""


def write(tmp, name, text):
    path = Path(tmp) / name
    path.write_text(text, encoding="utf-8")
    return path


class MailboxTests(unittest.TestCase):
    def test_plain_eml(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = read_messages(write(tmp, "reply.eml", EML))
        self.assertEqual(1, len(messages))
        self.assertEqual("editor@example.com", messages[0]["address"])
        self.assertIn("has been added", messages[0]["text"])
        self.assertFalse(messages[0]["is_dsn"])

    def test_html_body_is_reduced_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = read_messages(write(tmp, "pay.eml", PAY_EML))
        text = messages[0]["text"]
        self.assertIn("listing fee", text)
        self.assertNotIn("<p>", text)

    def test_a_directory_reads_every_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "a.eml", EML)
            write(tmp, "b.eml", PAY_EML)
            (Path(tmp) / "notes.txt").write_text("not mail", encoding="utf-8")
            messages = read_messages(tmp)
        self.assertEqual(2, len(messages))

    def test_a_dsn_is_recognised_with_its_status_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            messages = read_messages(write(tmp, "bounce.eml", BOUNCE_EML))
        self.assertTrue(messages[0]["is_dsn"])
        self.assertEqual("5.1.1", messages[0]["dsn_status"])


class ClassifyTests(unittest.TestCase):
    def test_a_yes_with_two_signals_is_high_confidence(self):
        verdict = classify("Re: Resource suggestion", "Your guide has been added, thank you for your submission.")
        self.assertEqual("yes", verdict["reply_class"])
        self.assertEqual("high", verdict["confidence"])
        self.assertFalse(verdict["needs_human"])

    def test_pay_beats_yes_when_both_appear(self):
        verdict = classify("Re: Resource suggestion", PAY_EML.split("\n\n", 1)[1])
        self.assertEqual("pay", verdict["reply_class"])

    def test_no_fee_is_not_a_pay_signal(self):
        verdict = classify("Re: Resource suggestion", FREE_EML.split("\n\n", 1)[1])
        self.assertEqual("yes", verdict["reply_class"])

    def test_reciprocal_catches_the_exchange_pitch(self):
        verdict = classify("partnership", "We do link exchanges with related sites.")
        self.assertEqual("reciprocal", verdict["reply_class"])

    def test_a_polite_no_is_no(self):
        verdict = classify("Re: Resource suggestion", REJECT_EML.split("\n\n", 1)[1])
        self.assertEqual("no", verdict["reply_class"])

    def test_nothing_matched_needs_a_human(self):
        verdict = classify("hello", "Thanks for reaching out, have a nice week.")
        self.assertEqual("", verdict["reply_class"])
        self.assertTrue(verdict["needs_human"])


class MatchTests(unittest.TestCase):
    def _message(self, **headers):
        base = {"in_reply_to": "", "references": "", "from": ""}
        base.update(headers)
        return base

    def test_in_reply_to_names_the_row(self):
        target, via = match_target(
            self._message(in_reply_to="<key-looperman.com@soundbuttons.cc>"),
            ["looperman.com"],
            {"key-looperman.com": "looperman.com"},
        )
        self.assertEqual("looperman.com", target)
        self.assertEqual("in_reply_to", via)

    def test_from_domain_matches_a_known_target(self):
        target, via = match_target(self._message(**{"from": "Editor <staff@head-fi.org>"}), ["head-fi.org"])
        self.assertEqual("head-fi.org", target)
        self.assertEqual("from_domain", via)

    def test_an_unknown_domain_is_unmatched_not_guessed(self):
        target, via = match_target(self._message(**{"from": "someone@elsewhere.net"}), ["head-fi.org"])
        self.assertEqual("", target)
        self.assertEqual("unmatched", via)


class ProposeTests(unittest.TestCase):
    def _run(self, tmp, auto_confirm=False, manifest_rows=()):
        messages = read_messages(write(tmp, "yes.eml", EML))
        messages += read_messages(write(tmp, "pay.eml", PAY_EML))
        messages += read_messages(write(tmp, "bounce.eml", BOUNCE_EML))
        return propose(
            messages,
            known_domains=["example.com"],
            rows=manifest_rows,
            auto_confirm=auto_confirm,
            proposal_path=Path(tmp) / "reply-proposals.jsonl",
        )

    def test_every_reply_is_a_proposal_with_its_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp)
        classes = {item["reply_class"]: item for item in report["proposals"] if item["action"] == "reply"}
        self.assertEqual("yes", classes["yes"]["reply_class"])
        self.assertEqual("pay", classes["pay"]["reply_class"])
        self.assertTrue(classes["pay"]["needs_human"])
        self.assertIn("fee", " ".join(classes["pay"]["signals"]) or "fee")

    def test_the_bounce_is_a_transport_receipt_not_a_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp)
        bounce = [item for item in report["proposals"] if item["action"] == "bounced"]
        self.assertEqual(1, len(bounce))
        self.assertEqual("5.1.1", bounce[0]["dsn_status"])

    def test_pay_is_never_auto_confirmable(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, auto_confirm=True)
        auto = {item["reply_class"] for item in report["auto_confirmable"]}
        self.assertNotIn("pay", auto)
        self.assertNotIn("reciprocal", auto)

    def test_proposals_are_written_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            rows = [json.loads(line) for line in
                    (Path(tmp) / "reply-proposals.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(3, len(rows))
        self.assertEqual("reply_proposal", rows[0]["kind"])

    def test_an_unmatched_reply_is_reported_not_written(self):
        messages = read_messages(write(tempfile.mkdtemp(), "x.eml", """From: a@stranger.net
Subject: hello

Is this the right address?
"""))
        report = propose(messages, known_domains=["example.com"])
        self.assertEqual(0, len(report["proposals"]))
        self.assertEqual(1, len(report["unmatched"]))
        self.assertIn("hint", report["unmatched"][0])

    def test_path_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "yes.eml", EML)
            report = propose_from_path(path, known_domains=["example.com"],
                                       proposal_path=Path(tmp) / "q.jsonl")
        self.assertEqual(1, report["matched"])


if __name__ == "__main__":
    unittest.main()
