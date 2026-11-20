from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from soccer_paths import resolve_event_paths

from schedule_safety import (
    ScheduleSafetyError,
    validate_schedule_transition,
)

REQUIRED_COLUMNS = {
    "Match #",
    "Date",
    "Time",
    "Home Team",
    "Away Team",
    "Location",
    "Division",
    "Status",
}

def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def run_git(args, *, check=True):
    result = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed:\n"
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )
    return result.stdout.decode("utf-8", errors="replace")

def commit_exists(ref):
    if not ref or set(ref) == {"0"}:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0

def resolve_base(base, head):
    if commit_exists(base):
        return base
    parent = run_git(["rev-parse", f"{head}^"], check=False).strip()
    if commit_exists(parent):
        return parent
    return ""

def git_file_bytes(ref, path):
    if not ref:
        return None
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None

def normalize_match_no(value):
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = clean_text(value)
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text

def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    for fmt in (
        "%A, %B %d, %Y",
        "%B %d, %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unusable match date: {value!r}")

def time_is_tbd(value):
    if isinstance(value, (datetime, time)):
        return False
    return clean_text(value).upper() in {
        "", "-", "TBD", "TBA", "TO BE DETERMINED", "TO BE ANNOUNCED"
    }

def parse_time(value):
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = clean_text(value)
    normalized = re.sub(r"\s+(EDT|EST|ET)$", "", text, flags=re.I).strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Unusable match time: {value!r}")

def read_schedule(workbook_bytes, duration_minutes=60):
    workbook = load_workbook(
        BytesIO(workbook_bytes),
        read_only=True,
        data_only=True,
    )
    sheet = workbook.active
    header = next(sheet.iter_rows(values_only=True), None)
    if not header:
        workbook.close()
        raise RuntimeError("Spreadsheet has no header row")

    headers = {
        clean_text(value): index
        for index, value in enumerate(header)
        if value is not None
    }
    missing = REQUIRED_COLUMNS - set(headers)
    if missing:
        workbook.close()
        raise RuntimeError(
            f"Spreadsheet is missing required columns: {sorted(missing)}"
        )

    matches = {}

    for row in sheet.iter_rows(min_row=2, values_only=True):
        match_no = normalize_match_no(row[headers["Match #"]])
        if not match_no:
            continue

        if match_no in matches:
            workbook.close()
            raise RuntimeError(
                f"Spreadsheet contains duplicate Match # {match_no}"
            )

        match_date = parse_date(row[headers["Date"]])
        raw_time = row[headers["Time"]]
        all_day = time_is_tbd(raw_time)

        if all_day:
            start = datetime.combine(match_date, time.min)
            end = start + timedelta(days=1)
        else:
            start = datetime.combine(match_date, parse_time(raw_time))
            end = start + timedelta(minutes=duration_minutes)

        result = ""
        if "Results" in headers:
            result = clean_text(row[headers["Results"]])
            if result == "-":
                result = ""

        matches[match_no] = {
            "match_no": int(match_no) if match_no.isdigit() else match_no,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "all_day": all_day,
            "home_team": clean_text(row[headers["Home Team"]]),
            "away_team": clean_text(row[headers["Away Team"]]),
            "result": result,
            "location": clean_text(row[headers["Location"]]),
            "division": clean_text(row[headers["Division"]]),
            "status": clean_text(row[headers["Status"]]) or "Unknown",
        }

    workbook.close()
    return matches

def load_state(path):
    if not path.exists():
        return {"version": 1, "teams": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("teams", {})
    return data

def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        print("Missing-match state unchanged.")
        return False
    path.write_text(content, encoding="utf-8")
    print(f"Updated missing-match state: {path}")
    return True

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Preserve last-known GotSport matches that disappear from a "
            "metadata-status competition export."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    competition = manifest.get("competition", {})

    if clean_text(competition.get("missing_match_policy")).lower() != "preserve-warning":
        print("Missing-match preservation is not enabled for this competition.")
        return

    head = run_git(["rev-parse", args.head]).strip()
    base = resolve_base(args.base, head)
    if not base:
        print("No usable previous commit; nothing to compare for missing matches.")
        return


    repo_root = Path.cwd().resolve()
    event_paths = resolve_event_paths(
        manifest_path, competition, repo_root=repo_root
    )

    data_folder = event_paths.data_dir
    state_file = event_paths.state_dir / "missing_matches.json"


    state = load_state(state_file)
    teams_state = state.setdefault("teams", {})
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    default_duration = int(competition.get("default_duration_minutes", 60))
    schedule_safety = competition.get("schedule_safety", {})
    newly_missing = 0
    reappeared = 0

    for team in manifest.get("teams", []):
        slug = clean_text(team.get("slug"))
        if not slug:
            continue


        path = (
            (data_folder / f"{slug}.xlsx")
            .relative_to(repo_root)
            .as_posix()
        )


        if not old_bytes or not new_bytes:
            continue

        duration = int(team.get("duration_minutes", default_duration))
        old_matches = read_schedule(old_bytes, duration)
        new_matches = read_schedule(new_bytes, duration)

        try:
            transition = validate_schedule_transition(
                old_matches,
                new_matches,
                schedule_safety,
                label=slug,
            )
        except (ScheduleSafetyError, ValueError) as exc:
            raise RuntimeError(
                f"Schedule safety check failed for {path}:\n{exc}"
            ) from exc

        old_ids = set(old_matches)
        new_ids = set(new_matches)

        print(
            f"{slug}: safety check passed — "
            f"{transition['old_count']} previous, "
            f"{transition['new_count']} current, "
            f"{transition['missing_count']} missing "
            f"({transition['missing_fraction']:.1%})."
        )

        team_state = teams_state.setdefault(slug, {})

        for match_no in list(team_state):
            if match_no in new_ids:
                del team_state[match_no]
                reappeared += 1
                print(f"{slug}: Match {match_no} reappeared; cleared warning.")

        for match_no in sorted(old_ids - new_ids):
            if match_no not in team_state:
                team_state[match_no] = {
                    "first_missing_at": now,
                    "match": old_matches[match_no],
                }
                newly_missing += 1
                print(f"{slug}: Match {match_no} is missing; preserving last-known event.")

        if not team_state:
            teams_state.pop(slug, None)

    save_state(state_file, state)
    print(
        f"Newly missing: {newly_missing}; "
        f"reappeared: {reappeared}; "
        f"currently preserved: "
        f"{sum(len(v) for v in teams_state.values())}"
    )

if __name__ == "__main__":
    main()
