import tempfile
import unittest
import zipfile
from pathlib import Path

from bdl.package import build_package


class PackageTests(unittest.TestCase):
    def test_zip_contains_skill_and_excludes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skill-src"
            (root / "references").mkdir(parents=True)
            (root / ".git").mkdir()
            (root / "data").mkdir()
            (root / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
            (root / "references" / "guide.md").write_text("guide", encoding="utf-8")
            (root / ".git" / "config").write_text("secret", encoding="utf-8")
            (root / "data" / "state.json").write_text("{}", encoding="utf-8")
            (root / ".env").write_text("KEY=value", encoding="utf-8")

            archive = build_package(root, Path(tmp) / "dist")
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
            self.assertIn("skill-src/SKILL.md", names)
            self.assertIn("skill-src/references/guide.md", names)
            self.assertFalse(any("/.git/" in name for name in names))
            self.assertFalse(any("/data/" in name for name in names))
            self.assertFalse(any(name.endswith("/.env") for name in names))

    def test_missing_skill_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                build_package(Path(tmp), Path(tmp) / "dist")


if __name__ == "__main__":
    unittest.main()
