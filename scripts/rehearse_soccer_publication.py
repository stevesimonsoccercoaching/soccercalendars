"""Rehearse soccer publication in a disposable checkout, without publishing."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


def run(root, *command, **kwargs):
    return subprocess.run(
        command, cwd=root, check=True, **kwargs
    )


def git_output(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True
    ).strip()


def rehearse(root):
    root = Path(root).resolve()

    if Path(git_output(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise RuntimeError("Must use the Git repository root")

    head = git_output(root, "rev-parse", "HEAD")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    with tempfile.TemporaryDirectory(prefix="soccer-rehearsal-") as tmp:
        checkout = Path(tmp) / "repository"

        run(
            root, "git", "clone", "--quiet", "--local",
            "--no-hardlinks", str(root), str(checkout),
        )
        run(checkout, "git", "checkout", "--quiet", "--detach", head)
        run(checkout, "git", "remote", "remove", "origin")

        if git_output(checkout, "remote"):
            raise RuntimeError("Disposable checkout has a Git remote")

        if git_output(checkout, "rev-parse", "HEAD") != head:
            raise RuntimeError("Disposable checkout commit mismatch")

        print("PHASE 1: Build calendars", flush=True)
        run(
            checkout, sys.executable,
            "scripts/run_soccer_builds.py",
            "--execute", "--head", head,
            env=env,
        )

        sys.path.insert(0, str(checkout / "scripts"))
        from plan_soccer_builds import plan
        from prepare_soccer_checkpoints import prepare_checkpoints
        from soccer_publication_paths import (
            expected_publication_paths,
            stage_publication_outputs,
        )

        events, errors, warnings = plan(checkout)
        if errors or not events:
            raise RuntimeError(
                "Invalid or empty build plan: " + "; ".join(errors)
            )

        print("PHASE 2: Prepare candidate checkpoints", flush=True)
        prepare_checkpoints(checkout, events, head)

        print("PHASE 3: Verify and stage publication outputs", flush=True)
        allowed = set(expected_publication_paths(checkout, events))

        expected_feeds = {p for p in allowed if p.endswith(".ics")}
        actual_feeds = {
            p.relative_to(checkout).as_posix()
            for p in (checkout / "docs").rglob("*.ics")
        }
        if actual_feeds != expected_feeds:
            raise RuntimeError(
                "Calendar inventory mismatch: "
                f"missing={sorted(expected_feeds - actual_feeds)}, "
                f"unexpected={sorted(actual_feeds - expected_feeds)}"
            )

        staged = stage_publication_outputs(checkout, events)

        if not set(staged).issubset(allowed):
            raise RuntimeError("Unauthorized publication paths staged")

        run(checkout, "git", "diff", "--exit-code")

        untracked = git_output(
            checkout, "ls-files", "--others", "--exclude-standard"
        )
        if untracked:
            raise RuntimeError(
                f"Unexpected untracked files: {untracked}"
            )

        print("\nREHEARSAL RESULTS")
        print(f"Registered events: {len(events)}")
        print(f"Authorized publication files: {len(allowed)}")
        print(f"Files staged with changes: {len(staged)}")

        for path in staged:
            print(f"  STAGED: {path}")

        print("PASS: No unauthorized publication changes")
        print("PASS: No commit or push performed")

    print("PASS: Disposable checkout removed")
    print("PASS: Original repository untouched")


if __name__ == "__main__":
    rehearse(Path(__file__).resolve().parents[1])
