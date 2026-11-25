import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/watch-sffk-teamapp-calendar.yml"
STATE = "state/weave/sffk-2015-2016.json"


def git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


class WeavePushRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.watcher = self.root / "watcher"
        self.writer = self.root / "writer"

        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        marker = (
            "      - name: Commit updated watch state\n"
            "        shell: bash\n"
            "        run: |\n"
        )

        if workflow_text.count(marker) != 1:
            self.fail("Cannot locate watcher publication script")

        script_lines = []
        for line in workflow_text.split(marker, 1)[1].splitlines():
            if line and not line.startswith("          "):
                break
            script_lines.append(line[10:] if line else "")

        self.script = "\n".join(script_lines) + "\n"
        self.assertTrue(self.script.strip())

        git(self.root, "init", "--bare",
            "--initial-branch=main", str(self.origin))

        git(self.root, "init", "-b", "main", str(self.seed))
        git(self.seed, "config", "user.name", "Test User")
        git(self.seed, "config", "user.email", "test@example.invalid")

        self.write(self.seed, STATE, "original\n")
        self.write(self.seed, "docs/index.html", "original index\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "Initial state")
        git(self.seed, "remote", "add", "origin", str(self.origin))
        git(self.seed, "push", "-u", "origin", "main")

        git(self.root, "clone", str(self.origin), str(self.watcher))
        git(self.root, "clone", str(self.origin), str(self.writer))

        git(self.writer, "config", "user.name", "Other Writer")
        git(self.writer, "config", "user.email", "writer@example.invalid")

    def write(self, root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def remote_file(self, relative):
        return git(
            self.root,
            f"--git-dir={self.origin}",
            "show",
            f"main:{relative}",
        )

    def prepare_writer(self, relative, content):
        self.write(self.writer, relative, content)
        git(self.writer, "add", relative)
        git(self.writer, "commit", "-m", "Concurrent update")

    def run_watcher(self, inject_race=False):
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        if inject_race:
            bin_dir = self.root / "bin"
            bin_dir.mkdir(exist_ok=True)
            wrapper = bin_dir / "git"
            wrapper.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = "push" ] && '
                '[ "$2" = "origin" ] && '
                '[ "$3" = "HEAD:refs/heads/main" ] && '
                '[ ! -f "$RACE_SENTINEL" ]; then\n'
                '  touch "$RACE_SENTINEL"\n'
                '  "$REAL_GIT" -C "$RACE_CHECKOUT" '
                'push origin HEAD:refs/heads/main || exit 98\n'
                'fi\n'
                'exec "$REAL_GIT" "$@"\n'
            )
            wrapper.chmod(0o755)
            env["REAL_GIT"] = shutil.which("git")
            env["RACE_CHECKOUT"] = str(self.writer)
            env["RACE_SENTINEL"] = str(self.root / "race-fired")
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

        return subprocess.run(
            ["bash", "-c", self.script],
            cwd=self.watcher,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_rejected_push_recovers_without_losing_updates(self):
        self.write(self.watcher, STATE, "watcher update\n")
        self.prepare_writer("docs/index.html", "publisher update\n")

        result = self.run_watcher(inject_race=True)

        self.assertEqual(
            result.returncode, 0,
            result.stdout + result.stderr,
        )
        self.assertIn("Push attempt 2 of 3", result.stdout)
        self.assertEqual(self.remote_file(STATE), "watcher update")
        self.assertEqual(
            self.remote_file("docs/index.html"),
            "publisher update",
        )

    def test_conflicting_changes_stop_without_force_push(self):
        self.write(self.watcher, STATE, "watcher update\n")
        self.prepare_writer(STATE, "conflicting update\n")

        result = self.run_watcher(inject_race=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.remote_file(STATE),
            "conflicting update",
        )

    def test_development_branch_cannot_publish(self):
        git(self.watcher, "checkout", "-b", "integration/test")
        self.write(self.watcher, STATE, "watcher update\n")

        result = self.run_watcher()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_file(STATE), "original")


if __name__ == "__main__":
    unittest.main()
