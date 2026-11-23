"""Tests for isolated soccer publication candidate commits."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from soccer_publication_candidate import create_candidate_commit


FEED = "docs/test-calendar/test-team.ics"
MANIFEST = "config/2026-27/cjsl/league/events/55206/manifest.json"


class CandidateCommitTests(unittest.TestCase):

    def git(self, root, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def write(self, root, relative, content):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="soccer-candidate-test-"
        )
        self.addCleanup(self.temp.cleanup)

        parent = Path(self.temp.name)
        self.source = parent / "source"
        self.checkout = parent / "isolated"

        self.git(parent, "init", "-q", "-b", "main", str(self.source))

        manifest = {
            "competition": {
                "event_id": "55206",
                "organizer": "cjsl",
                "provider": "gotsport",
                "name": "Candidate Test Event",
                "calendar_profile": "explicit-status-cjsl-v1",
                "publication_path": "test-calendar",
            },
            "teams": [{"slug": "test-team"}],
        }

        self.write(
            self.source,
            MANIFEST,
            json.dumps(manifest, indent=2) + "\n",
        )
        self.write(self.source, FEED, "original calendar\n")
        self.write(self.source, "docs/index.html", "index\n")
        self.write(
            self.source,
            "state/2026-27/cjsl/league/events/55206/revisions.json",
            "{}\n",
        )

        self.git(self.source, "add", "-A")
        self.git(
            self.source,
            "-c", "user.name=Test",
            "-c", "user.email=test@example.com",
            "commit", "-qm", "Initial input",
        )

        self.base = self.git(self.source, "rev-parse", "HEAD")

        self.git(
            parent, "clone", "-q",
            str(self.source), str(self.checkout),
        )
        self.git(
            self.checkout, "checkout", "-q", "--detach", self.base
        )
        self.git(self.checkout, "remote", "remove", "origin")
        self.git(self.checkout, "config", "user.name", "Test")
        self.git(
            self.checkout, "config", "user.email", "test@example.com"
        )

    def stage_calendar(self):
        self.write(self.checkout, FEED, "updated calendar\n")
        self.git(self.checkout, "add", "--", FEED)

    def test_candidate_commit_contains_only_authorized_calendar(self):
        self.stage_calendar()

        candidate = create_candidate_commit(
            self.checkout, self.base
        )

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            candidate,
        )
        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD^"),
            self.base,
        )
        self.assertEqual(
            self.git(
                self.checkout,
                "diff", "--name-only", self.base, candidate,
            ),
            FEED,
        )
        self.assertEqual(
            self.git(self.checkout, "status", "--porcelain"),
            "",
        )
        self.assertEqual(
            self.git(self.source, "rev-parse", "HEAD"),
            self.base,
        )
        self.assertEqual(self.git(self.checkout, "remote"), "")

    def test_unauthorized_staged_file_is_rejected(self):
        self.stage_calendar()
        self.write(
            self.checkout,
            "docs/unregistered-team.ics",
            "unauthorized calendar\n",
        )
        self.git(
            self.checkout,
            "add", "--", "docs/unregistered-team.ics",
        )

        with self.assertRaisesRegex(RuntimeError, "unauthorized"):
            create_candidate_commit(self.checkout, self.base)

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            self.base,
        )

    def test_checkout_with_remote_is_rejected(self):
        self.stage_calendar()
        self.git(
            self.checkout, "remote", "add",
            "origin", str(self.source),
        )

        with self.assertRaisesRegex(RuntimeError, "remote"):
            create_candidate_commit(self.checkout, self.base)

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            self.base,
        )

    def test_empty_staged_publication_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "empty"):
            create_candidate_commit(self.checkout, self.base)

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            self.base,
        )


    def test_wrong_input_commit_is_rejected(self):
        self.stage_calendar()

        with self.assertRaisesRegex(
            RuntimeError, "checked-out commit"
        ):
            create_candidate_commit(
                self.checkout, "0" * 40
            )

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            self.base,
        )

    def test_unstaged_changes_are_rejected(self):
        self.stage_calendar()
        self.write(
            self.checkout,
            "docs/index.html",
            "modified but not staged\n",
        )

        with self.assertRaisesRegex(
            RuntimeError, "unstaged"
        ):
            create_candidate_commit(
                self.checkout, self.base
            )

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            self.base,
        )

    def test_missing_registered_output_is_rejected(self):
        self.stage_calendar()
        self.git(
            self.checkout,
            "rm", "--", "docs/index.html",
        )

        with self.assertRaisesRegex(
            RuntimeError, "missing publication output"
        ):
            create_candidate_commit(
                self.checkout, self.base
            )

        self.assertEqual(
            self.git(self.checkout, "rev-parse", "HEAD"),
            self.base,
        )


if __name__ == "__main__":
    unittest.main()
