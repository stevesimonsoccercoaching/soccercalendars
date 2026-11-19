"""Verify CJSL revision-state v2 using all published calendar data.

Run from the repository root:
    python scripts/verify_cjsl_v2_parity.py --manifest config/2026/cjsl/fall/manifest.json

This is a read-only verification of the PROPOSED revision state. It does not
write a v2 state file, alter published ICS files, or activate migration.
Its only generated files are written to a temporary directory.

Checks:
  1. The existing migration preview recovers published per-feed revisions.
  2. Version-2 builder reproduces every published ICS byte-for-byte.
  3. A second build with a later timestamp makes no changes whatsoever.
  4. The original shared legacy revision records remain untouched.
"""

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
from build_cjsl_calendars import build_calendar, read_matches
from seed_cjsl_revision_views import preview_revision_seed, RevisionSeedError


class V2ParityError(RuntimeError):
    """Stop an unsafe state-format migration before any real changes."""


def verify_v2_parity(root, manifest_name):
    root = Path(root).resolve()
    manifest_path = Path(manifest_name)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.resolve()

    try:
        relative = manifest_path.parent.relative_to(root / "config")
    except ValueError as exc:
        raise V2ParityError("Manifest must be beneath config/") from exc

    proposed_state, baseline = preview_revision_seed(root, manifest_path)
    if proposed_state.get("version") != 2:
        raise V2ParityError("Migration preview did not return version-2 state")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    competition = manifest["competition"]
    defaults = manifest.get("calendar_defaults", {})
    display_names = manifest.get("display_names", {})
    default_duration = int(competition.get("default_duration_minutes", 60))
    teams = manifest["teams"]

    if len(proposed_state["views"]) != len(teams):
        raise V2ParityError("Proposed view count differs from manifest team count")

    initial_state = copy.deepcopy(proposed_state)
    feed_count = 0
    event_count = 0

    with tempfile.TemporaryDirectory(prefix="cjsl-v2-parity-") as scratch:
        scratch_path = Path(scratch)
        for round_number, timestamp in enumerate(("20990101T000000Z", "20990102T000000Z"), 1):
            for team in teams:
                slug = team["slug"]
                workbook = root / "data" / relative / f"{slug}.xlsx"
                published = root / "docs" / relative / f"{slug}.ics"
                output = scratch_path / f"round-{round_number}" / f"{slug}.ics"
                matches = read_matches(
                    input_file=workbook,
                    tracked_team_name=team["name"],
                    duration_minutes=int(team.get("duration_minutes", default_duration)),
                )
                build_calendar(
                    competition=competition,
                    defaults=defaults,
                    display_names=display_names,
                    team=team,
                    matches=matches,
                    output_file=output,
                    revision_state=proposed_state,
                    changed_at=timestamp,
                )
                if output.read_bytes() != published.read_bytes():
                    raise V2ParityError(
                        f"Round {round_number}: version-2 build changed published feed: {slug}"
                    )
                if round_number == 1:
                    feed_count += 1
                    event_count += len(matches)

            if proposed_state != initial_state:
                raise V2ParityError(
                    f"Round {round_number}: no-change rebuild modified version-2 revision state"
                )

    if event_count != baseline["per_calendar_view_records"]:
        raise V2ParityError("Verified event count differs from migration preview")

    return {
        "team_feeds_verified": feed_count,
        "published_event_occurrences": event_count,
        "legacy_records_unchanged": len(initial_state["matches"]),
        "first_build_byte_identical": True,
        "second_build_byte_identical": True,
        "version_two_state_unchanged": True,
        "repository_files_written": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        report = verify_v2_parity(args.root, args.manifest)
    except (V2ParityError, RevisionSeedError, OSError, ValueError,
            KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"CJSL V2 PARITY FAILED: {exc}\n")
    print(json.dumps(report, indent=2))
    print("CJSL version-2 parity verified in a disposable directory; nothing published.")


if __name__ == "__main__":
    main()
