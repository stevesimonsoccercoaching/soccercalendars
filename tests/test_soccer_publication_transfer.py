"""Integration tests for transferring unpublished soccer commits."""

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


class PublicationTransferTests(unittest.TestCase):

    def git(self, root, *args):
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def write(self, root, relative, contents):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def commit(self, root, message):
        self.git(root, "add", "-A")
        self.git(
            root,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", message,
        )
        return self.git(root, "rev-parse", "HEAD")

    def populate_required_outputs(self, publisher):
        """Create all registered outputs in a disposable test fixture."""
        from plan_soccer_builds import plan
        from soccer_publication_paths import expected_publication_paths

        events, errors, _warnings = plan(publisher)
        if errors or not events:
            raise AssertionError(
                "Invalid transfer test fixture: "
                + repr(errors)
            )

        for relative in expected_publication_paths(publisher, events):
            destination = publisher / relative

            if destination.exists() or destination.is_symlink():
                continue

            if relative.endswith(".ics"):
                contents = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
            elif relative.endswith(".json"):
                contents = "{}\n"
            elif relative == "docs/index.html":
                contents = "original index\n"
            else:
                raise AssertionError(
                    "Unrecognized fixture output: " + relative
                )

            self.write(publisher, relative, contents)

    def test_verified_candidate_transfers_without_publishing(self):
        with tempfile.TemporaryDirectory(
            prefix="soccer-transfer-test-"
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
                publisher, "docs/index.html", "original index\n"
            )
            self.write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Transfer Test Event",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            self.populate_required_outputs(publisher)
            base = self.commit(publisher, "Input revision")

            self.git(
                publisher, "remote", "add",
                "origin", str(remote),
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--local",
                "--no-hardlinks", str(publisher), str(candidate),
            )
            self.git(
                candidate, "checkout", "-q", "--detach", base
            )
            self.git(candidate, "remote", "remove", "origin")

            self.write(
                candidate, "docs/index.html", "updated index\n"
            )
            candidate_sha = self.commit(
                candidate, "Candidate publication"
            )

            # Matching fetch/push URLs do not make a remote trusted.
            from soccer_publication_transfer import (
                transfer_candidate_commit,
            )

            with self.assertRaisesRegex(
                RuntimeError, "unauthorized or inconsistent remote"
            ):
                transfer_candidate_commit(
                    publisher, candidate, base, candidate_sha
                )

            # Refusal must occur before import or branch modification.
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"), base
            )
            self.assertNotEqual(
                subprocess.run(
                    ["git", "cat-file", "-e", candidate_sha],
                    cwd=publisher,
                    capture_output=True,
                    check=False,
                ).returncode,
                0,
            )

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                from soccer_publication_transfer import (
                    transfer_candidate_commit,
                )

                # A candidate must have the exact expected parent.
                with self.assertRaisesRegex(
                    RuntimeError, "candidate parent mismatch"
                ):
                    transfer_candidate_commit(
                        publisher, candidate, "f" * 40, candidate_sha
                    )

                # Rejection must leave local main unchanged.
                self.assertEqual(
                    self.git(publisher, "rev-parse", "HEAD"),
                    base,
                )

                # Deleting a registered calendar is not permitted.
                calendar = (
                    candidate / "docs/test-calendar/test-team.ics"
                )
                self.assertTrue(calendar.is_file())
                calendar.unlink()

                self.git(
                    candidate, "add", "-A", "--",
                    "docs/test-calendar/test-team.ics",
                )
                self.git(
                    candidate,
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "--amend", "-qm",
                    "Candidate deleting registered calendar",
                )

                deleted_sha = self.git(
                    candidate, "rev-parse", "HEAD"
                )

                with self.assertRaisesRegex(
                    RuntimeError, "non-regular candidate output"
                ):
                    transfer_candidate_commit(
                        publisher, candidate, base, deleted_sha
                    )

                self.assertEqual(
                    self.git(publisher, "rev-parse", "HEAD"),
                    base,
                )
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", deleted_sha],
                        cwd=publisher,
                        capture_output=True,
                        check=False,
                    ).returncode,
                    0,
                )

                # Restore the original candidate before the
                # existing symlink and successful-transfer checks.
                self.git(
                    candidate, "reset", "--hard", candidate_sha
                )

                # A manually constructed candidate could contain
                # a symlink even though the normal creator forbids it.
                output = candidate / "docs/index.html"
                output.unlink()
                output.symlink_to("missing-target.html")

                self.git(candidate, "add", "--", "docs/index.html")
                self.git(
                    candidate,
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "--amend", "-qm",
                    "Candidate with symlinked output",
                )

                symlink_sha = self.git(
                    candidate, "rev-parse", "HEAD"
                )
                self.assertNotEqual(symlink_sha, candidate_sha)
                self.assertEqual(
                    self.git(candidate, "rev-parse", "HEAD^"),
                    base,
                )

                with self.assertRaisesRegex(
                    RuntimeError, "non-regular candidate output"
                ):
                    transfer_candidate_commit(
                        publisher, candidate, base, symlink_sha
                    )

                # Rejection must occur before import.
                self.assertEqual(
                    self.git(publisher, "rev-parse", "HEAD"),
                    base,
                )
                self.assertNotEqual(
                    subprocess.run(
                        ["git", "cat-file", "-e", symlink_sha],
                        cwd=publisher,
                        capture_output=True,
                        check=False,
                    ).returncode,
                    0,
                )

                # Restore the valid candidate for the existing
                # successful-transfer assertions.
                self.git(
                    candidate, "reset", "--hard", candidate_sha
                )

                # Transfer must not execute local Git hooks.
                hooks = parent / "test-hooks"
                hooks.mkdir()
                hook_marker = parent / "post-merge-executed"
                hook = hooks / "post-merge"
                hook.write_text(
                    f"#!{sys.executable}\n"
                    "from pathlib import Path\n"
                    f"Path({str(hook_marker)!r}).write_text('executed')\n",
                    encoding="utf-8",
                )
                hook.chmod(0o755)

                self.git(
                    publisher, "config", "--local",
                    "core.hooksPath", str(hooks),
                )

                result = transfer_candidate_commit(
                    publisher, candidate, base, candidate_sha
                )

            self.assertFalse(
                hook_marker.exists(),
                "Transfer unexpectedly executed a Git post-merge hook",
            )
            self.assertEqual(result, candidate_sha)
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"),
                candidate_sha,
            )
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD^"),
                base,
            )
            self.assertEqual(
                self.git(
                    publisher, "symbolic-ref", "--short", "HEAD"
                ),
                "main",
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                base,
            )
            self.assertEqual(
                self.git(publisher, "status", "--porcelain"),
                "",
            )
            self.assertEqual(self.git(candidate, "remote"), "")


    def test_unregistered_candidate_is_rejected_before_import(self):
        with tempfile.TemporaryDirectory(
            prefix="soccer-transfer-guard-test-"
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

            self.write(publisher, "docs/index.html", "original\n")
            self.write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Transfer Guard Test",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            self.populate_required_outputs(publisher)
            base = self.commit(publisher, "Input revision")
            self.git(
                publisher, "remote", "add", "origin", str(remote)
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--local",
                "--no-hardlinks", str(publisher), str(candidate),
            )
            self.git(
                candidate, "checkout", "-q", "--detach", base
            )
            self.git(candidate, "remote", "remove", "origin")

            self.write(
                candidate,
                "docs/unregistered-team.ics",
                "unauthorized calendar\n",
            )
            candidate_sha = self.commit(
                candidate, "Unauthorized publication candidate"
            )

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                from soccer_publication_transfer import (
                    transfer_candidate_commit,
                )

                with self.assertRaisesRegex(
                    RuntimeError, "unauthorized candidate paths"
                ):
                    transfer_candidate_commit(
                        publisher, candidate, base, candidate_sha
                    )

            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"), base
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                base,
            )

            imported = subprocess.run(
                ["git", "cat-file", "-e", candidate_sha],
                cwd=publisher,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(imported.returncode, 0)


    def test_remote_advance_rejected_before_import(self):
        with tempfile.TemporaryDirectory(
            prefix="soccer-transfer-race-test-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            candidate = parent / "candidate"
            peer = parent / "competitor"
            publisher.mkdir()

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            self.write(publisher, "docs/index.html", "original\n")
            self.write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Remote Advance Test",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            self.populate_required_outputs(publisher)
            base = self.commit(publisher, "Input revision")
            self.git(
                publisher, "remote", "add", "origin", str(remote)
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--local",
                "--no-hardlinks", str(publisher), str(candidate),
            )
            self.git(candidate, "checkout", "-q", "--detach", base)
            self.git(candidate, "remote", "remove", "origin")

            self.write(
                candidate, "docs/index.html", "candidate update\n"
            )
            candidate_sha = self.commit(
                candidate, "Candidate publication"
            )

            # Another publisher advances remote main independently.
            self.git(
                parent, "clone", "-q", "--branch", "main",
                str(remote), str(peer),
            )
            self.write(
                peer, "docs/index.html", "competing update\n"
            )
            competing_sha = self.commit(
                peer, "Competing publication"
            )
            self.git(peer, "push", "-q", "origin", "main")

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                from soccer_publication_transfer import (
                    transfer_candidate_commit,
                )

                with self.assertRaisesRegex(
                    RuntimeError, "remote main has advanced"
                ):
                    transfer_candidate_commit(
                        publisher, candidate, base, candidate_sha
                    )

            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"), base
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                competing_sha,
            )

            imported = subprocess.run(
                ["git", "cat-file", "-e", candidate_sha],
                cwd=publisher,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(imported.returncode, 0)


    def test_remote_advances_after_import_before_local_merge(self):
        with tempfile.TemporaryDirectory(
            prefix="soccer-transfer-mid-race-"
        ) as tmp:
            parent = Path(tmp)
            remote = parent / "remote.git"
            publisher = parent / "publisher"
            candidate = parent / "candidate"
            peer = parent / "competitor"
            publisher.mkdir()

            self.git(
                parent, "init", "--bare", "-q",
                "--initial-branch=main", str(remote),
            )
            self.git(publisher, "init", "-q", "-b", "main")

            self.write(publisher, "docs/index.html", "original\n")
            self.write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Mid-Transfer Race Test",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            self.populate_required_outputs(publisher)
            base = self.commit(publisher, "Input revision")
            self.git(
                publisher, "remote", "add", "origin", str(remote)
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--local",
                "--no-hardlinks", str(publisher), str(candidate),
            )
            self.git(candidate, "checkout", "-q", "--detach", base)
            self.git(candidate, "remote", "remove", "origin")

            self.write(
                candidate, "docs/index.html", "candidate update\n"
            )
            candidate_sha = self.commit(
                candidate, "Candidate publication"
            )

            self.git(
                parent, "clone", "-q", "--branch", "main",
                str(remote), str(peer),
            )
            self.write(
                peer, "docs/index.html", "competing update\n"
            )
            competing_sha = self.commit(
                peer, "Competing publication"
            )

            import soccer_publication_transfer as transfer

            original_changed_paths = transfer.changed_paths
            advanced = False

            def advance_after_import(root, expected_base, head):
                nonlocal advanced

                result = original_changed_paths(
                    root, expected_base, head
                )

                # The publisher-side comparison occurs after fetch.
                if Path(root).resolve() == publisher and not advanced:
                    advanced = True
                    self.git(peer, "push", "-q", "origin", "main")

                return result

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                with patch.object(
                    transfer,
                    "changed_paths",
                    side_effect=advance_after_import,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "remote main has advanced"
                    ):
                        transfer.transfer_candidate_commit(
                            publisher, candidate, base, candidate_sha
                        )

            self.assertTrue(advanced)
            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"), base
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                competing_sha,
            )

            # Import occurred, but local main must not advance.
            self.assertEqual(
                self.git(publisher, "cat-file", "-t", candidate_sha),
                "commit",
            )


    def test_unchanged_missing_registered_output_is_rejected(self):
        from plan_soccer_builds import plan
        from soccer_publication_paths import expected_publication_paths

        with tempfile.TemporaryDirectory(
            prefix="soccer-transfer-missing-output-"
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

            self.write(publisher, "docs/index.html", "original\n")
            self.write(
                publisher,
                "config/2026-27/cjsl/league/events/55206/manifest.json",
                json.dumps({
                    "competition": {
                        "event_id": "55206",
                        "organizer": "cjsl",
                        "provider": "gotsport",
                        "name": "Missing Output Test",
                        "calendar_profile": "explicit-status-cjsl-v1",
                        "publication_path": "test-calendar",
                    },
                    "teams": [{"slug": "test-team"}],
                }) + "\n",
            )

            self.populate_required_outputs(publisher)

            events, errors, _warnings = plan(publisher)
            self.assertFalse(errors)

            revisions = [
                relative
                for relative in expected_publication_paths(
                    publisher, events
                )
                if relative.endswith("/revisions.json")
            ]
            self.assertEqual(len(revisions), 1)

            # The required file is absent from the base itself.
            (publisher / revisions[0]).unlink()
            base = self.commit(publisher, "Incomplete input revision")

            self.git(
                publisher, "remote", "add", "origin", str(remote)
            )
            self.git(
                publisher, "push", "-q", "-u", "origin", "main"
            )

            self.git(
                parent, "clone", "-q", "--local",
                "--no-hardlinks", str(publisher), str(candidate),
            )
            self.git(candidate, "checkout", "-q", "--detach", base)
            self.git(candidate, "remote", "remove", "origin")

            # The candidate changes an allowed file, but does
            # not restore the missing revisions.json.
            self.write(
                candidate, "docs/index.html", "updated\n"
            )
            candidate_sha = self.commit(
                candidate, "Incomplete publication candidate"
            )

            self.assertEqual(
                self.git(
                    candidate, "diff", "--name-only",
                    base, candidate_sha,
                ),
                "docs/index.html",
            )

            with patch.object(
                soccer_publication_push,
                "TRUSTED_PRODUCTION_REPOSITORY_URL",
                str(remote),
            ):
                from soccer_publication_transfer import (
                    transfer_candidate_commit,
                )

                with self.assertRaisesRegex(
                    RuntimeError,
                    "missing or non-regular registered output",
                ):
                    transfer_candidate_commit(
                        publisher, candidate, base, candidate_sha
                    )

            self.assertEqual(
                self.git(publisher, "rev-parse", "HEAD"), base
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "refs/heads/main"),
                base,
            )
            self.assertNotEqual(
                subprocess.run(
                    ["git", "cat-file", "-e", candidate_sha],
                    cwd=publisher,
                    capture_output=True,
                    check=False,
                ).returncode,
                0,
            )


if __name__ == "__main__":
    unittest.main()
