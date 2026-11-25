"""Create a verified soccer publication commit in an isolated checkout.

This module never pushes, fetches, or publishes to a remote.
"""

import re
import subprocess
from pathlib import Path

from plan_soccer_builds import plan
from soccer_publication_paths import expected_publication_paths


def git(root, *args):
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


def git_paths(root, *args):
    raw = subprocess.check_output(
        ["git", *args],
        cwd=root,
        stderr=subprocess.PIPE,
    )
    return {
        item.decode("utf-8")
        for item in raw.split(b"\0")
        if item
    }


def create_candidate_commit(root, input_sha):
    """Commit only validated, staged publication outputs."""
    root = Path(root).resolve()

    actual_root = Path(
        git(root, "rev-parse", "--show-toplevel")
    ).resolve()

    if actual_root != root:
        raise RuntimeError(
            "Candidate refused: incorrect repository root"
        )

    if (
        not isinstance(input_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", input_sha)
    ):
        raise ValueError("Candidate refused: invalid input SHA")

    head = git(root, "rev-parse", "HEAD")

    if head != input_sha:
        raise RuntimeError(
            "Candidate refused: checked-out commit differs "
            "from input SHA"
        )

    # Candidate commits are created only on detached HEAD.
    attached = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=root,
        capture_output=True,
    )

    if attached.returncode == 0:
        raise RuntimeError(
            "Candidate refused: checkout must be detached"
        )

    if git(root, "remote"):
        raise RuntimeError(
            "Candidate refused: isolated checkout has a remote"
        )

    events, errors, _warnings = plan(root)

    if errors or not events:
        raise RuntimeError(
            "Candidate refused: invalid event registration: "
            + "; ".join(errors or ["No registered events"])
        )

    allowed = set(expected_publication_paths(root, events))

    staged = git_paths(
        root,
        "diff", "--cached", "--name-only",
        "--no-renames", "-z",
    )

    if not staged:
        raise RuntimeError(
            "Candidate refused: empty staged publication"
        )

    unauthorized = staged - allowed

    if unauthorized:
        raise RuntimeError(
            "Candidate refused: unauthorized staged paths: "
            + repr(sorted(unauthorized))
        )

    # Refuse partially staged modifications.
    unstaged = subprocess.run(
        ["git", "diff", "--quiet", "--exit-code"],
        cwd=root,
        capture_output=True,
    )

    if unstaged.returncode != 0:
        raise RuntimeError(
            "Candidate refused: unstaged changes detected"
        )

    untracked = git_paths(
        root, "ls-files", "--others",
        "--exclude-standard", "-z",
    )

    if untracked:
        raise RuntimeError(
            "Candidate refused: unexpected untracked files: "
            + repr(sorted(untracked))
        )

    # Every registered output must exist without symlink traversal.
    for relative in sorted(allowed):
        destination = root

        for component in Path(relative).parts:
            destination = destination / component

            if destination.is_symlink():
                raise RuntimeError(
                    "Candidate refused: symlinked publication path: "
                    + relative
                )

        if not destination.is_file():
            raise RuntimeError(
                "Candidate refused: missing publication output: "
                + relative
            )

    # Do not execute repository hooks or require signing for
    # this disposable, unpublished candidate commit.
    subprocess.run(
        [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "-c", "commit.gpgsign=false",
            "commit", "-m",
            "Candidate soccer calendar publication",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    candidate = git(root, "rev-parse", "HEAD")

    if git(root, "rev-parse", "HEAD^") != input_sha:
        raise RuntimeError(
            "Candidate parent does not match input commit"
        )

    if git(root, "status", "--porcelain"):
        raise RuntimeError(
            "Candidate checkout is not clean after commit"
        )

    return candidate
