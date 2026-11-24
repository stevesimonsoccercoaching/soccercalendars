"""Coordinate verified soccer publication to the trusted main branch."""

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

import soccer_publication_push
from check_soccer_publication_preflight import check_preflight
from prepare_soccer_publication_candidate import prepare_candidate
from soccer_publication_transfer import transfer_candidate_commit
from soccer_publication_push import push_publication_commit


def git(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True,
        stderr=subprocess.PIPE,
    ).strip()


def publish_calendars(publisher, expected_base):
    """Publish a verified candidate, or return None if unchanged."""
    publisher = Path(publisher).resolve()

    if not isinstance(expected_base, str) or not re.fullmatch(
        r"[0-9a-f]{40}", expected_base
    ):
        raise ValueError("Full expected base SHA required")

    trusted = soccer_publication_push.TRUSTED_PRODUCTION_REPOSITORY_URL
    fetch_urls = git(
        publisher, "remote", "get-url", "--all", "origin"
    ).splitlines()
    push_urls = git(
        publisher, "remote", "get-url", "--push", "--all", "origin"
    ).splitlines()

    if fetch_urls != [trusted] or push_urls != [trusted]:
        raise RuntimeError(
            "Publication refused: unauthorized or inconsistent remote"
        )

    check_preflight(publisher, expected_base)

    with tempfile.TemporaryDirectory(
        prefix="soccer-publication-candidate-"
    ) as temporary:
        candidate = Path(temporary) / "candidate"

        subprocess.run(
            [
                "git", "clone", "--quiet", "--local",
                "--no-hardlinks", str(publisher), str(candidate),
            ],
            cwd=publisher,
            check=True,
            capture_output=True,
            text=True,
        )

        git(candidate, "checkout", "--quiet", "--detach", expected_base)
        git(candidate, "remote", "remove", "origin")
        git(candidate, "config", "user.name", "github-actions[bot]")
        git(
            candidate, "config", "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )

        try:
            candidate_sha, staged = prepare_candidate(
                candidate, expected_base
            )
        except RuntimeError as exc:
            if str(exc) != "Candidate refused: empty staged publication":
                raise

            if (
                git(candidate, "rev-parse", "HEAD") != expected_base
                or git(
                    candidate, "status", "--porcelain",
                    "--untracked-files=all"
                )
            ):
                raise RuntimeError(
                    "No-change candidate is not clean"
                ) from exc

            check_preflight(publisher, expected_base)
            print("PASS: No publication changes; no push performed.")
            return None

        print(f"Candidate SHA: {candidate_sha}")
        print(f"Changed publication outputs: {len(staged)}")

        transferred = transfer_candidate_commit(
            publisher, candidate, expected_base, candidate_sha
        )
        if transferred != candidate_sha:
            raise RuntimeError("Transferred candidate SHA mismatch")

        published = push_publication_commit(publisher, expected_base)
        if published != candidate_sha:
            raise RuntimeError("Published candidate SHA mismatch")

        print(f"PASS: Published verified candidate {published}")
        return published


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument(
        "--publish", action="store_true",
        help="Explicitly authorize guarded production publication",
    )
    args = parser.parse_args()

    if not args.publish:
        parser.error(
            "Explicit --publish required; no publication attempted"
        )

    try:
        publish_calendars(
            Path(__file__).resolve().parents[1],
            args.expected_head,
        )
    except (
        RuntimeError, ValueError, OSError,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(1, f"Publication FAILED: {exc}\n")


if __name__ == "__main__":
    main()
