"""Safely push an already-committed soccer publication."""

import re
import subprocess
from pathlib import Path


def git(root, *args):
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


def push_publication_commit(root, expected_base, allowed_paths):
    """Push one verified publication commit to main without force."""
    root = Path(root).resolve()

    actual_root = Path(
        git(root, "rev-parse", "--show-toplevel")
    ).resolve()

    if actual_root != root:
        raise RuntimeError("Publication refused: incorrect repository root")

    if not re.fullmatch(r"[0-9a-f]{40}", expected_base):
        raise ValueError("Publication refused: invalid base commit SHA")

    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")

    if branch != "main":
        raise RuntimeError("Publication refused: branch must be main")

    head = git(root, "rev-parse", "HEAD")
    parent = git(root, "rev-parse", "HEAD^")

    if parent != expected_base:
        raise RuntimeError(
            "Publication refused: commit parent differs from expected base"
        )

    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(
            "Publication refused: working tree or index is not clean"
        )

    changed_raw = subprocess.check_output(
        [
            "git", "diff", "--name-only", "--no-renames",
            "-z", expected_base, head,
        ],
        cwd=root,
    )
    changed = set(changed_raw.decode("utf-8").split("\0")) - {""}
    allowed = set(allowed_paths)

    if not changed:
        raise RuntimeError("Publication refused: empty publication commit")

    unexpected = changed - allowed

    if unexpected:
        raise RuntimeError(
            f"Publication refused: unauthorized committed paths: "
            f"{sorted(unexpected)}"
        )

    fetch_urls = git(
        root, "remote", "get-url", "--all", "origin"
    ).splitlines()

    push_urls = git(
        root, "remote", "get-url", "--push", "--all", "origin"
    ).splitlines()

    if (
        len(fetch_urls) != 1
        or len(push_urls) != 1
        or fetch_urls[0] != push_urls[0]
    ):
        raise RuntimeError(
            "Publication refused: push destination must match "
            "the single fetch destination"
        )

    remote_result = git(
        root, "ls-remote", "--exit-code",
        "origin", "refs/heads/main",
    )
    remote_base = remote_result.split()[0]

    if remote_base != expected_base:
        raise RuntimeError(
            "Publication refused: remote main differs from expected base"
        )

    # A normal, explicit push. Never use force or force-with-lease.
    # Git rejects non-fast-forward updates if remote main advances.
    subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/main"],
        cwd=root,
        check=True,
    )

    remote_after = git(
        root, "ls-remote", "--exit-code",
        "origin", "refs/heads/main",
    ).split()[0]

    if remote_after != head:
        raise RuntimeError(
            "Publication push completed, but remote HEAD differs"
        )

    return head
