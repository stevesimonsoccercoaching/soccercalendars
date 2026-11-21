"""Tests for safe publication checkpoint preparation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prepare_soccer_checkpoints import prepare_checkpoints


class CheckpointPreparationTests(unittest.TestCase):

    def test_candidate_records_processed_input_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"competition": {}}),
                encoding="utf-8",
            )

            input_sha = self.make_input_commit(root)
            events = [{
                "manifest": manifest,
                "missing_match_sync": True,
            }]

            prepare_checkpoints(root, events, input_sha)

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )

            self.assertEqual(
                json.loads(checkpoint.read_text(encoding="utf-8")),
                {
                    "version": 1,
                    "last_published_input_commit": input_sha,
                },
            )


    def test_invalid_second_event_does_not_update_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            valid = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / valid
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            events = [
                {"manifest": valid, "missing_match_sync": True},
                {
                    "manifest": "config/missing/manifest.json",
                    "missing_match_sync": True,
                },
            ]

            with self.assertRaises(FileNotFoundError):
                prepare_checkpoints(root, events, self.make_input_commit(root))

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )

            self.assertFalse(checkpoint.exists())


    def test_blocked_second_destination_preserves_first_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []

            for event_id in ("54578", "54579"):
                manifest = (
                    "config/2026-27/edp/north-atlantic/"
                    f"events/{event_id}/manifest.json"
                )

                path = root / manifest
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    '{"competition": {}}',
                    encoding="utf-8",
                )

                events.append({
                    "manifest": manifest,
                    "missing_match_sync": True,
                })

            state = root / "state/2026-27/edp/north-atlantic/events"

            first = state / "54578/processing_checkpoint.json"
            first.parent.mkdir(parents=True, exist_ok=True)

            original = json.dumps({
                "version": 1,
                "last_published_input_commit": "b" * 40,
            }) + "\n"

            first.write_text(original, encoding="utf-8")

            blocked = state / "54579/processing_checkpoint.json"
            blocked.mkdir(parents=True)

            with self.assertRaises(IsADirectoryError):
                prepare_checkpoints(root, events, self.make_input_commit(root))

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                original,
            )


    def test_nonexistent_git_commit_is_rejected(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            subprocess.run(
                ["git", "init", "-q"],
                cwd=root, check=True,
            )
            subprocess.run(
                [
                    "git", "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-qm", "initial",
                ],
                cwd=root, check=True,
            )

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )
            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                prepare_checkpoints(
                    root,
                    [{
                        "manifest": manifest,
                        "missing_match_sync": True,
                    }],
                    "f" * 40,
                )

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )
            self.assertFalse(checkpoint.exists())

    def make_input_commit(self, root):
        import subprocess

        subprocess.run(
            ["git", "init", "-q"],
            cwd=root, check=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=root, check=True,
        )
        subprocess.run(
            [
                "git",
                "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "--allow-empty",
                "-qm", "test input",
            ],
            cwd=root, check=True,
        )
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root, text=True,
        ).strip()

    def test_input_commit_must_match_checked_out_head(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            old_sha = self.make_input_commit(root)

            (root / "marker.txt").write_text(
                "newer inputs\n", encoding="utf-8"
            )

            subprocess.run(
                ["git", "add", "marker.txt"],
                cwd=root, check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", "newer inputs",
                ],
                cwd=root, check=True,
            )

            current_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root, text=True,
            ).strip()

            self.assertNotEqual(old_sha, current_sha)

            with self.assertRaises(ValueError):
                prepare_checkpoints(
                    root,
                    [{
                        "manifest": manifest,
                        "missing_match_sync": True,
                    }],
                    old_sha,
                )

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )

            self.assertFalse(checkpoint.exists())

    def test_modified_workbook_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            workbook = (
                root / "data/2026-27/edp/north-atlantic/"
                "events/54578/edp-team.xlsx"
            )
            workbook.parent.mkdir(parents=True)
            workbook.write_bytes(b"original export")

            input_sha = self.make_input_commit(root)

            # Modify the workbook without committing it.
            workbook.write_bytes(b"modified export")

            with self.assertRaises(ValueError):
                prepare_checkpoints(
                    root,
                    [{
                        "manifest": manifest,
                        "missing_match_sync": True,
                    }],
                    input_sha,
                )

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )

            self.assertFalse(checkpoint.exists())

    def test_generated_docs_and_state_changes_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            input_sha = self.make_input_commit(root)

            # Simulate generated calendar output.
            calendar = root / "docs/2026-27/edp-north-atlantic/team.ics"
            calendar.parent.mkdir(parents=True)
            calendar.write_text(
                "BEGIN:VCALENDAR\nEND:VCALENDAR\n",
                encoding="utf-8",
            )

            # Simulate generated revision state.
            revisions = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/revisions.json"
            )
            revisions.parent.mkdir(parents=True)
            revisions.write_text("{}\n", encoding="utf-8")

            prepare_checkpoints(
                root,
                [{
                    "manifest": manifest,
                    "missing_match_sync": True,
                }],
                input_sha,
            )

            checkpoint = (
                revisions.parent / "processing_checkpoint.json"
            )

            self.assertEqual(
                json.loads(
                    checkpoint.read_text(encoding="utf-8")
                )["last_published_input_commit"],
                input_sha,
            )

    def test_second_write_failure_preserves_existing_checkpoint(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []

            for event_id in ("54578", "54579"):
                manifest = (
                    "config/2026-27/edp/north-atlantic/"
                    f"events/{event_id}/manifest.json"
                )
                path = root / manifest
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    '{"competition": {}}',
                    encoding="utf-8",
                )
                events.append({
                    "manifest": manifest,
                    "missing_match_sync": True,
                })

            input_sha = self.make_input_commit(root)

            state = root / "state/2026-27/edp/north-atlantic/events"
            first = state / "54578/processing_checkpoint.json"
            second = state / "54579/processing_checkpoint.json"

            first.parent.mkdir(parents=True, exist_ok=True)
            original = '{"version": 1, "last_published_input_commit": "old"}\n'
            first.write_text(original, encoding="utf-8")

            import os
            real_replace = os.replace

            def simulated_replace(src, dst):
                if Path(dst) == second:
                    raise OSError("simulated second checkpoint replacement failure")
                return real_replace(src, dst)

            with patch("os.replace", side_effect=simulated_replace):
                with self.assertRaisesRegex(
                    OSError, "simulated second checkpoint replacement failure"
                ):
                    prepare_checkpoints(root, events, input_sha)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                original,
            )

    def test_failed_rollback_preserves_recovery_backup(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []

            for event_id in ("54578", "54579"):
                manifest = (
                    "config/2026-27/edp/north-atlantic/"
                    f"events/{event_id}/manifest.json"
                )
                path = root / manifest
                path.parent.mkdir(parents=True)
                path.write_text(
                    '{"competition": {}}',
                    encoding="utf-8",
                )
                events.append({
                    "manifest": manifest,
                    "missing_match_sync": True,
                })

            input_sha = self.make_input_commit(root)

            state = root / "state/2026-27/edp/north-atlantic/events"
            first = state / "54578/processing_checkpoint.json"
            second = state / "54579/processing_checkpoint.json"

            first.parent.mkdir(parents=True, exist_ok=True)
            original = b'{"version": 1, "last_published_input_commit": "old"}\n'
            first.write_bytes(original)

            real_replace = os.replace
            first_replacements = 0

            def simulated_replace(src, dst):
                nonlocal first_replacements

                if Path(dst) == second:
                    raise OSError("second replacement failed")

                if Path(dst) == first:
                    first_replacements += 1
                    if first_replacements == 2:
                        raise OSError("rollback failed")

                return real_replace(src, dst)

            with patch("os.replace", side_effect=simulated_replace):
                with self.assertRaisesRegex(
                    RuntimeError, "rollback incomplete"
                ) as caught:
                    prepare_checkpoints(root, events, input_sha)

            backups = list(first.parent.glob(".checkpoint-*.tmp"))

            self.assertTrue(
                any(p.read_bytes() == original for p in backups),
                "Original checkpoint backup must survive failed rollback",
            )

            message = str(caught.exception)
            self.assertIn(str(first), message)
            self.assertTrue(
                any(
                    str(p) in message
                    for p in backups
                    if p.read_bytes() == original
                ),
                "Error must identify the preserved original backup",
            )

    def test_cleanup_error_does_not_mask_replacement_error(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            input_sha = self.make_input_commit(root)

            events = [{
                "manifest": manifest,
                "missing_match_sync": True,
            }]

            real_unlink = Path.unlink

            def simulated_unlink(path, *args, **kwargs):
                if (
                    path.name.startswith(".checkpoint-")
                    and path.suffix == ".tmp"
                ):
                    raise OSError("simulated cleanup failure")
                return real_unlink(path, *args, **kwargs)

            with patch(
                "os.replace",
                side_effect=OSError("original replacement failure"),
            ):
                with patch.object(
                    Path, "unlink", autospec=True,
                    side_effect=simulated_unlink,
                ):
                    with self.assertRaisesRegex(
                        OSError, "original replacement failure"
                    ):
                        prepare_checkpoints(root, events, input_sha)

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )

            self.assertFalse(checkpoint.exists())

    def test_cleanup_failure_after_success_is_reported(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )

            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            input_sha = self.make_input_commit(root)

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )

            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(
                '{"version": 1, "last_published_input_commit": "old"}\n',
                encoding="utf-8",
            )

            real_unlink = Path.unlink

            def simulated_unlink(path, *args, **kwargs):
                if (
                    path.name.startswith(".checkpoint-")
                    and path.suffix == ".tmp"
                    and path.exists()
                ):
                    raise OSError("simulated backup cleanup failure")

                return real_unlink(path, *args, **kwargs)

            with patch.object(
                Path, "unlink", autospec=True,
                side_effect=simulated_unlink,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "cleanup incomplete"
                ):
                    prepare_checkpoints(
                        root,
                        [{
                            "manifest": manifest,
                            "missing_match_sync": True,
                        }],
                        input_sha,
                    )

            self.assertEqual(
                json.loads(checkpoint.read_text(encoding="utf-8"))[
                    "last_published_input_commit"
                ],
                input_sha,
            )

            self.assertTrue(
                list(checkpoint.parent.glob(".checkpoint-*.tmp"))
            )

    def test_staging_cleanup_does_not_mask_original_error(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/north-atlantic/"
                "events/54578/manifest.json"
            )
            path = root / manifest
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"competition": {}}',
                encoding="utf-8",
            )

            input_sha = self.make_input_commit(root)

            checkpoint = (
                root / "state/2026-27/edp/north-atlantic/"
                "events/54578/processing_checkpoint.json"
            )
            checkpoint.parent.mkdir(parents=True)

            original = b'{"version": 1, "last_published_input_commit": "old"}\n'
            checkpoint.write_bytes(original)

            real_unlink = Path.unlink

            def simulated_unlink(path, *args, **kwargs):
                if path.name.startswith(".checkpoint-"):
                    raise OSError("staging cleanup failed")
                return real_unlink(path, *args, **kwargs)

            with patch(
                "os.fsync",
                side_effect=OSError("original staging failure"),
            ):
                with patch.object(
                    Path, "unlink",
                    autospec=True,
                    side_effect=simulated_unlink,
                ):
                    with self.assertRaisesRegex(
                        OSError, "original staging failure"
                    ):
                        prepare_checkpoints(root, [{
                            "manifest": manifest,
                            "missing_match_sync": True,
                        }], input_sha)

            self.assertEqual(checkpoint.read_bytes(), original)
