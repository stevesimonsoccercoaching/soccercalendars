"""Test guarded publication pushes using only disposable local Git remotes."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from soccer_publication_push import push_publication_commit


class PublicationPushTests(unittest.TestCase):

    def git(self, root, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def write(self, root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, root, message, *, allow_empty=False):
        self.git(root, "add", "-A")
        args = [
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", message,
        ]
        if allow_empty:
            args.append("--allow-empty")
        self.git(root, *args)
        return self.git(root, "rev-parse", "HEAD")

    def fixture(self):
        temp = tempfile.TemporaryDirectory(
            prefix="soccer-publication-push-test-"
        )
        self.addCleanup(temp.cleanup)

        base = Path(temp.name)
        remote = base / "remote.git"
        work = base / "publisher"
        work.mkdir()

        self.git(
            base, "init", "--bare", "--quiet",
            "--initial-branch=main", str(remote),
        )
        self.git(work, "init", "-q", "-b", "main")

        self.write(work, "docs/index.html", "original\n")
        initial = self.commit(work, "Initial publication")

        self.git(work, "remote", "add", "origin", str(remote))
        self.git(work, "push", "-q", "-u", "origin", "main")

        return work, remote, initial

    def remote_head(self, remote):
        return self.git(
            remote, "rev-parse", "refs/heads/main"
        )

    def candidate(self, work):
        self.write(work, "docs/index.html", "updated\n")
        return self.commit(work, "Candidate publication")

    def test_authorized_commit_pushes_successfully(self):
        work, remote, base = self.fixture()
        head = self.candidate(work)

        result = push_publication_commit(
            work, base, ["docs/index.html"]
        )

        self.assertEqual(result, head)
        self.assertEqual(self.remote_head(remote), head)
        self.assertEqual(
            self.git(work, "status", "--porcelain"), ""
        )

    def test_remote_advanced_is_rejected(self):
        work, remote, base = self.fixture()
        head = self.candidate(work)

        peer = work.parent / "competitor"
        self.git(
            work.parent, "clone", "-q", "--branch", "main",
            str(remote), str(peer),
        )

        self.write(peer, "docs/index.html", "competing update\n")
        competing_head = self.commit(peer, "Competing update")
        self.git(peer, "push", "-q", "origin", "main")

        with self.assertRaisesRegex(
            RuntimeError, "remote main differs"
        ):
            push_publication_commit(
                work, base, ["docs/index.html"]
            )

        self.assertEqual(
            self.remote_head(remote), competing_head
        )
        self.assertEqual(
            self.git(work, "rev-parse", "HEAD"), head
        )

    def test_wrong_branch_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)
        self.git(work, "checkout", "-q", "-b", "development")

        with self.assertRaisesRegex(
            RuntimeError, "branch must be main"
        ):
            push_publication_commit(
                work, base, ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_wrong_parent_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        with self.assertRaisesRegex(
            RuntimeError, "parent differs"
        ):
            push_publication_commit(
                work, "f" * 40, ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_unauthorized_committed_file_is_rejected(self):
        work, remote, base = self.fixture()

        self.write(work, "docs/index.html", "updated\n")
        self.write(work, "unexpected.txt", "not authorized\n")
        self.commit(work, "Mixed publication")

        with self.assertRaisesRegex(
            RuntimeError, "unauthorized committed paths"
        ):
            push_publication_commit(
                work, base, ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_dirty_working_tree_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)
        self.write(work, "untracked.txt", "unexpected\n")

        with self.assertRaisesRegex(
            RuntimeError, "not clean"
        ):
            push_publication_commit(
                work, base, ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_empty_publication_commit_is_rejected(self):
        work, remote, base = self.fixture()
        self.commit(
            work, "Empty publication", allow_empty=True
        )

        with self.assertRaisesRegex(
            RuntimeError, "empty publication commit"
        ):
            push_publication_commit(
                work, base, ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_invalid_base_sha_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        with self.assertRaisesRegex(
            ValueError, "invalid base commit SHA"
        ):
            push_publication_commit(
                work, "HEAD", ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_missing_remote_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)
        self.git(work, "remote", "remove", "origin")

        with self.assertRaises(subprocess.CalledProcessError):
            push_publication_commit(
                work, base, ["docs/index.html"]
            )

        self.assertEqual(self.remote_head(remote), base)


    def test_remote_advances_between_check_and_push(self):
        from unittest.mock import patch
        from soccer_publication_push import git as original_git

        work, remote, base = self.fixture()
        candidate_head = self.candidate(work)

        peer = work.parent / "competitor"
        self.git(
            work.parent, "clone", "-q", "--branch", "main",
            str(remote), str(peer),
        )

        self.write(peer, "docs/index.html", "competing update\\n")
        competing_head = self.commit(peer, "Competing publication")

        advanced = False

        def advance_after_remote_check(root, *args):
            nonlocal advanced

            result = original_git(root, *args)

            if not advanced and args == (
                "ls-remote", "--exit-code",
                "origin", "refs/heads/main",
            ):
                advanced = True
                self.git(peer, "push", "-q", "origin", "main")

            return result

        with patch(
            "soccer_publication_push.git",
            side_effect=advance_after_remote_check,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                push_publication_commit(
                    work, base, ["docs/index.html"]
                )

        self.assertTrue(advanced)
        self.assertEqual(self.remote_head(remote), competing_head)
        self.assertEqual(
            self.git(work, "rev-parse", "HEAD"), candidate_head
        )


if __name__ == "__main__":
    unittest.main()
