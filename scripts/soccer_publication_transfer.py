"""Transfer a verified soccer publication candidate without publishing.

This module never pushes or modifies a remote repository.
"""

import re
import subprocess
from pathlib import Path

import soccer_publication_push
from check_soccer_publication_preflight import check_preflight
from plan_soccer_builds import plan
from soccer_publication_paths import expected_publication_paths


def git(root, *args):
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


def changed_paths(root, base, head):
    output = subprocess.check_output(
        [
            "git", "diff", "--name-only",
            "--no-renames", "-z", base, head,
        ],
        cwd=root,
        stderr=subprocess.PIPE,
    )
    return {
        path.decode("utf-8")
        for path in output.split(b"\0")
        if path
    }


def verify_root(root):
    actual = Path(
        git(root, "rev-parse", "--show-toplevel")
    ).resolve()

    if actual != root:
        raise RuntimeError("Transfer refused: incorrect repository root")


def verify_clean(root):
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("Transfer refused: repository is not clean")


def transfer_candidate_commit(publisher, candidate, base, candidate_sha):
    """Fast-forward local main to a verified candidate; never push."""
    publisher = Path(publisher).resolve()
    candidate = Path(candidate).resolve()

    if publisher == candidate:
        raise RuntimeError("Transfer refused: repositories must differ")

    for value in (base, candidate_sha):
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{40}", value
        ):
            raise ValueError("Transfer refused: full Git SHAs required")

    verify_root(publisher)
    verify_root(candidate)
    verify_clean(publisher)
    verify_clean(candidate)

    if git(candidate, "remote"):
        raise RuntimeError("Transfer refused: candidate has a Git remote")

    detached = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=candidate,
        capture_output=True,
        check=False,
    )
    if detached.returncode != 1:
        raise RuntimeError("Transfer refused: candidate must be detached")

    if git(candidate, "rev-parse", "HEAD") != candidate_sha:
        raise RuntimeError("Transfer refused: candidate HEAD mismatch")

    if git(candidate, "rev-parse", "HEAD^") != base:
        raise RuntimeError("Transfer refused: candidate parent mismatch")

    changed = changed_paths(candidate, base, candidate_sha)

    if not changed:
        raise RuntimeError("Transfer refused: empty candidate commit")

    # Derive authorization from the publisher's registered events,
    # not from a list supplied by the candidate or its caller.
    events, errors, _warnings = plan(publisher)

    if errors or not events:
        raise RuntimeError(
            "Transfer refused: invalid publication registration: "
            + "; ".join(errors or ["No events"])
        )

    allowed = set(expected_publication_paths(publisher, events))
    unexpected = changed - allowed

    if unexpected:
        raise RuntimeError(
            "Transfer refused: unauthorized candidate paths: "
            + repr(sorted(unexpected))
        )

    # Inspect committed Git objects, not just their filenames.
    # Symlinks and submodules must never become publication outputs.
    tree_data = subprocess.check_output(
        [
            "git", "ls-tree", "--full-tree",
            "-r", "-z", candidate_sha,
        ],
        cwd=candidate,
        stderr=subprocess.PIPE,
    )

    tree_entries = {}

    for record in tree_data.split(b"\0"):
        if not record:
            continue

        metadata, separator, path_bytes = record.partition(b"\t")

        if not separator:
            raise RuntimeError(
                "Transfer refused: malformed candidate tree entry"
            )

        mode, object_type, _object_sha = metadata.split(b" ", 2)
        relative = path_bytes.decode("utf-8")

        if relative in allowed:
            tree_entries[relative] = (mode, object_type)

    for relative in sorted(changed):
        mode_and_type = tree_entries.get(relative)

        if mode_and_type not in (
            (b"100644", b"blob"),
            (b"100755", b"blob"),
        ):
            raise RuntimeError(
                "Transfer refused: non-regular candidate output: "
                + relative
            )

    # The complete candidate tree must contain every registered
    # publication output, including unchanged files.
    for relative in sorted(allowed):
        mode_and_type = tree_entries.get(relative)

        if mode_and_type not in (
            (b"100644", b"blob"),
            (b"100755", b"blob"),
        ):
            raise RuntimeError(
                "Transfer refused: missing or non-regular "
                "registered output: " + relative
            )

    # Validate the trusted destination before importing any objects.
    fetch_urls = git(
        publisher, "remote", "get-url", "--all", "origin"
    ).splitlines()
    push_urls = git(
        publisher, "remote", "get-url", "--push", "--all", "origin"
    ).splitlines()

    trusted = soccer_publication_push.TRUSTED_PRODUCTION_REPOSITORY_URL

    if fetch_urls != [trusted] or push_urls != [trusted]:
        raise RuntimeError(
            "Transfer refused: unauthorized or inconsistent remote"
        )

    # Read-only production preflight checks the local main revision,
    # cleanliness, and the current remote main revision.
    check_preflight(publisher, base)

    # Transfer the exact commit object. Do not update remote refs.
    subprocess.run(
        [
            "git", "fetch", "--no-tags",
            "--no-write-fetch-head",
            str(candidate), candidate_sha,
        ],
        cwd=publisher,
        check=True,
        capture_output=True,
        text=True,
    )

    if git(publisher, "cat-file", "-t", candidate_sha) != "commit":
        raise RuntimeError("Transfer refused: candidate object missing")

    if git(publisher, "rev-parse", f"{candidate_sha}^") != base:
        raise RuntimeError("Transfer refused: imported parent mismatch")

    if changed_paths(publisher, base, candidate_sha) != changed:
        raise RuntimeError("Transfer refused: imported changes mismatch")

    # Recheck the remote after import, before advancing local main.
    # The remote may have changed since the initial preflight.
    remote_after = git(
        publisher,
        "ls-remote",
        "--exit-code",
        "origin",
        "refs/heads/main",
    ).split()[0]

    if remote_after != base:
        raise RuntimeError(
            "Transfer refused: remote main has advanced "
            "during candidate transfer"
        )

    # Advance local main only. No push, no force, no remote mutation.
    subprocess.run(
        [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "merge", "--ff-only", candidate_sha,
        ],
        cwd=publisher,
        check=True,
        capture_output=True,
        text=True,
    )

    if git(publisher, "rev-parse", "HEAD") != candidate_sha:
        raise RuntimeError("Transfer refused: local HEAD mismatch")

    verify_clean(publisher)
    return candidate_sha
