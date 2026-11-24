"""Safety tests for the unified soccer publication orchestrator."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import publish_soccer_calendars


class SoccerPublicationOrchestratorTests(unittest.TestCase):

    def git(self, root, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def test_explicit_publish_flag_required(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "publish_soccer_calendars.py"),
                "--expected-head", "0" * 40,
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "Explicit --publish required", result.stderr
        )

    def test_invalid_base_rejected_before_git_operations(self):
        with patch.object(
            publish_soccer_calendars,
            "git",
            side_effect=AssertionError(
                "Git must not execute for an invalid SHA"
            ),
        ):
            with self.assertRaisesRegex(
                ValueError, "Full expected base SHA required"
            ):
                publish_soccer_calendars.publish_calendars(
                    Path.cwd(), "not-a-valid-sha"
                )

    def test_untrusted_remote_rejected_before_build(self):
        with tempfile.TemporaryDirectory(
            prefix="soccer-orchestrator-safety-"
        ) as tmp:
            root = Path(tmp)
            self.git(root, "init", "-q", "-b", "main")
            self.git(
                root, "remote", "add", "origin",
                "https://example.invalid/untrusted.git",
            )

            with (
                patch.object(
                    publish_soccer_calendars,
                    "check_preflight",
                ) as preflight,
                patch.object(
                    publish_soccer_calendars,
                    "prepare_candidate",
                ) as candidate,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "unauthorized or inconsistent remote",
                ):
                    publish_soccer_calendars.publish_calendars(
                        root, "0" * 40
                    )

                preflight.assert_not_called()
                candidate.assert_not_called()


    def test_no_change_does_not_publish(self):
        """A clean no-change candidate must not create a push."""
        import soccer_publication_push

        with tempfile.TemporaryDirectory(
            prefix="soccer-no-change-test-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            publisher.mkdir()

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            (publisher / "README.md").write_text(
                "Disposable test repository\\n",
                encoding="utf-8",
            )

            self.git(publisher, "add", "README.md")
            self.git(
                publisher,
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "Initial test commit",
            )
            base = self.git(publisher, "rev-parse", "HEAD")

            self.git(
                publisher, "remote", "add",
                "origin", str(remote),
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            with (
                patch.object(
                    soccer_publication_push,
                    "TRUSTED_PRODUCTION_REPOSITORY_URL",
                    str(remote),
                ),
                patch.object(
                    publish_soccer_calendars,
                    "prepare_candidate",
                    side_effect=RuntimeError(
                        "Candidate refused: empty staged publication"
                    ),
                ) as prepare,
                patch.object(
                    publish_soccer_calendars,
                    "transfer_candidate_commit",
                ) as transfer,
                patch.object(
                    publish_soccer_calendars,
                    "push_publication_commit",
                ) as push,
            ):
                result = (
                    publish_soccer_calendars.publish_calendars(
                        publisher, base
                    )
                )

            self.assertIsNone(result)
            prepare.assert_called_once()
            transfer.assert_not_called()
            push.assert_not_called()

            self.assertEqual(
                self.git(
                    remote, "rev-parse", "refs/heads/main"
                ),
                base,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                base,
            )
            self.assertEqual(
                self.git(
                    publisher, "status", "--porcelain"
                ),
                "",
            )


    def test_valid_candidate_publishes_to_disposable_remote(self):
        """The orchestrator transfers and pushes an authorized commit."""
        import json
        import soccer_publication_push
        from plan_soccer_builds import plan
        from soccer_publication_paths import expected_publication_paths

        with tempfile.TemporaryDirectory(
            prefix="soccer-orchestrator-success-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            publisher.mkdir()

            def write(root, relative, contents):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(contents, encoding="utf-8")

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            write(publisher, "docs/index.html", "Original index\n")
            write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Orchestrator Test",
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
                if (publisher / relative).exists():
                    continue
                if relative.endswith(".ics"):
                    contents = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
                elif relative.endswith(".json"):
                    contents = "{}\n"
                else:
                    self.fail("Unexpected output: " + relative)
                write(publisher, relative, contents)

            self.git(publisher, "add", "-A")
            self.git(
                publisher,
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "Initial production commit",
            )
            base = self.git(publisher, "rev-parse", "HEAD")

            self.git(
                publisher, "remote", "add", "origin", str(remote)
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            created = []

            def simulate_candidate(candidate, expected_base):
                self.assertEqual(expected_base, base)
                self.assertEqual(self.git(candidate, "remote"), "")
                self.assertEqual(
                    self.git(candidate, "rev-parse", "HEAD"), base
                )
                write(candidate, "docs/index.html", "Updated index\n")
                self.git(candidate, "add", "docs/index.html")
                self.git(
                    candidate,
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", "Test candidate",
                )
                sha = self.git(candidate, "rev-parse", "HEAD")
                created.append(sha)
                return sha, ["docs/index.html"]

            with (
                patch.object(
                    soccer_publication_push,
                    "TRUSTED_PRODUCTION_REPOSITORY_URL",
                    str(remote),
                ),
                patch.object(
                    publish_soccer_calendars,
                    "prepare_candidate",
                    side_effect=simulate_candidate,
                ) as prepare,
            ):
                published = publish_soccer_calendars.publish_calendars(
                    publisher, base
                )

            prepare.assert_called_once()
            self.assertEqual(published, created[0])
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                published,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                published,
            )
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"),
                "",
            )


    def test_build_failure_never_transfers_or_pushes(self):
        """A candidate-generation failure must stop publication."""
        import soccer_publication_push

        with tempfile.TemporaryDirectory(
            prefix="soccer-build-failure-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            publisher.mkdir()

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            (publisher / "README.md").write_text(
                "Test repository\n", encoding="utf-8"
            )
            self.git(publisher, "add", "README.md")
            self.git(
                publisher,
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "Initial commit",
            )
            base = self.git(publisher, "rev-parse", "HEAD")

            self.git(
                publisher, "remote", "add",
                "origin", str(remote),
            )
            self.git(
                publisher, "push", "-q", "-u",
                "origin", "main",
            )

            with (
                patch.object(
                    soccer_publication_push,
                    "TRUSTED_PRODUCTION_REPOSITORY_URL",
                    str(remote),
                ),
                patch.object(
                    publish_soccer_calendars,
                    "prepare_candidate",
                    side_effect=RuntimeError(
                        "Simulated calendar builder failure"
                    ),
                ) as prepare,
                patch.object(
                    publish_soccer_calendars,
                    "transfer_candidate_commit",
                ) as transfer,
                patch.object(
                    publish_soccer_calendars,
                    "push_publication_commit",
                ) as push,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Simulated calendar builder failure",
                ):
                    publish_soccer_calendars.publish_calendars(
                        publisher, base
                    )

            prepare.assert_called_once()
            transfer.assert_not_called()
            push.assert_not_called()

            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                base,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                base,
            )
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"),
                "",
            )


    def test_remote_advances_during_candidate_generation(self):
        """Reject a candidate when another publisher advances main."""
        import json
        import soccer_publication_push
        from plan_soccer_builds import plan
        from soccer_publication_paths import expected_publication_paths

        with tempfile.TemporaryDirectory(
            prefix="soccer-orchestrator-race-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            publisher.mkdir()

            def write(root, relative, contents):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(contents, encoding="utf-8")

            def commit(root, message):
                self.git(root, "add", "-A")
                self.git(
                    root,
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", message,
                )
                return self.git(root, "rev-parse", "HEAD")

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            write(publisher, "docs/index.html", "Original index\n")
            write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Orchestrator Race Test",
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
                if (publisher / relative).exists():
                    continue
                if relative.endswith(".ics"):
                    contents = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
                elif relative.endswith(".json"):
                    contents = "{}\n"
                else:
                    self.fail("Unexpected output: " + relative)
                write(publisher, relative, contents)

            base = commit(publisher, "Initial production commit")
            self.git(
                publisher, "remote", "add", "origin", str(remote)
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            competing = []

            def simulate_candidate(candidate, expected_base):
                self.assertEqual(expected_base, base)

                write(candidate, "docs/index.html", "Our candidate\n")
                candidate_sha = commit(candidate, "Our candidate")

                # Simulate a second workflow publishing first.
                peer = parent / "competitor"
                self.git(
                    parent, "clone", "-q", "--branch", "main",
                    str(remote), str(peer),
                )
                write(peer, "docs/index.html", "Competing update\n")
                competing_sha = commit(peer, "Competing publication")
                self.git(peer, "push", "-q", "origin", "main")
                competing.append(competing_sha)

                return candidate_sha, ["docs/index.html"]

            with (
                patch.object(
                    soccer_publication_push,
                    "TRUSTED_PRODUCTION_REPOSITORY_URL",
                    str(remote),
                ),
                patch.object(
                    publish_soccer_calendars,
                    "prepare_candidate",
                    side_effect=simulate_candidate,
                ),
                patch.object(
                    publish_soccer_calendars,
                    "push_publication_commit",
                ) as push,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "remote main has advanced"
                ):
                    publish_soccer_calendars.publish_calendars(
                        publisher, base
                    )

            push.assert_not_called()
            self.assertEqual(len(competing), 1)
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                competing[0],
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                base,
            )
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"),
                "",
            )


if __name__ == "__main__":
    unittest.main()
