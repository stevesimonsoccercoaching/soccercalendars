"""Test transfer and guarded push using disposable Git repositories."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import soccer_publication_push
from soccer_publication_transfer import transfer_candidate_commit
from soccer_publication_push import push_publication_commit
from plan_soccer_builds import plan
from soccer_publication_paths import expected_publication_paths


class PublicationPipelineTests(unittest.TestCase):

    def git(self, root, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def write(self, root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, root, message):
        self.git(root, "add", "-A")
        self.git(
            root,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", message,
        )
        return self.git(root, "rev-parse", "HEAD")

    def test_transfer_then_guarded_push(self):
        with tempfile.TemporaryDirectory(
            prefix="soccer-publication-pipeline-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            candidate = parent / "candidate"
            publisher.mkdir()

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            self.write(
                publisher, "docs/index.html", "Original index\n"
            )
            self.write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Pipeline Test Event",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            events, errors, _warnings = plan(publisher)
            self.assertFalse(errors)
            self.assertTrue(events)

            for relative in expected_publication_paths(
                publisher, events
            ):
                path = publisher / relative
                if path.exists():
                    continue
                if relative.endswith(".ics"):
                    content = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
                elif relative.endswith(".json"):
                    content = "{}\n"
                else:
                    self.fail("Unexpected publication path: " + relative)
                self.write(publisher, relative, content)

            base = self.commit(publisher, "Production base")

            self.git(
                publisher, "remote", "add",
                "origin", str(remote),
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--no-hardlinks",
                str(publisher), str(candidate),
            )
            self.git(
                candidate, "checkout", "-q", "--detach", base
            )
            self.git(candidate, "remote", "remove", "origin")

            self.write(
                candidate, "docs/index.html", "Updated index\n"
            )
            candidate_sha = self.commit(
                candidate, "Candidate publication"
            )

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                transferred = transfer_candidate_commit(
                    publisher, candidate, base, candidate_sha
                )
                self.assertEqual(transferred, candidate_sha)

                # Transfer must not publish.
                self.assertEqual(
                    self.git(remote, "rev-parse", "refs/heads/main"),
                    base,
                )

                # Explicitly invoke the guarded publisher.
                published = push_publication_commit(
                    publisher, base
                )

            self.assertEqual(published, candidate_sha)
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                candidate_sha,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                candidate_sha,
            )
            self.assertEqual(self.git(candidate, "remote"), "")
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"),
                "",
            )

            # Create another candidate after the first publication.
            self.write(
                candidate, "docs/index.html", "Second candidate\n"
            )
            second_sha = self.commit(
                candidate, "Second candidate publication"
            )

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                transferred_again = transfer_candidate_commit(
                    publisher, candidate, candidate_sha, second_sha
                )
                self.assertEqual(transferred_again, second_sha)

                # Transfer must not advance the remote.
                self.assertEqual(
                    self.git(remote, "rev-parse", "refs/heads/main"),
                    candidate_sha,
                )

                # Another publisher updates remote main.
                peer = parent / "competitor"
                self.git(
                    parent, "clone", "-q", "--branch", "main",
                    str(remote), str(peer),
                )
                self.write(
                    peer, "docs/index.html", "Competing update\n"
                )
                competing_sha = self.commit(
                    peer, "Competing publication"
                )
                self.git(peer, "push", "-q", "origin", "main")

                # Reject our outdated candidate.
                with self.assertRaisesRegex(
                    RuntimeError, "remote main differs"
                ):
                    push_publication_commit(
                        publisher, candidate_sha
                    )

            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                competing_sha,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                second_sha,
            )
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"),
                "",
            )


    def test_generated_candidate_transfers_and_publishes(self):
        """Exercise candidate preparation, transfer, and guarded push."""
        import shutil

        with tempfile.TemporaryDirectory(
            prefix="soccer-generated-pipeline-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            candidate = parent / "candidate"
            publisher.mkdir()

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            manifest = (
                "config/2026-27/cjsl/league/events/55206/"
                "manifest.json"
            )
            feed = "docs/test-calendar/test-team.ics"

            self.write(publisher, "docs/index.html", "Original index\n")
            self.write(
                publisher, manifest,
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Generated Pipeline Test",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            events, errors, _warnings = plan(publisher)
            self.assertFalse(errors)
            self.assertTrue(events)

            for relative in expected_publication_paths(
                publisher, events
            ):
                destination = publisher / relative
                if destination.exists():
                    continue
                if relative.endswith(".ics"):
                    content = (
                        "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
                    )
                elif relative.endswith(".json"):
                    content = "{}\n"
                else:
                    self.fail(
                        "Unexpected publication path: " + relative
                    )
                self.write(publisher, relative, content)

            # Real candidate-preparation orchestration.
            for name in (
                "soccer_paths.py",
                "soccer_publication_paths.py",
                "rehearse_soccer_publication.py",
                "soccer_publication_candidate.py",
                "prepare_soccer_publication_candidate.py",
            ):
                destination = publisher / "scripts" / name
                destination.parent.mkdir(
                    parents=True, exist_ok=True
                )
                shutil.copyfile(SCRIPTS / name, destination)

            # Deterministic builder replaces external GotSport access.
            self.write(
                publisher, "scripts/run_soccer_builds.py",
                "from pathlib import Path\n"
                "import subprocess\n"
                "if subprocess.check_output(['git', 'remote']).strip():\n"
                "    raise SystemExit('Candidate has a Git remote')\n"
                f"Path({feed!r}).write_text("
                "'BEGIN:VCALENDAR\\nVERSION:2.0\\n"
                "END:VCALENDAR\\n')\n",
            )

            self.write(
                publisher, "scripts/plan_soccer_builds.py",
                "def plan(root):\n"
                f"    return ([{{'manifest': {manifest!r}, "
                "'missing_match_sync': False}], [], [])\n",
            )

            self.write(
                publisher, "scripts/prepare_soccer_checkpoints.py",
                "def prepare_checkpoints(root, events, head):\n"
                "    return None\n",
            )

            base = self.commit(publisher, "Production input")
            self.git(
                publisher, "remote", "add",
                "origin", str(remote),
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--no-hardlinks",
                str(publisher), str(candidate),
            )
            self.git(
                candidate, "checkout", "-q", "--detach", base
            )
            self.git(candidate, "remote", "remove", "origin")
            self.git(candidate, "config", "user.name", "Test")
            self.git(
                candidate, "config", "user.email",
                "test@example.com",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/prepare_soccer_publication_candidate.py",
                    "--head", base,
                ],
                cwd=candidate,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                result.stdout + "\n" + result.stderr,
            )

            candidate_sha = self.git(
                candidate, "rev-parse", "HEAD"
            )
            self.assertNotEqual(candidate_sha, base)
            self.assertEqual(
                self.git(candidate, "rev-parse", "HEAD^"), base
            )
            self.assertEqual(
                self.git(
                    candidate, "diff", "--name-only",
                    base, candidate_sha,
                ),
                feed,
            )
            self.assertEqual(self.git(candidate, "remote"), "")
            self.assertEqual(
                self.git(candidate, "status", "--porcelain"), ""
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                base,
            )

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                transferred = transfer_candidate_commit(
                    publisher, candidate, base, candidate_sha
                )
                self.assertEqual(transferred, candidate_sha)

                # Transfer alone must never publish.
                self.assertEqual(
                    self.git(remote, "rev-parse", "refs/heads/main"),
                    base,
                )

                published = push_publication_commit(
                    publisher, base
                )

            self.assertEqual(published, candidate_sha)
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                candidate_sha,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                candidate_sha,
            )
            self.assertIn(
                "VERSION:2.0",
                self.git(publisher, "show", f"HEAD:{feed}"),
            )
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"), ""
            )


if __name__ == "__main__":
    unittest.main()
