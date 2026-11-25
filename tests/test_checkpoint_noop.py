"""Regression tests for avoiding unnecessary checkpoint updates."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prepare_soccer_checkpoints import prepare_checkpoints


class CheckpointNoopTests(unittest.TestCase):

    def test_unchanged_inputs_preserve_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            manifest = Path(
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )
            source = root / manifest
            source.parent.mkdir(parents=True)
            source.write_text('{"competition": {}}\n', encoding="utf-8")

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            git("init", "-q")
            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "initial source inputs"
            )
            original_input = git("rev-parse", "HEAD")

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(
                json.dumps({
                    "version": 1,
                    "last_published_input_commit": original_input,
                }, indent=2) + "\n",
                encoding="utf-8",
            )

            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "record processing checkpoint"
            )

            current_head = git("rev-parse", "HEAD")

            prepare_checkpoints(
                root,
                [{"manifest": str(manifest), "missing_match_sync": True}],
                current_head,
            )

            saved = json.loads(checkpoint.read_text(encoding="utf-8"))

            self.assertEqual(
                saved["last_published_input_commit"],
                original_input,
            )
            self.assertEqual(git("status", "--porcelain"), "")


    def test_changed_workbook_advances_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            manifest = Path(
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )
            source_file = root / manifest
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                '{"competition": {}}\n', encoding="utf-8"
            )

            workbook = root / (
                "data/2026-27/edp/north-atlantic/"
                "events/54578/team.xlsx"
            )
            workbook.parent.mkdir(parents=True)
            workbook.write_bytes(b"original export")

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            git("init", "-q")
            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "initial source"
            )
            original = git("rev-parse", "HEAD")

            checkpoint = root / (
                "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(
                json.dumps({
                    "version": 1,
                    "last_published_input_commit": original,
                }, indent=2) + "\n",
                encoding="utf-8",
            )

            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "record checkpoint"
            )

            workbook.write_bytes(b"updated export")
            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "update workbook"
            )
            updated = git("rev-parse", "HEAD")

            prepare_checkpoints(
                root,
                [{"manifest": str(manifest), "missing_match_sync": True}],
                updated,
            )

            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["last_published_input_commit"], updated
            )


    def test_unrelated_checkpoint_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = Path(
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )
            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}\n', encoding="utf-8"
            )

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            git("init", "-q")
            original_branch = git("branch", "--show-current")
            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "original source"
            )

            git("checkout", "-q", "--orphan", "unrelated-history")
            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "unrelated source"
            )
            unrelated_sha = git("rev-parse", "HEAD")
            git("checkout", "-q", original_branch)

            checkpoint = root / (
                "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(
                json.dumps({
                    "version": 1,
                    "last_published_input_commit": unrelated_sha,
                }, indent=2) + "\n",
                encoding="utf-8",
            )

            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "record unrelated checkpoint"
            )

            with self.assertRaises((ValueError, RuntimeError)):
                prepare_checkpoints(
                    root,
                    [{
                        "manifest": str(manifest),
                        "missing_match_sync": True,
                    }],
                    git("rev-parse", "HEAD"),
                )


    def test_corrupted_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            manifest = Path(
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )
            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"competition": {}}),
                encoding="utf-8",
            )

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            git("init", "-q")
            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "initial source"
            )

            checkpoint = root / (
                "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(
                "INVALID CHECKPOINT",
                encoding="utf-8",
            )

            git("add", "-A")
            git(
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "corrupted checkpoint"
            )

            original = checkpoint.read_bytes()

            with self.assertRaisesRegex(
                RuntimeError, "Invalid existing checkpoint"
            ):
                prepare_checkpoints(
                    root,
                    [{"manifest": str(manifest),
                      "missing_match_sync": True}],
                    git("rev-parse", "HEAD"),
                )

            self.assertEqual(checkpoint.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
