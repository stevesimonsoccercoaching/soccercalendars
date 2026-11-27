"""Read-only checks for a soccer manifest and its XLSX file inventory."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from soccer_paths import resolve_event_paths


def is_valid_team_slug(slug):
    return (
        isinstance(slug, str)
        and re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", slug
        ) is not None
    )


def validate_team_identities(teams):
    """Validate identities required for unambiguous team processing."""
    if not isinstance(teams, list) or not teams:
        return ["teams must be a nonempty list"]

    errors = []
    names = set()
    slugs = set()

    for number, team in enumerate(teams, 1):
        prefix = f"teams[{number}]"

        if not isinstance(team, dict):
            errors.append(f"{prefix} must be an object")
            continue

        name, slug = team.get("name"), team.get("slug")

        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix}.name is required")
        elif name.casefold() in names:
            errors.append(f"Duplicate team name: {name}")
        else:
            names.add(name.casefold())

        if not is_valid_team_slug(slug):
            errors.append(
                f"{prefix}.slug must be lowercase, "
                "alphanumeric and hyphenated"
            )
            continue

        if slug in slugs:
            errors.append(f"Duplicate slug: {slug}")

        slugs.add(slug)

    return errors


def validate(manifest_path):
    errors = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Cannot read manifest: {exc}"], None
    if not isinstance(manifest, dict):
        return ["Manifest must be a JSON object"], None
    competition = manifest.get("competition")
    teams = manifest.get("teams")
    if not isinstance(competition, dict):
        return ["competition must be an object"], None
    if not isinstance(teams, list) or not teams:
        return ["teams must be a nonempty list"], None
    event_id = str(competition.get("event_id", "")).strip()
    if not event_id.isdecimal():
        errors.append("competition.event_id must be numeric")

    try:
        event_paths = resolve_event_paths(manifest_path, competition)
    except ValueError as exc:
        return [str(exc)], None

    data_dir = event_paths.data_dir

    if not data_dir.is_dir():
        return [f"Missing data directory: {data_dir}"], None

    errors.extend(validate_team_identities(teams))

    ids = set()
    expected = set()
    for number, team in enumerate(teams, 1):
        prefix = f"teams[{number}]"
        if not isinstance(team, dict):
            continue

        slug = team.get("slug")
        if not is_valid_team_slug(slug):
            continue

        expected.add(f"{slug}.xlsx")
        if "short_name" in team and (not isinstance(team["short_name"], str) or not team["short_name"].strip()):
            errors.append(f"{prefix}.short_name must be nonempty when supplied")
        got = team.get("gotsport")
        if not isinstance(got, dict):
            errors.append(f"{prefix}.gotsport must be an object")
            continue
        schedule_id = str(got.get("schedule_team_id", "")).strip()
        export_id = str(got.get("export_team_id", "")).strip()
        if not schedule_id.isdecimal() or not export_id.isdecimal():
            errors.append(f"{prefix}: GotSport team IDs must be numeric")
        if schedule_id.isdecimal():
            if schedule_id in ids:
                errors.append(f"Duplicate GotSport team ID: {schedule_id}")
            ids.add(schedule_id)
        # Do not require the two IDs to be equal: GotSport may distinguish them.
        for field, endpoint, team_id in (
            ("schedule_url", "schedules", schedule_id),
            ("export_url", "matches_export", export_id),
        ):
            value = got.get(field)
            if not isinstance(value, str):
                errors.append(f"{prefix}.gotsport.{field} is required")
                continue
            url = urlparse(value)
            valid_path = url.path == f"/org_event/events/{event_id}/{endpoint}"
            if field == "schedule_url":
                # Some CJSL GotSport schedule links use a numeric splash/org ID.
                # Only schedule pages may use this format; exports remain /org_event/.
                valid_path = valid_path or bool(re.fullmatch(
                    rf"/splash/[0-9]+/events/{re.escape(event_id)}/schedules",
                    url.path,
                ))
            if (url.scheme != "https" or url.netloc != "system.gotsport.com"
                    or not valid_path
                    or parse_qs(url.query).get("team") != [team_id]):
                errors.append(f"{prefix}.gotsport.{field}: check event ID, team ID and URL")

    actual = {p.name for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".xlsx"}
    for filename in sorted(expected - actual):
        errors.append(f"Registered team missing XLSX: {data_dir / filename}")
    for filename in sorted(actual - expected):
        errors.append(f"Unregistered XLSX (add team to manifest): {data_dir / filename}")
    return errors, (len(teams), len(actual))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    errors, summary = validate(args.manifest)
    if errors:
        print("Manifest validation FAILED:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    print(f"Manifest validation passed: {summary[0]} teams, {summary[1]} XLSX files")
    print("Read-only validation: no files modified")


if __name__ == "__main__":
    main()
