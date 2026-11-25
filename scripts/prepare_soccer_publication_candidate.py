"""Prepare an unpublished soccer calendar candidate commit.

Run only inside a disposable, detached, remote-free checkout.
No fetch, push, or production publication is performed.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Disable bytecode before importing local modules so disposable
# candidate checkouts remain free of generated __pycache__ files.
sys.dont_write_bytecode = True

from rehearse_soccer_publication import build_and_stage, git_output
from soccer_publication_candidate import create_candidate_commit


def prepare_candidate(checkout, input_sha):
    checkout = Path(checkout).resolve()

    if not isinstance(input_sha, str) or not re.fullmatch(
        r"[0-9a-f]{40}", input_sha
    ):
        raise ValueError("Full input Git SHA required")

    actual_root = Path(
        git_output(checkout, "rev-parse", "--show-toplevel")
    ).resolve()

    if actual_root != checkout:
        raise RuntimeError("Incorrect repository root")

    if git_output(checkout, "rev-parse", "HEAD") != input_sha:
        raise RuntimeError("Checkout differs from input SHA")

    if git_output(checkout, "remote"):
        raise RuntimeError("Isolated checkout still has a Git remote")

    attached = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=checkout,
        capture_output=True,
        check=False,
    )

    if attached.returncode == 0:
        raise RuntimeError("Candidate checkout must use detached HEAD")

    if attached.returncode != 1:
        raise RuntimeError("Cannot verify detached HEAD")

    staged, _allowed, _events = build_and_stage(checkout, input_sha)
    candidate = create_candidate_commit(checkout, input_sha)

    return candidate, staged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()

    try:
        candidate, staged = prepare_candidate(
            Path.cwd(), args.head
        )
    except (
        RuntimeError,
        ValueError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(1, f"Candidate preparation FAILED: {exc}\n")

    print(f"CANDIDATE SHA: {candidate}")
    print(f"Committed publication paths: {len(staged)}")
    for path in staged:
        print(f"  COMMITTED: {path}")
    print("PASS: Candidate created locally; no publication performed")


if __name__ == "__main__":
    main()
