"""Read-only build-profile planning for registered soccer events.

From the repository root:
    python scripts/plan_soccer_builds.py
    python scripts/plan_soccer_builds.py --json

This tool does not validate XLSX contents, execute builders, or write files.
Production orchestration will consume its explicitly selected profiles later.
"""

import argparse
import json
from pathlib import Path

from discover_soccer_manifests import discover

# Profiles describe existing, separately tested behavior. A new competition
# must explicitly choose one and prove compatibility, or implement a new one.
PROFILES = {
    "explicit-status-cjsl-v1": {
        "builder": "scripts/build_cjsl_calendars.py",
        "preserve_missing": False,
    },
    "metadata-warning-v1": {
        "builder": "scripts/build_soccer_calendars.py",
        "preserve_missing": True,
    },
}

# Temporary, exact-path compatibility for the two pre-migration events only.
# All newly structured events must declare competition.calendar_profile.
LEGACY_PROFILES = {
    "config/2026/cjsl/fall/manifest.json": "explicit-status-cjsl-v1",
    "config/2026-27/edp-north-atlantic/manifest.json": "metadata-warning-v1",
}


def plan(repo_root):
    """Return (planned_events, errors, warnings), without modifying files."""
    root = Path(repo_root)
    records, errors, warnings = discover(root)
    if errors:
        return [], errors, warnings

    events = []
    for record in records:
        path = record["manifest"]
        manifest = json.loads((root / path).read_text(encoding="utf-8"))
        competition = manifest["competition"]
        requested = competition.get("calendar_profile")

        if requested is None:
            if record["layout"] == "legacy" and path in LEGACY_PROFILES:
                profile = LEGACY_PROFILES[path]
                warnings.append(f"{path}: using temporary legacy profile {profile!r}")
            else:
                errors.append(f"{path}: competition.calendar_profile is required")
                continue
        elif not isinstance(requested, str) or not requested.strip():
            errors.append(f"{path}: competition.calendar_profile must be a nonempty string")
            continue
        else:
            profile = requested.strip()

        spec = PROFILES.get(profile)
        if spec is None:
            errors.append(f"{path}: unsupported calendar_profile {profile!r}")
            continue

        if record["provider"] != "gotsport":
            errors.append(f"{path}: calendar_profile {profile!r} currently requires GotSport")
            continue

        status_mode = str(competition.get("status_mode", "")).strip().lower()
        missing_policy = str(competition.get("missing_match_policy", "")).strip().lower()
        if spec["preserve_missing"]:
            if status_mode != "metadata" or missing_policy != "preserve-warning":
                errors.append(
                    f"{path}: profile {profile!r} requires "
                    "status_mode='metadata' and missing_match_policy='preserve-warning'"
                )
                continue
            safety = competition.get("schedule_safety")
            if not isinstance(safety, dict) or safety.get("reject_empty_export") is not True:
                errors.append(f"{path}: metadata-warning profile requires reject_empty_export=true")
                continue
            fraction = safety.get("max_missing_fraction")
            if (isinstance(fraction, bool) or not isinstance(fraction, (int, float))
                    or not 0 <= fraction <= 1):
                errors.append(f"{path}: metadata-warning profile requires max_missing_fraction in [0,1]")
                continue
        elif status_mode == "metadata" or missing_policy == "preserve-warning":
            errors.append(f"{path}: explicit-status profile conflicts with metadata/missing-warning policy")
            continue

        events.append({
            **record,
            "calendar_profile": profile,
            "builder": spec["builder"],
            "missing_match_sync": spec["preserve_missing"],
        })

    if errors:
        # Never supply a partially usable build plan on invalid inventory.
        return [], errors, warnings
    return events, [], warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root")
    parser.add_argument("--json", action="store_true", help="Machine-readable plan")
    args = parser.parse_args()

    events, errors, warnings = plan(args.root)
    if args.json:
        print(json.dumps({"events": events, "errors": errors, "warnings": warnings}, indent=2))
    else:
        for event in events:
            print(f"{event['name']}: {event['teams']} teams, "
                  f"{event['calendar_profile']} -> {event['builder']}")
        print(f"Plan: {len(events)} events, {sum(e['teams'] for e in events)} teams")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
