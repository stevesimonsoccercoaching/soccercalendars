"""Tests for isolated Git staging of soccer publication files."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "scripts")
)

from soccer_publication_paths import stage_publication_outputs


class PublicationStagingTests(unittest.TestCase):

    def git(self, root, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=root,
            text=True,
        ).strip()

    def write(self, root, name, content):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_only_allowed_outputs_are_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/edp-north-atlantic/"
                "events/54578/manifest.json"
            )

            self.write(root, manifest, json.dumps({
                "competition": {
                    "publication_path": "2026-27/edp-north-atlantic"
                },
                "teams": [{"slug": "edp-team"}],
            }))

            docs = "docs/2026-27/edp-north-atlantic"
            state = (
                "state/2026-27/edp/edp-north-atlantic/"
                "events/54578"
            )

            allowed = {
                "docs/index.html",
                f"{docs}/edp-team.ics",
                f"{state}/revisions.json",
                f"{state}/missing_matches.json",
                f"{state}/processing_checkpoint.json",
            }

            unrelated = {
                "docs/2026/cjsl/fall/README.MD",
                "state/weave/sffk-2015-2016.json",
            }

            for name in sorted(allowed | unrelated):
                self.write(root, name, "original\n")

            subprocess.run(
                ["git", "init", "-q"],
                cwd=root, check=True,
            )
            self.git(root, "add", ".")
            subprocess.run(
                [
                    "git",
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", "baseline",
                ],
                cwd=root, check=True,
            )

            for name in sorted(allowed | unrelated):
                self.write(root, name, "modified\n")

            self.write(
                root,
                f"{docs}/unregistered-team.ics",
                "unexpected\n",
            )
            self.write(
                root,
                f"{state}/local-notes.tmp",
                "temporary\n",
            )

            events = [{
                "manifest": manifest,
                "missing_match_sync": True,
            }]

            stage_publication_outputs(root, events)

            staged = set(
                self.git(
                    root, "diff", "--cached",
                    "--name-only",
                ).splitlines()
            )

            self.assertEqual(staged, allowed)

            self.assertFalse(staged & unrelated)
            self.assertNotIn(
                f"{docs}/unregistered-team.ics", staged
            )
            self.assertNotIn(
                f"{state}/local-notes.tmp", staged
            )


    def test_refuses_preexisting_staged_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/edp-north-atlantic/"
                "events/54578/manifest.json"
            )

            self.write(root, manifest, json.dumps({
                "competition": {},
                "teams": [{"slug": "edp-team"}],
            }))

            unrelated = "state/weave/sffk-2015-2016.json"
            self.write(root, unrelated, "unrelated state\n")

            self.git(root, "init", "-q")
            self.git(root, "add", unrelated)

            events = [{
                "manifest": manifest,
                "missing_match_sync": True,
            }]

            with self.assertRaisesRegex(
                ValueError, "already contains staged changes"
            ):
                stage_publication_outputs(root, events)

            staged = self.git(
                root, "diff", "--cached", "--name-only"
            ).splitlines()

            self.assertEqual(staged, [unrelated])


    def test_refuses_leftover_checkpoint_recovery_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/edp-north-atlantic/"
                "events/54578/manifest.json"
            )
            self.write(root, manifest, json.dumps({
                "competition": {
                    "publication_path": "2026-27/edp-north-atlantic"
                },
                "teams": [{"slug": "edp-team"}],
            }))

            state = (
                "state/2026-27/edp/edp-north-atlantic/"
                "events/54578"
            )

            for name in (
                "docs/index.html",
                "docs/2026-27/edp-north-atlantic/edp-team.ics",
                f"{state}/revisions.json",
                f"{state}/missing_matches.json",
                f"{state}/processing_checkpoint.json",
            ):
                self.write(root, name, "valid\\n")

            recovery = f"{state}/.checkpoint-stranded.tmp"
            self.write(root, recovery, "recovery backup\\n")

            self.git(root, "init", "-q")

            with self.assertRaisesRegex(ValueError, "recovery"):
                stage_publication_outputs(root, [{
                    "manifest": manifest,
                    "missing_match_sync": True,
                }])

            self.assertEqual(
                self.git(root, "diff", "--cached", "--name-only"),
                "",
            )


    def test_missing_publication_output_stops_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            manifest = (
                "config/2026-27/edp/edp-north-atlantic/"
                "events/54578/manifest.json"
            )

            self.write(root, manifest, json.dumps({
                "competition": {
                    "publication_path": "2026-27/edp-north-atlantic"
                },
                "teams": [{"slug": "edp-team"}],
            }))

            state = (
                "state/2026-27/edp/edp-north-atlantic/"
                "events/54578"
            )

            for name in (
                "docs/index.html",
                f"{state}/revisions.json",
                f"{state}/missing_matches.json",
                f"{state}/processing_checkpoint.json",
            ):
                self.write(root, name, "existing content")

            # Deliberately omit the registered edp-team.ics file.
            self.git(root, "init", "-q")

            with self.assertRaisesRegex(
                FileNotFoundError,
                "Required publication output missing",
            ):
                stage_publication_outputs(root, [{
                    "manifest": manifest,
                    "missing_match_sync": True,
                }])

            self.assertEqual(
                self.git(root, "diff", "--cached", "--name-only"),
                "",
            )


    def test_refuses_symlinked_publication_paths(self):
        for kind in ("calendar_file", "publication_directory"):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)

                    manifest = (
                        "config/2026-27/edp/edp-north-atlantic/"
                        "events/54578/manifest.json"
                    )
                    self.write(root, manifest, json.dumps({
                        "competition": {
                            "publication_path":
                                "2026-27/edp-north-atlantic"
                        },
                        "teams": [{"slug": "edp-team"}],
                    }))

                    state = (
                        "state/2026-27/edp/"
                        "edp-north-atlantic/events/54578"
                    )

                    for name in (
                        "docs/index.html",
                        f"{state}/revisions.json",
                        f"{state}/missing_matches.json",
                        f"{state}/processing_checkpoint.json",
                    ):
                        self.write(root, name, "valid")

                    calendar = (
                        root / "docs/2026-27/"
                        "edp-north-atlantic/edp-team.ics"
                    )

                    if kind == "calendar_file":
                        self.write(root, "outside.txt", "outside")
                        calendar.parent.mkdir(
                            parents=True, exist_ok=True
                        )
                        calendar.symlink_to(root / "outside.txt")
                    else:
                        outside = root / "outside-calendars"
                        self.write(
                            root,
                            "outside-calendars/edp-team.ics",
                            "outside",
                        )
                        calendar.parent.parent.mkdir(
                            parents=True, exist_ok=True
                        )
                        calendar.parent.symlink_to(
                            outside, target_is_directory=True
                        )

                    self.git(root, "init", "-q")

                    with self.assertRaisesRegex(
                        ValueError, "Symlinked publication path refused"
                    ):
                        stage_publication_outputs(root, [{
                            "manifest": manifest,
                            "missing_match_sync": True,
                        }])

                    self.assertEqual(
                        self.git(
                            root, "diff", "--cached", "--name-only"
                        ),
                        "",
                    )


if __name__ == "__main__":
    unittest.main()
