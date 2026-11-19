"""Read-only inventory of soccer events registered under config/.

Usage (from the repository root):
    python scripts/discover_soccer_manifests.py
    python scripts/discover_soccer_manifests.py --json

Recognizes the two existing pre-migration layouts while requiring new events
to use config/<soccer-year>/<organizer>/<competition>/events/<event-id>/manifest.json.
This does not build calendars, read XLSX rows, or modify repository files.
"""

import argparse
import json
import re
from pathlib import Path

YEAR_PATTERN = re.compile(r"20\d{2}-\d{2}\Z")
SEGMENT_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
LEGACY_PATHS = {
    ("2026", "cjsl", "fall"): "cjsl",
    ("2026-27", "edp-north-atlantic"): "edp",
}


def discover(repo_root):
    """Return (records, errors, warnings); never write files."""
    config_root = Path(repo_root) / "config"
    records, errors, warnings = [], [], []
    if not config_root.is_dir():
        return records, ["Missing config/ directory"], warnings

    seen_events = {}
    manifests = sorted(config_root.rglob("manifest.json"))
    if not manifests:
        errors.append("No manifests found under config/")

    for manifest_path in manifests:
        rel = manifest_path.relative_to(config_root)
        event_dir = rel.parts[:-1]
        display = (Path("config") / rel).as_posix()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{display}: cannot read manifest: {exc}")
            continue
        if not isinstance(manifest, dict):
            errors.append(f"{display}: manifest must be a JSON object")
            continue

        competition = manifest.get("competition")
        teams = manifest.get("teams")
        if not isinstance(competition, dict) or not isinstance(teams, list):
            errors.append(f"{display}: competition must be an object and teams must be a list")
            continue

        event_id = str(competition.get("event_id", "")).strip()
        organizer = str(competition.get("organizer", "")).strip().lower()
        provider = str(competition.get("provider", "")).strip().lower()
        if not event_id.isascii() or not event_id.isdecimal():
            errors.append(f"{display}: competition.event_id must contain ASCII digits")
        if not organizer or not provider:
            errors.append(f"{display}: competition.organizer and competition.provider are required")
        if not teams:
            errors.append(f"{display}: teams must not be empty")

        if event_dir in LEGACY_PATHS:
            layout = "legacy"
            if organizer != LEGACY_PATHS[event_dir]:
                errors.append(f"{display}: organizer must be {LEGACY_PATHS[event_dir]!r} for this legacy path")
            warnings.append(f"{display}: legacy layout; migrate before production cutover")
        elif (len(event_dir) == 5 and event_dir[3] == "events"
              and YEAR_PATTERN.fullmatch(event_dir[0])
              and SEGMENT_PATTERN.fullmatch(event_dir[1])
              and SEGMENT_PATTERN.fullmatch(event_dir[2])
              and event_dir[4].isascii() and event_dir[4].isdecimal()):
            layout = "canonical"
            if event_dir[1] != organizer:
                errors.append(f"{display}: directory organizer {event_dir[1]!r} differs from manifest {organizer!r}")
            if event_dir[4] != event_id:
                errors.append(f"{display}: directory event ID {event_dir[4]!r} differs from manifest {event_id!r}")
        else:
            layout = "invalid"
            errors.append(f"{display}: invalid event path; expected <year>/<organizer>/<competition>/events/<event-id>/manifest.json")

        if provider and event_id.isascii() and event_id.isdecimal():
            key = (provider, event_id)
            if key in seen_events:
                errors.append(f"{display}: duplicate {provider} event {event_id}; first registered in {seen_events[key]}")
            else:
                seen_events[key] = display

        slugs = set()
        for index, team in enumerate(teams, 1):
            if not isinstance(team, dict):
                errors.append(f"{display}: teams[{index}] must be an object")
                continue
            slug = team.get("slug")
            if not isinstance(slug, str) or not SEGMENT_PATTERN.fullmatch(slug):
                errors.append(f"{display}: teams[{index}] has an invalid slug")
            elif slug in slugs:
                errors.append(f"{display}: duplicate team slug {slug!r}")
            else:
                slugs.add(slug)

        records.append({
            "manifest": display,
            "layout": layout,
            "provider": provider,
            "organizer": organizer,
            "event_id": event_id,
            "name": str(competition.get("name", "")).strip(),
            "teams": len(teams),
        })

    return records, errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root (default: current directory)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable inventory")
    args = parser.parse_args()

    records, errors, warnings = discover(args.root)
    if args.json:
        print(json.dumps({"events": records, "errors": errors, "warnings": warnings}, indent=2))
    else:
        for record in records:
            print(f"{record['provider']} / {record['organizer']} / {record['event_id']} — "
                  f"{record['teams']} teams ({record['layout']}): {record['manifest']}")
        print(f"Inventory: {len(records)} events, {sum(r['teams'] for r in records)} team registrations")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
