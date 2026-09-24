from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import json
import re
import sys

from openpyxl import load_workbook


REQUIRED_COLUMNS = {
    "Match #" ,
    "Date" ,
    "Time" ,
    "Home Team" ,
    "Away Team" ,
    "Location" ,
    "Division" ,
    "Status" ,
}


# ------------------------------------------------------------
# Basic helpers
# ------------------------------------------------------------

def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def ics_escape(value):
    """
    Escape text used in an iCalendar text property.
    """

    value = clean_text(value)

    return (
        value
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_line(line, limit=75):
    """
    RFC 5545 content lines should not exceed 75 octets.
    Continuation lines start with a space.
    """

    if len(line.encode("utf-8")) <= limit:
        return [line]

    pieces = []
    remaining = line
    first = True

    while remaining:
        available_bytes = limit if first else limit - 1

        piece = ""
        used_bytes = 0

        for character in remaining:
            character_bytes = len(
                character.encode("utf-8")
            )

            if used_bytes + character_bytes > available_bytes:
                break

            piece += character
            used_bytes += character_bytes

        if not piece:
            piece = remaining[0]

        if first:
            pieces.append(piece)
        else:
            pieces.append(" " + piece)

        remaining = remaining[len(piece):]
        first = False

    return pieces


# ------------------------------------------------------------
# GotSport XLSX parsing
# ------------------------------------------------------------

def parse_match_start(date_value, time_value):
    """
    Handles CJSL's current GotSport XLSX format:

        Saturday, September 26, 2026
        18:40 EDT

    Also tolerates true Excel date/time values.
    """

    # Date
    if isinstance(date_value, datetime):
        match_date = date_value.date()

    elif isinstance(date_value, date):
        match_date = date_value

    else:
        date_text = clean_text(date_value)

        # GotSport may use two spaces before
        # a single-digit day.
        date_text = re.sub(
            r"\s+",
            " ",
            date_text
        )

        match_date = datetime.strptime(
            date_text,
            "%A, %B %d, %Y"
        ).date()

    # Time
    if isinstance(time_value, datetime):
        match_time = time_value.time()

    elif isinstance(time_value, time):
        match_time = time_value

    else:
        time_text = clean_text(time_value)

        # Example:
        # 18:40 EDT -> 18:40
        clock_text = time_text.split()[0]

        match_time = datetime.strptime(
            clock_text,
            "%H:%M"
        ).time()

    return datetime.combine(
        match_date,
        match_time
    )


def read_matches(
    input_file,
    tracked_team_name,
    duration_minutes
):
    workbook = load_workbook(
        input_file,
        read_only=True,
        data_only=True
    )

    sheet = workbook.active

    header_row = next(
        sheet.iter_rows(values_only=True)
    )

    headers = {
        clean_text(value): index
        for index, value in enumerate(header_row)
        if value is not None
    }

    missing = (
        REQUIRED_COLUMNS
        - set(headers)
    )

    if missing:
        raise RuntimeError(
            f"{input_file} is missing required "
            f"columns: {sorted(missing)}"
        )

    matches = []

    for row in sheet.iter_rows(
        min_row=2,
        values_only=True
    ):
        match_no = row[
            headers["Match #"]
        ]

        if match_no is None:
            continue

        home_team = clean_text(
            row[headers["Home Team"]]
        )

        away_team = clean_text(
            row[headers["Away Team"]]
        )

        if (
            home_team != tracked_team_name
            and away_team != tracked_team_name
        ):
            raise RuntimeError(
                f"Match {match_no} does not "
                f"contain configured team "
                f"{tracked_team_name!r}"
            )

        start = parse_match_start(
            row[headers["Date"]],
            row[headers["Time"]]
        )

        end = start + timedelta(
            minutes=duration_minutes
        )

        matches.append(
            {
                "match_no": int(match_no),
                "start": start,
                "end": end,
                "home_team": home_team,
                "away_team": away_team,
                "location": clean_text(
                    row[headers["Location"]]
                ),
                "division": clean_text(
                    row[headers["Division"]]
                ),
                "status": clean_text(
                    row[headers["Status"]]
                ),
            }
        )

    workbook.close()

    matches.sort(
        key=lambda match: (
            match["start"],
            match["match_no"]
        )
    )

    return matches


# ------------------------------------------------------------
# Calendar generation
# ------------------------------------------------------------

def build_calendar(
    competition,
    defaults,
    display_names,
    team,
    matches,
    output_file
):
    organizer = competition["organizer"]
    event_id = str(
        competition["event_id"]
    )

    calendar_timezone = competition[
        "timezone"
    ]

    team_name = team["name"]

    short_name = team.get(
        "short_name",
        display_names.get(
            team_name,
            team_name
        )
    )

    gotsport = team["gotsport"]

    source_url = gotsport[
        "schedule_url"
    ]

    reminders = defaults.get(
        "reminders",
        []
    )

    generated_at = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    calendar_name = (
        f"{organizer} - {team_name}"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        (
            "PRODID:-//Soccer Calendars//"
            "CJSL GotSport Converter//EN"
        ),
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",

        (
            "X-WR-CALNAME:"
            f"{ics_escape(calendar_name)}"
        ),

        (
            "X-WR-TIMEZONE:"
            f"{calendar_timezone}"
        ),

        "BEGIN:VTIMEZONE",
        f"TZID:{calendar_timezone}",

        (
            "X-LIC-LOCATION:"
            f"{calendar_timezone}"
        ),

        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "TZNAME:EDT",
        "DTSTART:19700308T020000",

        (
            "RRULE:FREQ=YEARLY;"
            "BYMONTH=3;BYDAY=2SU"
        ),

        "END:DAYLIGHT",

        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0400",
        "TZOFFSETTO:-0500",
        "TZNAME:EST",
        "DTSTART:19701101T020000",

        (
            "RRULE:FREQ=YEARLY;"
            "BYMONTH=11;BYDAY=1SU"
        ),

        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for match in matches:
        home_team = match[
            "home_team"
        ]

        away_team = match[
            "away_team"
        ]

        if home_team == team_name:
            home_away = "HOME"
            opponent_full = away_team
            separator = "vs"

        else:
            home_away = "AWAY"
            opponent_full = home_team
            separator = "@"

        opponent_short = display_names.get(
            opponent_full,
            opponent_full
        )

        summary = (
            f"{organizer}: "
            f"{short_name} "
            f"{separator} "
            f"{opponent_short}"
        )

        description = (
            f"Match No: "
            f"{match['match_no']} "
            f"({home_away})"
            "\\n\\n"
            f"HOME: "
            f"{ics_escape(home_team)}"
            "\\n"
            f"AWAY: "
            f"{ics_escape(away_team)}"
            "\\n\\n"
            f"Division: "
            f"{ics_escape(match['division'])}"
            "\\n"
            f"Source: "
            f"{source_url}"
        )

        # Stable UID:
        # same GotSport match keeps same UID even
        # if date, time or field later changes.
        uid = (
            f"{organizer.lower()}-"
            f"{event_id}-"
            f"match-"
            f"{match['match_no']}"
            "@system.gotsport.com"
        )

        lines.extend(
            [
                "BEGIN:VEVENT",

                f"UID:{uid}",

                f"DTSTAMP:{generated_at}",

                (
                    "DTSTART;TZID="
                    f"{calendar_timezone}:"
                    f"{match['start'].strftime('%Y%m%dT%H%M%S')}"
                ),

                (
                    "DTEND;TZID="
                    f"{calendar_timezone}:"
                    f"{match['end'].strftime('%Y%m%dT%H%M%S')}"
                ),

                (
                    "SUMMARY:"
                    f"{ics_escape(summary)}"
                ),

                (
                    "LOCATION:"
                    f"{ics_escape(match['location'])}"
                ),

                (
                    "DESCRIPTION:"
                    f"{description}"
                ),

                f"URL:{source_url}",

                (
                    "CATEGORIES:"
                    f"{organizer},Soccer"
                ),

                "TRANSP:OPAQUE",
            ]
        )

        status_lower = (
            match["status"].lower()
        )

        if "cancel" in status_lower:
            lines.append(
                "STATUS:CANCELLED"
            )
        else:
            lines.append(
                "STATUS:CONFIRMED"
            )

        if match["status"]:
            lines.append(
                "X-GOTSPORT-STATUS:"
                f"{ics_escape(match['status'])}"
            )

        for reminder in reminders:
            lines.extend(
                [
                    "BEGIN:VALARM",

                    (
                        "TRIGGER:"
                        f"{reminder['trigger']}"
                    ),

                    "ACTION:DISPLAY",

                    (
                        "DESCRIPTION:"
                        f"{ics_escape(reminder['description'])}"
                    ),

                    "END:VALARM",
                ]
            )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    folded_lines = []

    for line in lines:
        folded_lines.extend(
            fold_line(line)
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file.write_text(
        "\r\n".join(folded_lines)
        + "\r\n",
        encoding="utf-8",
        newline=""
    )

    print(
        f"Created {output_file} "
        f"with {len(matches)} matches."
    )


# ------------------------------------------------------------
# Competition manifest processing
# ------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage:\n"
            "python "
            "scripts/build_cjsl_calendars.py "
            "config/YEAR/cjsl/"
            "SEASON/manifest.json"
        )

    manifest_file = Path(
        sys.argv[1]
    )

    if not manifest_file.exists():
        raise SystemExit(
            f"Manifest not found: "
            f"{manifest_file}"
        )

    manifest = json.loads(
        manifest_file.read_text(
            encoding="utf-8"
        )
    )

    competition = manifest[
        "competition"
    ]

    defaults = manifest.get(
        "calendar_defaults",
        {}
    )

    display_names = manifest.get(
        "display_names",
        {}
    )

    teams = manifest.get(
        "teams",
        []
    )

    if not teams:
        raise SystemExit(
            "No teams are configured "
            "in the manifest."
        )

    # Example manifest:
    #
    # config/2026/cjsl/fall/manifest.json
    #
    # becomes:
    #
    # data/2026/cjsl/fall/
    # docs/2026/cjsl/fall/

    parts = manifest_file.parts

    try:
        config_index = parts.index(
            "config"
        )

    except ValueError:
        raise SystemExit(
            "Manifest must be stored "
            "beneath config/."
        )

    relative_folder = Path(
        *parts[
            config_index + 1:-1
        ]
    )

    data_folder = (
        Path("data")
        / relative_folder
    )

    docs_folder = (
        Path("docs")
        / relative_folder
    )

    default_duration = int(
        competition.get(
            "default_duration_minutes",
            60
        )
    )

    print(
        f"Building {len(teams)} "
        f"calendar(s) for "
        f"{competition['name']}..."
    )

    for team in teams:
        team_name = team["name"]
        slug = team["slug"]

        input_file = (
            data_folder
            / f"{slug}.xlsx"
        )

        output_file = (
            docs_folder
            / f"{slug}.ics"
        )

        if not input_file.exists():
            raise FileNotFoundError(
                f"Missing XLSX for "
                f"{team_name}:\n"
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
        )

    print(
        "All configured CJSL calendars "
        "built successfully."
    )


if __name__ == "__main__":
    main()
