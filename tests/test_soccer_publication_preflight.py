"""Test production publication preflight without touching production."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_soccer_publication_preflight import check_preflight


class PublicationPreflightTests(unittest.TestCase):

    def git(self, root, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def fixture(self):
        temp = tempfile.TemporaryDirectory(
            prefix="soccer-preflight-test-"
        )
        self.addCleanup(temp.cleanup)

        base = Path(temp.name)
        remote = base / "remote.git"
        work = base / "work"
        work.mkdir()

        self.git(
            base, "init", "--bare", "--quiet",
            "--initial-branch=main", str(remote)
        )
        self.git(work, "init", "-q", "-b", "main")

        (work / "README.md").write_text(
            "Test repository\n", encoding="utf-8"
        )

        self.git(work, "add", "README.md")
        self.git(
            work,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", "Initial test commit",
        )

        self.git(work, "remote", "add", "origin", str(remote))
        self.git(work, "push", "-q", "-u", "origin", "main")

        return work, remote, self.git(work, "rev-parse", "HEAD")

    def test_clean_main_at_expected_remote_commit_passes(self):
        work, remote, head = self.fixture()

        check_preflight(work, head)

        self.assertEqual(
            self.git(work, "status", "--porcelain"), ""
        )
        self.assertEqual(self.git(work, "rev-parse", "HEAD"), head)

    def test_wrong_branch_is_rejected(self):
        work, remote, head = self.fixture()

        self.git(
            work, "checkout", "-q", "-b",
            "integration/multi-league-actions",
        )

        with self.assertRaisesRegex(
            RuntimeError, "checkout must be on main"
        ):
            check_preflight(work, head)

    def test_dirty_working_tree_is_rejected(self):
        work, remote, head = self.fixture()

        (work / "unexpected.txt").write_text(
            "Untracked file\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            RuntimeError, "working tree is not clean"
        ):
            check_preflight(work, head)

    def test_wrong_expected_commit_is_rejected(self):
        work, remote, head = self.fixture()

        with self.assertRaisesRegex(
            RuntimeError, "differs from expected commit"
        ):
            check_preflight(work, "f" * 40)

    def test_remote_advanced_is_rejected(self):
        work, remote, head = self.fixture()

        peer = work.parent / "peer"
        self.git(
            work.parent,
            "clone", "-q", "--branch", "main",
            str(remote), str(peer),
        )

        (peer / "update.txt").write_text(
            "Remote update\n", encoding="utf-8"
        )

        self.git(peer, "add", "update.txt")
        self.git(
            peer,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", "Advance remote",
        )
        self.git(peer, "push", "-q", "origin", "main")

        with self.assertRaisesRegex(
            RuntimeError, "remote main has advanced"
        ):
            check_preflight(work, head)

        self.assertEqual(
            self.git(work, "rev-parse", "HEAD"), head
        )
        self.assertEqual(
            self.git(work, "status", "--porcelain"), ""
        )

    def test_missing_remote_is_rejected(self):
        work, remote, head = self.fixture()

        self.git(work, "remote", "remove", "origin")

        with self.assertRaises(subprocess.CalledProcessError):
            check_preflight(work, head)

    def test_malformed_expected_commit_is_rejected(self):
        work, remote, head = self.fixture()

        with self.assertRaisesRegex(
            ValueError, "full commit SHA"
        ):
            check_preflight(work, "HEAD")


if __name__ == "__main__":
    unittest.main()
