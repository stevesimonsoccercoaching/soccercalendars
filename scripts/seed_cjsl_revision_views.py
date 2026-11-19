"""Prepare and verify CJSL per-calendar revision migration, without writing to repo.

Usage from repo root:
  python scripts/seed_cjsl_revision_views.py --manifest config/2026/cjsl/fall/manifest.json
  python scripts/seed_cjsl_revision_views.py --manifest config/2026/cjsl/fall/manifest.json --json

The old revisions.json contains a shared record per match; this prospective
v2 format keeps that entire legacy history and adds a separate view record for
each team calendar. Historical SEQUENCE and LAST-MODIFIED are read from the
already-published ICS, not guessed from the collision-prone shared state.

STRICT SAFETY REQUIREMENT: each published ICS must be reproduced byte-for-byte
by the existing CJSL builder using that calendar's seeded records and current
XLSX input. Any discrepancy is an ERROR, not a revision bump or automatic edit.

This command does not modify config/, data/, docs/, or state/. It does not
write the candidate state. That will be a separately reviewed step.
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
from build_cjsl_calendars import (
    build_calendar,
    match_fingerprint,
    read_matches,
)


class RevisionSeedError(RuntimeError):
    """Block candidate migration until historical parity is established."""


def load_published_events(path, organizer, event_id):
    if not path.is_file():
        raise RevisionSeedError(f"Published calendar missing: {path}")
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RevisionSeedError(f"{path}: not UTF-8") from exc

    # RFC 5545: folded lines continue with a single space or tab.
    lines = re.sub(r"\r?\n[ \t]", "", text).splitlines()
    records = {}
    current = None
    uid_pattern = re.compile(
        rf"{re.escape(organizer.lower())}-{re.escape(event_id)}-"
        r"match-(\d+)@system\.gotsport\.com\Z"
    )
    for line in lines:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise RevisionSeedError(f"{path}: nested VEVENT")
            current = {}
        elif line == "END:VEVENT":
            if current is None:
                raise RevisionSeedError(f"{path}: unexpected END:VEVENT")
            uid = current.get("UID", "")
            match = uid_pattern.fullmatch(uid)
            if not match:
                raise RevisionSeedError(f"{path}: unexpected UID {uid!r}")
            key = f"{event_id}:{int(match.group(1))}"
            if key in records:
                raise RevisionSeedError(f"{path}: duplicate UID {uid!r}")
            seq = current.get("SEQUENCE", "")
            stamp = current.get("LAST-MODIFIED", "")
            dtstamp = current.get("DTSTAMP", "")
            if not re.fullmatch(r"\d+", seq):
                raise RevisionSeedError(f"{path}: invalid SEQUENCE for {uid}")
            if not re.fullmatch(r"\d{8}T\d{6}Z", stamp):
                raise RevisionSeedError(f"{path}: invalid LAST-MODIFIED for {uid}")
            if dtstamp != stamp:
                raise RevisionSeedError(f"{path}: DTSTAMP differs from LAST-MODIFIED for {uid}")
            records[key] = {
                "sequence": int(seq),
                "last_modified": stamp,
            }
            current = None
        elif current is not None and ":" in line:
            name, value = line.split(":", 1)
            if name in ("UID", "SEQUENCE", "LAST-MODIFIED", "DTSTAMP"):
                if name in current:
                    raise RevisionSeedError(f"{path}: duplicate {name} in VEVENT")
                current[name] = value
    if current is not None:
        raise RevisionSeedError(f"{path}: unclosed VEVENT")
    if not records:
        raise RevisionSeedError(f"{path}: no VEVENTs found")
    return records, content


def team_summary(competition, display_names, team, match):
    tracked = team["name"]
    if match["home_team"] == tracked:
        opponent = match["away_team"]
    elif match["away_team"] == tracked:
        opponent = match["home_team"]
    else:
        raise RevisionSeedError(
            f"{team['slug']}: match {match['match_no']} does not involve configured team"
        )
    short = team.get("short_name", display_names.get(tracked, tracked))
    summary = (
        f"{competition['organizer']}: {short} "
        f"vs {display_names.get(opponent, opponent)}"
    )
    if "cancel" in match["status"].lower():
        summary = f"\u274c Canceled \u2014 {summary}"
    return summary


def preview_revision_seed(root, manifest_path):
    """Return (candidate_v2_state, report). No repository file is modified."""
    root = Path(root).resolve()
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.resolve()
    try:
        relative = manifest_path.parent.relative_to(root / "config")
    except ValueError as exc:
        raise RevisionSeedError("Manifest must be under the repository config/ directory") from exc

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    competition = manifest["competition"]
    organizer = competition["organizer"]
    event_id = str(competition["event_id"])
    teams = manifest["teams"]
    defaults = manifest.get("calendar_defaults", {})
    display_names = manifest.get("display_names", {})
    duration_default = int(competition.get("default_duration_minutes", 60))
    revision_path = root / "state" / relative / "revisions.json"

    legacy = json.loads(revision_path.read_text(encoding="utf-8"))
    if legacy.get("version") != 1 or not isinstance(legacy.get("matches"), dict):
        raise RevisionSeedError(
            f"{revision_path}: expected existing version 1 with legacy matches"
        )
    if legacy.get("views"):
        raise RevisionSeedError("Per-calendar view history already exists; refusing to seed twice")

    views = {}
    all_versions = {}
    event_count = 0
    with tempfile.TemporaryDirectory(prefix="cjsl-revision-seed-") as temp:
        for team in teams:
            slug = team["slug"]
            if slug in views:
                raise RevisionSeedError(f"Duplicate team slug: {slug}")
            workbook = root / "data" / relative / f"{slug}.xlsx"
            published = root / "docs" / relative / f"{slug}.ics"
            saved, original_bytes = load_published_events(published, organizer, event_id)
            matches = read_matches(workbook, team["name"], int(team.get(
                "duration_minutes", duration_default
            )))
            expected_keys = [f"{event_id}:{m['match_no']}" for m in matches]
            if len(expected_keys) != len(set(expected_keys)):
                raise RevisionSeedError(f"{slug}: duplicate XLSX match IDs")
            if set(saved) != set(expected_keys):
                raise RevisionSeedError(
                    f"{slug}: published/XLSX match mismatch: "
                    f"missing in ICS={sorted(set(expected_keys) - set(saved))}; "
                    f"extra in ICS={sorted(set(saved) - set(expected_keys))}"
                )
            view_matches = {}
            for match in matches:
                key = f"{event_id}:{match['match_no']}"
                record = dict(saved[key])
                record["fingerprint"] = match_fingerprint(
                    match, team_summary(competition, display_names, team, match)
                )
                view_matches[key] = record
                all_versions.setdefault(key, []).append(record["sequence"])

            # Use a temporary one-calendar version-1 state as a strict oracle:
            # existing builder should regenerate the published ICS exactly
            # when given that calendar's saved revision numbers/timestamps.
            single_view_state = {"version": 1, "matches": dict(view_matches)}
            rendered = Path(temp) / f"{slug}.ics"
            build_calendar(
                competition=competition,
                defaults=defaults,
                display_names=display_names,
                team=team,
                matches=matches,
                output_file=rendered,
                revision_state=single_view_state,
                changed_at="20990101T000000Z",
            )
            if single_view_state["matches"] != view_matches:
                raise RevisionSeedError(
                    f"{slug}: builder changed revision state in a no-change preview"
                )
            candidate_bytes = rendered.read_bytes()
            if candidate_bytes != original_bytes:
                # A source schedule changed, output format changed, or the
                # committed feeds were already stale. Stop, do not mask it.
                mismatch_at = next(
                    (i for i, (a, b) in enumerate(zip(candidate_bytes, original_bytes))
                     if a != b),
                    min(len(candidate_bytes), len(original_bytes)),
                )
                raise RevisionSeedError(
                    f"{slug}: current workbook/builder does not reproduce published "
                    f"ICS byte-for-byte (first differing byte {mismatch_at}; "
                    f"rebuilt {len(candidate_bytes)} bytes, "
                    f"published {len(original_bytes)} bytes)."
                )
            views[slug] = view_matches
            event_count += len(matches)

    candidate = {
        "version": 2,
        "matches": legacy["matches"],  # Keep every legacy record unchanged.
        "views": views,
    }
    conflicting = sum(
        len(set(versions)) > 1 for versions in all_versions.values()
    )
    report = {
        "legacy_matches_preserved": len(legacy["matches"]),
        "team_feeds_verified": len(views),
        "per_calendar_view_records": event_count,
        "unique_published_matches": len(all_versions),
        "shared_match_revision_conflicts": conflicting,
        "published_ics_byte_identical": True,
        "repository_files_written": 0,
    }
    return candidate, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", required=True,
        help="CJSL manifest path beneath config/",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        _, report = preview_revision_seed(args.root, args.manifest)
    except (RevisionSeedError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Revision migration PREVIEW FAILED: {exc}\n")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("CJSL per-calendar revision migration preview passed.")
        for key, value in report.items():
            print(f"  {key}: {value}")
        print("No repository files changed. This command has no --apply option.")


if __name__ == "__main__":
    main()
