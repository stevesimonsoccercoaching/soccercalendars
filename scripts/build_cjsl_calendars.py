    print(
        f"Created {output_file} with {len(matches)} matches."
    )


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage:\n"
            "python scripts/build_cjsl_calendars.py "
            "config/YEAR/cjsl/SEASON/manifest.json"
        )

    manifest_file = Path(sys.argv[1])

    if not manifest_file.exists():
        raise SystemExit(
            f"Manifest not found: {manifest_file}"
        )

    manifest = json.loads(
        manifest_file.read_text(encoding="utf-8")
    )

    competition = manifest["competition"]
    defaults = manifest.get("calendar_defaults", {})
    display_names = manifest.get("display_names", {})
    teams = manifest.get("teams", [])

    if not teams:
        raise SystemExit(
            "No teams are configured in the manifest."
        )

    parts = manifest_file.parts

    try:
        config_index = parts.index("config")
    except ValueError:
        raise SystemExit(
            "Manifest must be stored beneath config/."
        )

    relative_folder = Path(
        *parts[config_index + 1:-1]
    )

    data_folder = Path("data") / relative_folder
    docs_folder = Path("docs") / relative_folder

    revision_state_file = (
        Path("state")
        / relative_folder
        / "revisions.json"
    )

    revision_state = load_revision_state(
        revision_state_file
    )

    changed_at = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    default_duration = int(
        competition.get(
            "default_duration_minutes",
            60
        )
    )

    print(
        f"Building {len(teams)} calendar(s) for "
        f"{competition['name']}..."
    )

    for team in teams:
        team_name = team["name"]
        slug = team["slug"]

        input_file = data_folder / f"{slug}.xlsx"
        output_file = docs_folder / f"{slug}.ics"

        if not input_file.exists():
            raise FileNotFoundError(
                f"Missing XLSX for {team_name}:\n"
                f"{input_file}"
            )

        duration_minutes = int(
            team.get(
                "duration_minutes",
                default_duration
            )
        )

        matches = read_matches(
            input_file=input_file,
            tracked_team_name=team_name,
            duration_minutes=duration_minutes,
        )

        build_calendar(
            competition=competition,
            defaults=defaults,
            display_names=display_names,
            team=team,
            matches=matches,
            output_file=output_file,
            revision_state=revision_state,
            changed_at=changed_at,
        )

    state_changed = save_revision_state(
        revision_state_file,
        revision_state,
    )

    if state_changed:
        print(
            f"Updated revision state: {revision_state_file}"
        )
    else:
        print(
            f"Revision state unchanged: {revision_state_file}"
        )

    print(
