
"""Regression tests for EDP missing-match history safety."""

import sys
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


if __name__ == "__main__":
    unittest.main()
