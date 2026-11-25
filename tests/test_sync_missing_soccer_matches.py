
"""Regression tests for EDP missing-match history safety."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sync_missing_soccer_matches as sync


class MissingMatchBaselineTests(unittest.TestCase):

    def test_valid_explicit_base_is_used(self):
        with (
            patch.object(sync, "commit_exists", return_value=True),
            patch.object(sync, "run_git") as git,
        ):
            result = sync.resolve_base("valid-base", "HEAD")
            self.assertEqual(result, "valid-base")
            git.assert_not_called()

    def test_invalid_explicit_base_is_rejected(self):
        with (
            patch.object(sync, "commit_exists", return_value=False),
            patch.object(sync, "run_git") as git,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Explicit historical Git baseline is invalid"
            ):
                sync.resolve_base("invalid-base", "HEAD")

            git.assert_not_called()

    def test_omitted_base_uses_parent(self):
        with (
            patch.object(sync, "run_git", return_value="parent-commit"),
            patch.object(sync, "commit_exists", return_value=True),
        ):
            result = sync.resolve_base("", "HEAD")
            self.assertEqual(result, "parent-commit")

    def test_omitted_base_without_parent_returns_empty(self):
        with (
            patch.object(sync, "run_git", return_value=""),
            patch.object(sync, "commit_exists", return_value=False),
        ):
            result = sync.resolve_base("", "HEAD")
            self.assertEqual(result, "")


class MissingMatchHistoryRangeTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.manifest = Path(self.tmp.name) / "manifest.json"
        self.manifest.write_text(
            json.dumps({
                "competition": {
                    "missing_match_policy": "preserve-warning"
                },
                "teams": [],
            }),
            encoding="utf-8",
        )

        self.args = [
            "sync_missing_soccer_matches.py",
            "--manifest", str(self.manifest),
            "--base", "base-sha",
            "--head", "head-sha",
        ]

    def test_head_must_match_checked_out_commit(self):
        with (
            patch.object(sys, "argv", self.args),
            patch.object(
                sync, "run_git",
                side_effect=["head-sha", "different-sha"],
            ),
            patch.object(
                sync, "resolve_base", return_value="base-sha"
            ),
            patch.object(sync.subprocess, "run") as git_run,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "--head must match"
            ):
                sync.main()

            git_run.assert_not_called()

    def test_base_must_be_ancestor_of_head(self):
        with (
            patch.object(sys, "argv", self.args),
            patch.object(
                sync, "run_git",
                side_effect=["head-sha", "head-sha"],
            ),
            patch.object(
                sync, "resolve_base", return_value="base-sha"
            ),
            patch.object(sync.subprocess, "run") as git_run,
        ):
            git_run.return_value.returncode = 1

            with self.assertRaisesRegex(
                RuntimeError, "--base must be an ancestor"
            ):
                sync.main()

            git_run.assert_called_once()
            self.assertEqual(
                git_run.call_args.args[0],
                [
                    "git", "merge-base", "--is-ancestor",
                    "base-sha", "head-sha",
                ],
            )


if __name__ == "__main__":
    unittest.main()
