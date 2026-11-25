
"""Prepare candidate soccer publication checkpoints.

This module does not commit, push, or publish calendars.
"""

import json
import os
import tempfile
import re
import subprocess
import sys
from pathlib import Path

from soccer_paths import resolve_event_paths


def prepare_checkpoints(root, events, input_sha):
    """Validate all events before writing candidate checkpoints."""
    root = Path(root).resolve()

    if (
        not isinstance(input_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", input_sha)
    ):
        raise ValueError("Input commit must be a full 40-character Git SHA")

    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{input_sha}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0 or result.stdout.strip() != input_sha:
        raise ValueError("Input SHA does not identify an existing Git commit")

    checked_out = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    if checked_out.returncode != 0 or checked_out.stdout.strip() != input_sha:
        raise ValueError("Input commit must match checked-out HEAD")

    source_status = subprocess.run(
        [
            "git", "status", "--porcelain=v1",
            "--untracked-files=all", "--",
            "data/", "config/",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    if source_status.returncode != 0:
        raise ValueError("Cannot verify source working-tree status")

    if source_status.stdout.strip():
        raise ValueError(
            "Uncommitted source inputs detected under data/ or config/"
        )

    # Phase 1: Resolve every checkpoint path before writing.
    pending = []

    for event in events:
        if not event["missing_match_sync"]:
            continue

        manifest = Path(event["manifest"])
        registration = json.loads(
            (root / manifest).read_text(encoding="utf-8")
        )

        paths = resolve_event_paths(
            manifest,
            registration["competition"],
            repo_root=root,
        )

        checkpoint = paths.state_dir / "processing_checkpoint.json"

        if checkpoint.is_dir():
            raise IsADirectoryError(
                f"Checkpoint destination is a directory: {checkpoint}"
            )

        pending.append(checkpoint)

    # Stage new contents and backups before replacing any checkpoint.
    payload = json.dumps({
        "version": 1,
        "last_published_input_commit": input_sha,
    }, indent=2).encode("utf-8") + bytes([10])

    def stage(destination, contents):
        fd, name = tempfile.mkstemp(
            prefix=".checkpoint-", suffix=".tmp",
            dir=destination.parent,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError as cleanup_exc:
                print(
                    f"WARNING: checkpoint staging cleanup failed: "
                    f"{name}: {cleanup_exc}",
                    file=sys.stderr,
                )
            raise
        return Path(name)

    staged = []
    replaced = []
    rollback_incomplete = False

    try:
        for checkpoint in pending:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            original = (
                checkpoint.read_bytes()
                if checkpoint.exists() else None
            )

            entry = [checkpoint, None, None]
            staged.append(entry)
            entry[1] = stage(checkpoint, payload)

            if original is not None:
                entry[2] = stage(checkpoint, original)

        for checkpoint, candidate, backup in staged:
            os.replace(candidate, checkpoint)
            replaced.append((checkpoint, backup))

    except Exception:
        rollback_errors = []

        for checkpoint, backup in reversed(replaced):
            try:
                if backup is None:
                    checkpoint.unlink()
                else:
                    os.replace(backup, checkpoint)
            except OSError as exc:
                rollback_errors.append((checkpoint, backup, exc))

        if rollback_errors:
            rollback_incomplete = True
            details = "; ".join(
                f"checkpoint={checkpoint}; backup={backup}; error={exc}"
                for checkpoint, backup, exc in rollback_errors
            )
            raise RuntimeError(
                "Checkpoint rollback incomplete; manual recovery required: "
                + details
            ) from rollback_errors[0][2]
        raise

    finally:
        cleanup_errors = []

        for checkpoint, candidate, backup in staged:
            temporaries = (
                (candidate,) if rollback_incomplete
                else (candidate, backup)
            )

            for temporary in temporaries:
                if temporary is None:
                    continue

                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_errors.append((temporary, exc))

        if cleanup_errors:
            for temporary, exc in cleanup_errors:
                print(
                    f"WARNING: checkpoint cleanup failed: "
                    f"{temporary}: {exc}",
                    file=sys.stderr,
                )

            if sys.exc_info()[0] is None:
                raise RuntimeError(
                    "Checkpoint cleanup incomplete; inspect "
                    "the temporary files before publishing"
                )
