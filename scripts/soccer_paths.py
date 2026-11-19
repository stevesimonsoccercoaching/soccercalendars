"""Resolve a soccer event's storage paths and stable publication directory.

Each event's config/, data/, and state/ directories follow the location of
its manifest. By default its generated ICS files do as well. A manifest can
instead set competition.publication_path to a *relative* directory beneath
docs/ to preserve existing subscription URLs when the event is relocated.

Example after migrating a legacy CJSL event:
    manifest: config/2026-27/cjsl/league/events/55206/manifest.json
    competition.publication_path: "2026/cjsl/fall"

    data/2026-27/cjsl/league/events/55206/
    state/2026-27/cjsl/league/events/55206/
    docs/2026/cjsl/fall/                  (existing public URL)

This module only calculates paths. It does not move, read, or write files;
callers must validate publication-path uniqueness across registered events.
"""

from dataclasses import dataclass
from pathlib import Path
import re


# Match the portable directory names accepted by manifest discovery.
_SAFE_SEGMENT = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_SAFE_PUBLICATION_PATH = re.compile(
    rf"{_SAFE_SEGMENT}(?:/{_SAFE_SEGMENT})*\Z"
)


@dataclass(frozen=True)
class SoccerEventPaths:
    """Absolute locations plus the two relative event/publication directories."""

    manifest: Path
    event_relative: Path
    publication_relative: Path
    data_dir: Path
    state_dir: Path
    docs_dir: Path


def resolve_event_paths(manifest_path, competition, *, repo_root="."):
    """Resolve directories without altering legacy behavior unless opted in.

    ``competition['publication_path']`` is relative to ``docs/``, NOT to the
    repository root, and cannot escape ``docs/``. It is deliberately optional
    so old event manifests retain precisely their existing output paths.

    Raises ValueError for invalid location/configuration; no files are touched.
    """
    root = Path(repo_root).resolve()
    manifest = Path(manifest_path)
    if not manifest.is_absolute():
        manifest = root / manifest
    manifest = manifest.resolve()

    try:
        event_relative = manifest.parent.relative_to(root / "config")
    except ValueError as exc:
        raise ValueError("Manifest must reside beneath this repository's config/") from exc
    if manifest.name != "manifest.json" or event_relative == Path("."):
        raise ValueError("Expected config/<event-directory>/manifest.json")
    if not isinstance(competition, dict):
        raise ValueError("Manifest competition must be an object")

    requested = competition.get("publication_path")
    if requested is None:
        publication_relative = event_relative
    else:
        if not isinstance(requested, str) or not _SAFE_PUBLICATION_PATH.fullmatch(requested):
            raise ValueError(
                "competition.publication_path must be a nonempty slash-separated "
                "lowercase directory beneath docs/ (no ., .., or backslashes)"
            )
        publication_relative = Path(requested)

    return SoccerEventPaths(
        manifest=manifest,
        event_relative=event_relative,
        publication_relative=publication_relative,
        data_dir=root / "data" / event_relative,
        state_dir=root / "state" / event_relative,
        docs_dir=root / "docs" / publication_relative,
    )
