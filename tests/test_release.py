import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import release


class ReleaseScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "application").mkdir()
        (self.root / "application/version.py").write_text('__version__ = "v1.0.0"\n')
        for args in (
            ["init", "-b", "main"],
            ["config", "user.email", "test@example.invalid"],
            ["config", "user.name", "Release Test"],
            ["config", "commit.gpgSign", "false"],
            ["config", "tag.gpgSign", "false"],
            ["add", "."],
            ["commit", "-m", "Initial"],
        ):
            self.git(*args)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def run_release(self, *args):
        with patch("tools.release.ROOT", self.root), patch(
            "sys.argv", ["release.py", *args]
        ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            release.main()

    def test_version_commit_and_annotated_tag_match(self):
        self.run_release("v1.1.0")
        self.assertIn('"v1.1.0"', (self.root / "application/version.py").read_text())
        self.assertEqual(self.git("cat-file", "-t", "v1.1.0"), "tag")
        self.assertEqual(self.git("rev-parse", "v1.1.0^{}"), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_dirty_tree_invalid_duplicate_and_older_tags_do_not_change_head(self):
        original = self.git("rev-parse", "HEAD")
        with self.assertRaises(SystemExit):
            self.run_release("invalid")
        (self.root / "uncommitted.txt").write_text("user changes")
        with self.assertRaises(SystemExit):
            self.run_release("v1.1.0")
        (self.root / "uncommitted.txt").unlink()
        self.git("tag", "v1.1.0")
        for tag in ("v1.1.0", "v1.0.9"):
            with self.subTest(tag=tag), self.assertRaises(SystemExit):
                self.run_release(tag)
        self.assertEqual(self.git("rev-parse", "HEAD"), original)

    def test_dry_run_does_not_create_commit_tag_or_change_version(self):
        original = self.git("rev-parse", "HEAD")
        self.run_release("v1.1.0", "--publish", "--dry-run")
        self.assertEqual(self.git("rev-parse", "HEAD"), original)
        self.assertEqual(self.git("tag", "--list"), "")
        self.assertEqual(self.git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
