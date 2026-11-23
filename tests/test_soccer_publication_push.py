"""Test guarded publication pushes using only disposable local Git remotes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import soccer_publication_push
from soccer_publication_push import push_publication_commit
from unittest.mock import patch


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
        self.write(
            work,
            "config/2026-27/cjsl/league/events/55206/manifest.json",
            json.dumps(
                {
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Publication Test Event",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                },
                indent=2,
            ) + "\n",
        )

        initial = self.commit(work, "Initial publication")

        self.git(work, "remote", "add", "origin", str(remote))
        self.git(work, "push", "-q", "-u", "origin", "main")

        trusted_url = patch.object(
            soccer_publication_push,
            "TRUSTED_PRODUCTION_REPOSITORY_URL",
            str(remote),
        )
        trusted_url.start()
        self.addCleanup(trusted_url.stop)

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
            work, base
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
                work, base
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
                work, base
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_wrong_parent_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        with self.assertRaisesRegex(
            RuntimeError, "parent differs"
        ):
            push_publication_commit(
                work, "f" * 40
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
                work, base
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
                work, base
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
                work, base
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_invalid_base_sha_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        with self.assertRaisesRegex(
            ValueError, "invalid base commit SHA"
        ):
            push_publication_commit(
                work, "HEAD"
            )

        self.assertEqual(self.remote_head(remote), base)

    def test_missing_remote_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)
        self.git(work, "remote", "remove", "origin")

        with self.assertRaises(subprocess.CalledProcessError):
            push_publication_commit(
                work, base
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
                    work, base
                )

        self.assertTrue(advanced)
        self.assertEqual(self.remote_head(remote), competing_head)
        self.assertEqual(
            self.git(work, "rev-parse", "HEAD"), candidate_head
        )


    def test_separate_push_url_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        other = work.parent / "wrong-destination.git"
        self.git(
            work.parent, "init", "--bare", "-q",
            "--initial-branch=main", str(other),
        )

        self.git(
            work, "remote", "set-url",
            "--push", "origin", str(other),
        )

        with self.assertRaisesRegex(
            RuntimeError, "push destination"
        ):
            push_publication_commit(
                work, base
            )

        self.assertEqual(self.remote_head(remote), base)

        result = subprocess.run(
            [
                "git", "--git-dir", str(other),
                "show-ref", "--verify", "--quiet",
                "refs/heads/main",
            ],
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)


    def test_matching_but_unauthorized_repository_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        wrong = work.parent / "wrong-repository.git"

        self.git(
            work.parent,
            "clone", "--bare", "-q",
            str(remote), str(wrong),
        )

        # Fetch and push now agree, but on the wrong repository.
        self.git(
            work, "remote", "set-url",
            "origin", str(wrong),
        )

        with self.assertRaisesRegex(
            RuntimeError, "unauthorized repository"
        ):
            push_publication_commit(
                work, base
            )

        # Neither repository should receive the candidate commit.
        self.assertEqual(self.remote_head(remote), base)
        self.assertEqual(self.remote_head(wrong), base)


    def test_pushinstead_of_rewrite_is_rejected(self):
        work, remote, base = self.fixture()
        self.candidate(work)

        wrong = work.parent / "rewritten-destination.git"
        self.git(
            work.parent, "init", "--bare", "-q",
            "--initial-branch=main", str(wrong),
        )

        # Git config rewrites pushes without changing origin's stored URL.
        self.git(
            work, "config", "--local",
            f"url.{wrong}.pushInsteadOf", str(remote),
        )

        self.assertEqual(
            self.git(work, "remote", "get-url", "origin"),
            str(remote),
        )
        self.assertEqual(
            self.git(work, "remote", "get-url", "--push", "origin"),
            str(wrong),
        )

        with self.assertRaisesRegex(
            RuntimeError, "push destination"
        ):
            push_publication_commit(
                work, base
            )

        self.assertEqual(self.remote_head(remote), base)

        result = subprocess.run(
            [
                "git", "--git-dir", str(wrong),
                "show-ref", "--verify", "--quiet",
                "refs/heads/main",
            ],
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)


    def test_caller_cannot_self_authorize_unregistered_calendar(self):
        work, remote, base = self.fixture()

        self.write(
            work,
            "docs/unregistered-team.ics",
            "unauthorized calendar\\n",
        )
        self.commit(work, "Attempt unregistered calendar publication")

        # The caller deliberately authorizes an unregistered file.
        # The publisher must independently reject it.
        with self.assertRaisesRegex(
            RuntimeError, "unauthorized committed paths"
        ):
            push_publication_commit(
                work, base
            )

        self.assertEqual(self.remote_head(remote), base)


    def test_publication_cannot_modify_its_own_manifest(self):
        work, remote, base = self.fixture()

        manifest = (
            "config/2026-27/cjsl/league/events/55206/manifest.json"
        )

        registration = json.loads(
            (work / manifest).read_text(encoding="utf-8")
        )

        registration["teams"].append(
            {"slug": "unapproved-team"}
        )

        self.write(
            work,
            manifest,
            json.dumps(registration, indent=2) + "\n",
        )

        self.write(
            work,
            "docs/test-calendar/unapproved-team.ics",
            "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
        )

        self.commit(
            work,
            "Attempt to authorize calendar by changing manifest",
        )

        with self.assertRaisesRegex(
            RuntimeError, "unauthorized committed paths"
        ):
            push_publication_commit(work, base)

        self.assertEqual(self.remote_head(remote), base)


if __name__ == "__main__":
    unittest.main()
