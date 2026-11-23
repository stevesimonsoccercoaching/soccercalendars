"""Read-only safeguards before a production soccer publication build."""

import argparse
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


def check_preflight(root, expected_head):
    root = Path(root).resolve()

    repository = Path(
        git(root, "rev-parse", "--show-toplevel")
    ).resolve()

    if repository != root:
        raise RuntimeError(
            "Publication refused: incorrect repository root"
        )

    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError(
            "Publication refused: expected HEAD must be a full commit SHA"
        )

    branch_result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    if (
        branch_result.returncode != 0
        or branch_result.stdout.strip() != "main"
    ):
        raise RuntimeError(
            "Publication refused: checkout must be on main"
        )

    head = git(root, "rev-parse", "HEAD")

    if head != expected_head:
        raise RuntimeError(
            "Publication refused: checked-out commit differs "
            "from expected commit"
        )

    dirty = git(
        root, "status", "--porcelain", "--untracked-files=all"
    )

    if dirty:
        raise RuntimeError(
            "Publication refused: working tree is not clean"
        )

    remote_result = git(
        root,
        "ls-remote",
        "--exit-code",
        "origin",
        "refs/heads/main",
    )

    remote_head = remote_result.split()[0]

    if remote_head != head:
        raise RuntimeError(
            "Publication refused: remote main has advanced "
            "or differs from checked-out HEAD"
        )

    print("PASS: Production publication preflight checks passed.")
    print(f"Verified commit: {head}")
    print("Verified branch: main")
    print("Verified remote: origin/main")
    print("No repository changes performed.")


def main():
    parser = argparse.ArgumentParser(
        description="Verify production publication prerequisites."
    )
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()

    check_preflight(
        Path(__file__).resolve().parents[1],
        args.expected_head,
    )


if __name__ == "__main__":
    main()
