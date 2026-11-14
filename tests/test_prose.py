import unittest

from bdl.adapters.prose import load_prose_contacts, parse_prose
from tests import PROSE


class ProseExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contacts = load_prose_contacts(PROSE)

    def test_table_contact_has_source_trace(self):
        found = [c for c in self.contacts if c.address == "contact@beebom.com"]
        self.assertEqual(len(found), 1)
        contact = found[0]
        self.assertEqual(contact.target_domain, "beebom.com")
        self.assertEqual(contact.state, "candidate")
        self.assertTrue(contact.content_hash.startswith("sha256:"))
        self.assertGreater(contact.line, 0)
        self.assertIn("beebom.com", contact.excerpt)

    def test_form_path_is_captured(self):
        found = [c for c in self.contacts if c.target_domain == "assethoard.com"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].form_url, "/contact")
        self.assertTrue(found[0].target_url_hint.startswith("https://assethoard.com/"))

    def test_exclusion_section_marks_excluded(self):
        found = [c for c in self.contacts if c.address == "support@excluded-example.com"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].state, "excluded")

    def test_placeholder_is_ambiguous(self):
        found = [c for c in self.contacts if c.target_domain == "unknown.example.com"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].state, "ambiguous")

    def test_content_hash_changes_with_source(self):
        original = parse_prose("# T\n\n| Target | Entry |\n|---|---|\n| a.com | x@a.com |\n", "a.md")
        changed = parse_prose("# T\n\n| Target | Entry |\n|---|---|\n| a.com | y@a.com |\n", "a.md")
        self.assertNotEqual(original[0].content_hash, changed[0].content_hash)

    def test_empty_directory_is_a_gap_not_a_failure(self):
        self.assertEqual(load_prose_contacts("/nonexistent/prose"), [])


if __name__ == "__main__":
    unittest.main()
