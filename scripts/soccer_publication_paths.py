"""Calculate permitted soccer publication paths without modifying files."""

import json
import re
from pathlib import Path

from soccer_paths import resolve_event_paths


_SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def expected_publication_paths(root, events):
    """Return sorted repo-relative paths eligible for publication."""
    root = Path(root).resolve()
    allowed = {"docs/index.html"}
    publications = set()
    state_directories = set()

    for event in events:
        manifest = Path(event["manifest"])
        if not manifest.is_absolute():
            manifest = root / manifest

        manifest = manifest.resolve()
        registration = json.loads(
            manifest.read_text(encoding="utf-8")
        )

        paths = resolve_event_paths(
            manifest,
            registration["competition"],
            repo_root=root,
        )

        publication = paths.docs_dir.relative_to(root)
        state = paths.state_dir.relative_to(root)

        if publication in publications:
            raise ValueError(
                f"Duplicate publication directory: {publication}"
            )
        if state in state_directories:
            raise ValueError(
                f"Duplicate state directory: {state}"
            )

        publications.add(publication)
        state_directories.add(state)

        teams = registration["teams"]
        if not isinstance(teams, list) or not teams:
            raise ValueError("Event must register at least one team")

        slugs = set()
        for team in teams:
            slug = team["slug"]

            if (
                not isinstance(slug, str)
                or not _SAFE_SLUG.fullmatch(slug)
                or slug in slugs
            ):
                raise ValueError(
                    f"Invalid or duplicate team slug: {slug!r}"
                )

            slugs.add(slug)
            candidate = (
                publication / f"{slug}.ics"
            ).as_posix()

            if candidate in allowed:
                raise ValueError(
                    f"Duplicate publication file: {candidate}"
                )
            allowed.add(candidate)

        allowed.add(
            (state / "revisions.json").as_posix()
        )

        preserve = event["missing_match_sync"]
        if type(preserve) is not bool:
            raise ValueError(
                "missing_match_sync must be a boolean"
            )

        if preserve:
            allowed.add(
                (state / "missing_matches.json").as_posix()
            )
            allowed.add(
                (state / "processing_checkpoint.json").as_posix()
            )

    return sorted(allowed)


def stage_publication_outputs(root, events):
    """Stage only registered generated outputs, starting with a clean index."""
    import subprocess

    root = Path(root).resolve()
    allowed = expected_publication_paths(root, events)

    repository = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        text=True,
    ).strip()

    if Path(repository).resolve() != root:
        raise ValueError("Publication root must be the Git repository root")

    staged_before = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=root,
    )

    if staged_before:
        raise ValueError(
            "Git index already contains staged changes; publication refused"
        )

    for relative in allowed:
        path = root

        for component in Path(relative).parts:
            path = path / component
            if path.is_symlink():
                raise ValueError(
                    f"Symlinked publication path refused: {relative}"
                )

        if not path.is_file():
            raise FileNotFoundError(
                f"Required publication output missing: {relative}"
            )

    # Unresolved checkpoint backups may indicate incomplete recovery.
    # Never stage or publish while one remains.
    for relative in allowed:
        if Path(relative).name != "processing_checkpoint.json":
            continue

        state_dir = root / Path(relative).parent

        recovery_files = sorted(
            state_dir.glob(".checkpoint-*.tmp")
        )

        if recovery_files:
            raise ValueError(
                "Unresolved checkpoint recovery file; "
                "publication refused: "
                + ", ".join(str(p) for p in recovery_files)
            )

    subprocess.run(
        ["git", "add", "-A", "--", *allowed],
        cwd=root,
        check=True,
    )

    staged_after = set(
        subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "-z"],
            cwd=root,
        ).decode("utf-8").split("\0")
    ) - {""}

    unexpected = staged_after - set(allowed)

    if unexpected:
        raise RuntimeError(
            f"Unexpected staged publication paths: {sorted(unexpected)}"
        )

    return sorted(staged_after)
