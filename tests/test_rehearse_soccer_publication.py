"""Integration tests for the disposable publication rehearsal."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

EVENT = "2026-27/edp/edp-north-atlantic/events/54578"
MANIFEST = f"config/{EVENT}/manifest.json"
STATE = f"state/{EVENT}"
FEED = "docs/2026-27/edp-north-atlantic/demo-team.ics"


class PublicationRehearsalTests(unittest.TestCase):

    def write(self, root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def git(self, root, *args):
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True
        ).strip()

    def fixture(self, root, mode):
        self.git(root, "init", "-q")

        for name in (
            "soccer_paths.py",
            "soccer_publication_paths.py",
            "rehearse_soccer_publication.py",
            "soccer_publication_candidate.py",
            "prepare_soccer_publication_candidate.py",
        ):
            destination = root / "scripts" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SCRIPTS / name, destination)

        self.write(root, MANIFEST, json.dumps({
            "competition": {
                "publication_path": "2026-27/edp-north-atlantic"
            },
            "teams": [{"slug": "demo-team"}],
        }))

        self.write(root, "docs/index.html", "index\n")
        self.write(root, FEED, "original calendar\n")
        self.write(root, f"{STATE}/revisions.json", "{}\n")
        self.write(root, f"{STATE}/missing_matches.json", "{}\n")
        self.write(
            root, f"{STATE}/processing_checkpoint.json",
            '{"version": 1}\n',
        )
        self.write(root, "build_mode.txt", mode)

        self.write(
            root, "scripts/plan_soccer_builds.py",
            f"def plan(root):\n"
            f"    return ([{{'manifest': {MANIFEST!r}, "
            f"'missing_match_sync': True}}], [], [])\n",
        )

        self.write(
            root, "scripts/prepare_soccer_checkpoints.py",
            f"from pathlib import Path\n"
            f"def prepare_checkpoints(root, events, input_sha):\n"
            f"    Path(root, {str(STATE + '/processing_checkpoint.json')!r})"
            f".write_text(input_sha + '\\n')\n",
        )

        self.write(
            root, "scripts/run_soccer_builds.py",
            "from pathlib import Path\n"
            "import subprocess\n"
            "mode = Path('build_mode.txt').read_text().strip()\n"
            "if subprocess.check_output(['git', 'remote']).strip():\n"
            "    raise SystemExit('Remote still connected')\n"
            "if mode == 'fail':\n"
            "    raise SystemExit(7)\n"
            f"Path({FEED!r}).write_text('updated calendar\\n')\n"
            "if mode == 'unexpected':\n"
            "    Path('docs/2026-27/edp-north-atlantic/"
            "unregistered.ics').write_text('unexpected\\n')\n",
        )

        self.git(root, "add", "-A")
        subprocess.run(
            [
                "git", "-c", "user.name=Test",
                "-c", "user.email=test@example.com",
                "commit", "-qm", "fixture",
            ],
            cwd=root, check=True,
        )

    def execute(self, root):
        code = (
            "import sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "from rehearse_soccer_publication import rehearse; "
            "rehearse(sys.argv[2])"
        )

        head_before = self.git(root, "rev-parse", "HEAD")

        result = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPTS), str(root)],
            cwd=root,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        self.assertEqual(
            self.git(root, "rev-parse", "HEAD"),
            head_before,
        )
        self.assertEqual(self.git(root, "status", "--porcelain"), "")

        return result

    def test_success_stages_calendar_and_checkpoint_in_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, "success")
            result = self.execute(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Files staged with changes: 2", result.stdout)
            self.assertIn(f"STAGED: {FEED}", result.stdout)
            self.assertIn(
                f"STAGED: {STATE}/processing_checkpoint.json",
                result.stdout,
            )
            self.assertIn("PASS: Original repository untouched",
                          result.stdout)

    def test_unregistered_calendar_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, "unexpected")
            result = self.execute(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Calendar inventory mismatch", result.stderr)
            self.assertIn("PHASE 3:", result.stdout)
            self.assertNotIn("PASS: No commit or push performed",
                             result.stdout)

    def test_failed_builder_stops_before_checkpoint_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, "fail")
            result = self.execute(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("PHASE 2:", result.stdout)
            self.assertNotIn("PHASE 3:", result.stdout)


    def candidate_fixture(self, parent, mode, keep_remote=False):
        source = parent / "source"
        source.mkdir()
        self.fixture(source, mode)

        checkout = parent / "checkout"
        self.git(
            parent, "clone", "-q", "--local",
            "--no-hardlinks", str(source), str(checkout),
        )

        head = self.git(source, "rev-parse", "HEAD")
        self.git(checkout, "checkout", "-q", "--detach", head)

        if not keep_remote:
            self.git(checkout, "remote", "remove", "origin")

        self.git(checkout, "config", "user.name", "Test")
        self.git(checkout, "config", "user.email", "test@example.com")

        return source, checkout, head

    def run_candidate(self, checkout, head):
        return subprocess.run(
            [
                sys.executable,
                "scripts/prepare_soccer_publication_candidate.py",
                "--head", head,
            ],
            cwd=checkout,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_end_to_end_candidate_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, checkout, head = self.candidate_fixture(
                Path(tmp), "success"
            )

            result = self.run_candidate(checkout, head)
            self.assertEqual(result.returncode, 0, result.stderr)

            candidate = self.git(checkout, "rev-parse", "HEAD")
            self.assertNotEqual(candidate, head)
            self.assertEqual(
                self.git(checkout, "rev-parse", "HEAD^"), head
            )
            self.assertEqual(
                set(self.git(
                    checkout, "diff", "--name-only", head, candidate
                ).splitlines()),
                {FEED, f"{STATE}/processing_checkpoint.json"},
            )
            self.assertEqual(
                self.git(source, "rev-parse", "HEAD"), head
            )
            self.assertEqual(
                self.git(checkout, "status", "--porcelain"), ""
            )
            self.assertEqual(self.git(checkout, "remote"), "")
            self.assertIn(
                "no publication performed", result.stdout
            )

    def test_end_to_end_failed_builder_creates_no_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, checkout, head = self.candidate_fixture(
                Path(tmp), "fail"
            )

            result = self.run_candidate(checkout, head)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                self.git(checkout, "rev-parse", "HEAD"), head
            )
            self.assertNotIn("CANDIDATE SHA:", result.stdout)

    def test_candidate_refuses_remote_before_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, checkout, head = self.candidate_fixture(
                Path(tmp), "success", keep_remote=True
            )

            result = self.run_candidate(checkout, head)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Git remote", result.stderr)
            self.assertNotIn("PHASE 1:", result.stdout)
            self.assertEqual(
                self.git(checkout, "rev-parse", "HEAD"), head
            )


if __name__ == "__main__":
    unittest.main()
