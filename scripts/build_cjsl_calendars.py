from contextlib import ExitStack
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile
import hashlib
import json
import re
import sys
import zlib

from openpyxl import load_workbook
from openpyxl.descriptors.base import Set
from openpyxl.reader.excel import ExcelReader
from openpyxl.worksheet._reader import _cast_number
from soccer_paths import resolve_event_paths
from validate_soccer_manifest import validate_team_identities


WORKBOOK_READ_ERRORS = (OSError, BadZipFile, ParseError, zlib.error)

try:
    from lxml.etree import XMLSyntaxError
except ImportError:
    pass
else:
    WORKBOOK_READ_ERRORS += (XMLSyntaxError,)


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
    return str(value).strip()


def match_fingerprint(match, summary):
    payload = {
        "summary": clean_text(summary),
        "start": match["start"].isoformat(),
        "end": match["end"].isoformat(),
        "all_day": bool(match["all_day"]),
        "home_team": match["home_team"],
        "away_team": match["away_team"],
        "result": match["result"],
        "location": match["location"],
        "division": match["division"],
        "status": match["status"],
    }

    # Calendar presentation rules are included in the fingerprint so a change
    # to the published event itself (not just GotSport data) gets a one-time
    # revision bump and is more likely to refresh cleanly in subscribed clients.
    payload["calendar_rendering"] = "vs-updated-visible-cancel-v1"

    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(serialized).hexdigest()


def load_revision_state(path):
    if not path.exists():
        return {
            "version": 1,
            "matches": {},
        }

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid revision state file: {path}"
        )

    data.setdefault("version", 1)
    data.setdefault("matches", {})

    if not isinstance(data["matches"], dict):
        raise RuntimeError(
            f"Invalid revision state matches object: {path}"
        )

    return data


def get_match_revision(
    state,
    event_id,
    match,
    summary,
    changed_at,
    calendar_slug,
):
    key = f"{event_id}:{match['match_no']}"
    fingerprint = match_fingerprint(match, summary)

    version = state.get("version", 1)

    if version == 2:
        views = state.get("views")

        if not isinstance(views, dict) or calendar_slug not in views:
            raise RuntimeError(
                f"Missing seeded CJSL revision view: {calendar_slug}"
            )

        bucket = views[calendar_slug]

        if not isinstance(bucket, dict):
            raise RuntimeError(
                f"Invalid CJSL revision view: {calendar_slug}"
            )

    elif version == 1:
        bucket = state["matches"]

    else:
        raise RuntimeError(
            f"Unsupported CJSL revision version: {version}"
        )

    previous = bucket.get(key)

    if previous and previous.get("fingerprint") == fingerprint:
        sequence = int(previous.get("sequence", 1))
        last_modified = (
            clean_text(previous.get("last_modified"))
            or changed_at
        )
    else:
        if previous:
            sequence = int(previous.get("sequence", 0)) + 1
        else:
            sequence = 1

        last_modified = changed_at

    bucket[key] = {
        "fingerprint": fingerprint,
        "sequence": sequence,
        "last_modified": last_modified,
    }

    return {
        "sequence": sequence,
        "last_modified": last_modified,
    }


def save_revision_state(path, state):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    content = (
        json.dumps(
            state,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    if (
        path.exists()
        and path.read_text(encoding="utf-8") == content
    ):
        return False

    path.write_text(
        content,
        encoding="utf-8",
    )

    return True


def ics_escape(value):
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
            character_bytes = len(character.encode("utf-8"))
            if used_bytes + character_bytes > available_bytes:
                break
            piece += character
            used_bytes += character_bytes

        if not piece:
            piece = remaining[0]

        pieces.append(piece if first else " " + piece)
        remaining = remaining[len(piece):]
        first = False

    return pieces


def parse_match_date(date_value):
    if isinstance(date_value, datetime):
        return date_value.date()

    if isinstance(date_value, date):
        return date_value

    date_text = clean_text(date_value)

    if not date_text:
        raise ValueError("Match date is blank")

    date_text = re.sub(r"\s+", " ", date_text)

    return datetime.strptime(
        date_text,
        "%A, %B %d, %Y"
    ).date()


def parse_match_start(date_value, time_value):
    match_date = parse_match_date(date_value)

    if isinstance(time_value, datetime):
        match_time = time_value.time()
    elif isinstance(time_value, time):
        match_time = time_value
    else:
        time_text = clean_text(time_value)

        if not time_text:
            raise ValueError("Match time is blank")

        clock_text = time_text.split()[0]
        match_time = datetime.strptime(
            clock_text,
            "%H:%M"
        ).time()

    return datetime.combine(match_date, match_time)


def time_is_tbd(time_value):
    if isinstance(time_value, (datetime, time)):
        return False

    time_text = clean_text(time_value).upper()

    return time_text in {
        "",
        "-",
        "TBD",
        "TBA",
        "TO BE DETERMINED",
        "TO BE ANNOUNCED",
    }


def parse_match_number(value):
    """Return a positive integral match identity without truncation."""
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid match ID")

    if isinstance(value, int):
        match_no = value
    elif isinstance(value, float) and value.is_integer():
        match_no = int(value)
    elif isinstance(value, str) and re.fullmatch(
        r"[0-9]+", value.strip()
    ):
        match_no = int(value.strip())
    else:
        raise ValueError("Match ID must be a positive integer")

    if match_no <= 0:
        raise ValueError("Match ID must be greater than zero")

    return match_no


class CJSLInputError(RuntimeError):
    def __init__(self, kind, message, match_ids=()):
        super().__init__(message)
        self.kind = kind
        self.severity = "error"
        self.team_slugs = ()
        self.match_ids = tuple(match_ids)
        self.message = message


class CJSLInputErrors(RuntimeError):
    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__(
            "CJSL input validation failed:\n"
            + "\n".join(
                f"{problem.severity.upper()}: {problem}"
                for problem in self.problems
            )
        )


def exception_origin(error):
    """Return the code object where an exception originated."""
    origin = error.__traceback__

    if origin is None:
        return None

    while origin.tb_next is not None:
        origin = origin.tb_next

    return origin.tb_frame.f_code


def read_matches(input_file, tracked_team_name, duration_minutes):
    problems = []
    matches = []
    seen_match_numbers = set()

    def add_problem(kind, message, match_ids=(), cause=None):
        problem = CJSLInputError(kind, message, match_ids)

        if cause is not None:
            problem.__cause__ = cause

        problems.append(problem)

    with ExitStack() as resources:
        try:
            source = resources.enter_context(
                Path(input_file).open("rb")
            )

            workbook = load_workbook(
                source,
                read_only=True,
                data_only=True
            )
        except FileNotFoundError as exc:
            raise CJSLInputError(
                "missing_workbook",
                f"{input_file}: Missing workbook for "
                f"{tracked_team_name!r}"
            ) from exc
        except KeyError as exc:
            # Distinguish missing ZIP members from unrelated KeyErrors.
            if exception_origin(exc) is not ZipFile.getinfo.__code__:
                raise

            raise CJSLInputError(
                "missing_workbook_component",
                f"{input_file}: Missing internal XLSX component "
                f"for {tracked_team_name!r}: {exc}"
            ) from exc
        except WORKBOOK_READ_ERRORS as exc:
            raise CJSLInputError(
                "unreadable_workbook",
                f"{input_file}: Unable to open workbook for "
                f"{tracked_team_name!r}: {exc}"
            ) from exc
        except ValueError as exc:
            # Openpyxl wraps invalid workbook metadata in ValueError.
            # Recognize descriptor validation failures without
            # swallowing unrelated application or library defects.
            cause = exc.__cause__

            if (
                exception_origin(exc) is not ExcelReader.read.__code__
                or not isinstance(cause, ValueError)
                or exception_origin(cause) is not Set.__set__.__code__
            ):
                raise

            raise CJSLInputError(
                "unreadable_workbook",
                f"{input_file}: Unable to open workbook for "
                f"{tracked_team_name!r}: {exc}"
            ) from exc

        try:
            sheet = workbook.active

            if sheet is None:
                raise CJSLInputError(
                    "missing_worksheet",
                    f"{input_file}: No active worksheet for "
                    f"{tracked_team_name!r}"
                )

            header_row = next(
                sheet.iter_rows(values_only=True), None
            )

            if header_row is None:
                raise CJSLInputError(
                    "empty_worksheet",
                    f"{input_file}: Empty worksheet for "
                    f"{tracked_team_name!r}"
                )

            headers = {}
            consumed_columns = REQUIRED_COLUMNS | {"Results"}

            for index, value in enumerate(header_row):
                header = clean_text(value)

                if not header:
                    continue

                if header in headers and header in consumed_columns:
                    add_problem(
                        "duplicate_column",
                        f"{input_file}: Duplicate column {header!r} "
                        f"for {tracked_team_name!r} at columns "
                        f"{headers[header] + 1} and {index + 1}"
                    )

                    continue

                headers[header] = index

            missing = REQUIRED_COLUMNS - set(headers)

            if missing:
                add_problem(
                    "missing_columns",
                    f"{input_file} is missing required columns: {sorted(missing)}"
                )

            # Reject invalid headers before interpreting rows.
            if len(problems) == 1:
                raise problems[0]

            if problems:
                raise CJSLInputErrors(problems)

            for row_number, row in enumerate(
                sheet.iter_rows(min_row=2, values_only=True),
                start=2,
            ):
                if not any(clean_text(value) for value in row):
                    continue

                initial_problem_count = len(problems)
                raw_match_no = row[headers["Match #"]]
                match_no = None

                if not clean_text(raw_match_no):
                    add_problem(
                        "missing_match_id",
                        f"{input_file}: Row {row_number} is missing "
                        f"Match # for {tracked_team_name!r}",
                    )
                else:
                    try:
                        match_no = parse_match_number(raw_match_no)
                    except ValueError as exc:
                        add_problem(
                            "invalid_match_id",
                            f"{input_file}: Invalid Match # "
                            f"{raw_match_no!r} for "
                            f"{tracked_team_name!r}: {exc} "
                            f"(row {row_number})",
                            cause=exc,
                        )

                match_ids = (
                    (match_no,) if match_no is not None else ()
                )

                match_label = (
                    f"Match {match_no}"
                    if match_no is not None
                    else f"Row {row_number} (unknown Match #)"
                )

                if match_no is not None:
                    if match_no in seen_match_numbers:
                        add_problem(
                            "duplicate_match_id",
                            f"{input_file}: Duplicate Match # "
                            f"{match_no} for {tracked_team_name!r} "
                            f"(row {row_number})",
                            match_ids,
                        )

                    seen_match_numbers.add(match_no)

                home_team = clean_text(row[headers["Home Team"]])
                away_team = clean_text(row[headers["Away Team"]])

                if not home_team or not away_team:
                    add_problem(
                        "missing_team_identity",
                        f"{input_file}: Row {row_number}, "
                        f"{match_label} has a missing home or away "
                        f"team identity for {tracked_team_name!r}",
                        match_ids,
                    )
                elif home_team == away_team:
                    add_problem(
                        "identical_team_identity",
                        f"{input_file}: Row {row_number}, "
                        f"{match_label} has identical home and away "
                        f"team identities: {home_team!r}",
                        match_ids,
                    )
                elif (
                    home_team != tracked_team_name
                    and away_team != tracked_team_name
                ):
                    add_problem(
                        "unrelated_match",
                        f"{match_label} does not contain configured "
                        f"team {tracked_team_name!r} "
                        f"(row {row_number})",
                        match_ids,
                    )

                date_value = row[headers["Date"]]
                time_value = row[headers["Time"]]
                match_date = None

                try:
                    match_date = parse_match_date(date_value)
                except ValueError as exc:
                    add_problem(
                        "invalid_match_date",
                        f"{input_file}: {match_label} has no usable "
                        f"date (row {row_number})",
                        match_ids,
                        cause=exc,
                    )

                all_day = time_is_tbd(time_value)

                if not all_day:
                    try:
                        # A fixed valid date permits independent
                        # time validation when the date is invalid.
                        # Such a row is never accepted.
                        start = parse_match_start(
                            match_date or date(2000, 1, 1),
                            time_value,
                        )
                    except (ValueError, IndexError) as exc:
                        add_problem(
                            "invalid_match_time",
                            f"{input_file}: {match_label} has an invalid "
                            f"time value: {time_value!r} "
                            f"(row {row_number})",
                            match_ids,
                            cause=exc,
                        )

                if len(problems) != initial_problem_count:
                    continue

                if all_day:
                    start = datetime.combine(match_date, time.min)
                    end = start + timedelta(days=1)
                else:
                    end = start + timedelta(minutes=duration_minutes)

                if "Results" in headers:
                    result = clean_text(row[headers["Results"]])
                else:
                    result = ""

                raw_status = clean_text(row[headers["Status"]])
                display_status = raw_status if raw_status else "Scheduled"

                matches.append(
                    {
                        "match_no": match_no,
                        "start": start,
                        "end": end,
                        "all_day": all_day,
                        "home_team": home_team,
                        "away_team": away_team,
                        "result": result,
                        "location": clean_text(row[headers["Location"]]),
                        "division": clean_text(row[headers["Division"]]),
                        "status": display_status,
                    }
                )
        except WORKBOOK_READ_ERRORS as exc:
            add_problem(
                "unreadable_worksheet",
                f"{input_file}: Unable to read worksheet for "
                f"{tracked_team_name!r}: {exc}",
                cause=exc,
            )
        except ValueError as exc:
            # Invalid numeric cell encoding fails before row validation.
            if exception_origin(exc) is not _cast_number.__code__:
                raise

            add_problem(
                "unreadable_worksheet",
                f"{input_file}: Unable to read worksheet for "
                f"{tracked_team_name!r}: {exc}",
                cause=exc,
            )
        finally:
            workbook.close()

    if len(problems) == 1:
        raise problems[0]

    if problems:
        raise CJSLInputErrors(problems)

    if not matches:
        raise CJSLInputError(
            "empty_export",
            f"{input_file}: No matches found for "
            f"{tracked_team_name!r}"
        )

    matches.sort(
        key=lambda match: (
            match["start"],
            match["match_no"]
        )
    )

    return matches


def validate_shared_matches(teams, collected):
    """Identify material disagreements within one CJSL event."""
    registered = {
        team["name"]: team["slug"]
        for team in teams
    }

    readable_slugs = {
        team["slug"]
        for team, matches, output_file in collected
    }

    by_match = {}
    problems = []

    def normalize_whitespace(value):
        return " ".join(clean_text(value).split())

    def comparison_fields(match):
        return {
            "date": match["start"].date(),
            "time": (
                None if match["all_day"]
                else match["start"].time()
            ),
            "home_team": match["home_team"],
            "away_team": match["away_team"],
            "location": normalize_whitespace(match["location"]),
            "status": (
                normalize_whitespace(match["status"])
                or "Scheduled"
            ),
        }

    def describe_differences(comparable):
        baseline = comparable[0][1]

        details = []

        for field in baseline:
            if any(
                fields[field] != baseline[field]
                for slug, fields in comparable
            ):
                values = ", ".join(
                    f"{slug}={fields[field]!r}"
                    for slug, fields in comparable
                )

                details.append(f"{field}: {values}")

        return details

    def add_problem(
        kind, message, slugs, match_no, severity="error"
    ):
        problem = CJSLInputError(
            kind,
            message,
            match_ids=(match_no,),
        )

        problem.team_slugs = tuple(sorted(slugs))
        problem.severity = severity
        problems.append(problem)

    for team, matches, output_file in collected:
        for match in matches:
            by_match.setdefault(
                match["match_no"], []
            ).append((team["slug"], match))

    for match_no, appearances in sorted(by_match.items()):
        present_slugs = {
            slug for slug, match in appearances
        }

        expected_slugs = set()

        for slug, match in appearances:
            for name in (
                match["home_team"],
                match["away_team"],
            ):
                if name in registered:
                    expected_slugs.add(registered[name])

        comparable = [
            (slug, comparison_fields(match))
            for slug, match in appearances
        ]

        details = describe_differences(comparable)

        if details:
            add_problem(
                "shared_match_conflict",
                f"Match {match_no}: conflicting shared-match "
                f"records; {'; '.join(details)}",
                present_slugs,
                match_no,
            )

        warning_comparable = [
            (
                slug,
                {
                    "result": normalize_whitespace(match["result"]),
                    "division": normalize_whitespace(match["division"]),
                },
            )
            for slug, match in appearances
        ]

        warning_details = describe_differences(
            warning_comparable
        )

        if warning_details:
            add_problem(
                "shared_match_warning",
                f"Match {match_no}: non-blocking shared-match "
                f"disagreement; {'; '.join(warning_details)}",
                present_slugs,
                match_no,
                severity="warning",
            )

        missing_slugs = (
            expected_slugs & readable_slugs
        ) - present_slugs

        for missing_slug in sorted(missing_slugs):
            add_problem(
                "missing_shared_match",
                f"Match {match_no}: absent from validated "
                f"export for {missing_slug}; present in "
                f"{', '.join(sorted(present_slugs))}",
                present_slugs | {missing_slug},
                match_no,
            )

        incomplete_slugs = expected_slugs - readable_slugs

        for incomplete_slug in sorted(incomplete_slugs):
            add_problem(
                "incomplete_shared_match_comparison",
                f"Match {match_no}: unable to compare against "
                f"{incomplete_slug}; its export did not pass "
                f"workbook validation",
                present_slugs | {incomplete_slug},
                match_no,
                severity="warning",
            )

    return problems


def build_calendar(
    competition,
    defaults,
    display_names,
    team,
    matches,
    output_file,
    revision_state,
    changed_at,
):
    organizer = competition["organizer"]
    event_id = str(competition["event_id"])
    calendar_timezone = competition["timezone"]

    team_name = team["name"]
    short_name = team.get(
        "short_name",
        display_names.get(team_name, team_name)
    )

    source_url = team["gotsport"]["schedule_url"]
    reminders = defaults.get("reminders", [])

    calendar_name = f"{organizer} - {team_name}"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Soccer Calendars//CJSL GotSport Converter//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
        f"X-WR-TIMEZONE:{calendar_timezone}",
        "BEGIN:VTIMEZONE",
        f"TZID:{calendar_timezone}",
        f"X-LIC-LOCATION:{calendar_timezone}",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "TZNAME:EDT",
        "DTSTART:19700308T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0400",
        "TZOFFSETTO:-0500",
        "TZNAME:EST",
        "DTSTART:19701101T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for match in matches:
        home_team = match["home_team"]
        away_team = match["away_team"]

        if home_team == team_name:
            home_away = "HOME"
            opponent_full = away_team
        else:
            home_away = "AWAY"
            opponent_full = home_team

        opponent_short = display_names.get(
            opponent_full,
            opponent_full
        )

        status_lower = match["status"].lower()

        summary = (
            f"{organizer}: {short_name} "
            f"vs {opponent_short}"
        )

        if "cancel" in status_lower:
            summary = f"❌ Canceled — {summary}"

        uid = (
            f"{organizer.lower()}-{event_id}-"
            f"match-{match['match_no']}@system.gotsport.com"
        )

        revision = get_match_revision(
            state=revision_state,
            event_id=event_id,
            summary=summary,
            match=match,
            changed_at=changed_at,
            calendar_slug=team["slug"],
        )

        last_modified = revision["last_modified"]
        sequence = revision["sequence"]

        updated_utc = datetime.strptime(
            last_modified,
            "%Y%m%dT%H%M%SZ",
        ).replace(tzinfo=timezone.utc)
        updated_local = updated_utc.astimezone(
            ZoneInfo(calendar_timezone)
        )
        updated_date = updated_local.strftime(
            "%b %d, %Y"
        ).replace(" 0", " ")
        updated_time = updated_local.strftime(
            "%I:%M %p"
        ).lstrip("0")
        updated_label = (
            f"{updated_date} at {updated_time} "
            f"{updated_local.tzname()}"
        )

        description_parts = [
            f"Status: {ics_escape(match['status'])}",
            f"Match No: {match['match_no']} ({home_away})",
            f"Division: {ics_escape(match['division'])}",
            f"Updated: {ics_escape(updated_label)}",
            "",
            f"HOME: {ics_escape(home_team)}",
            f"AWAY: {ics_escape(away_team)}",
        ]

        result = clean_text(match.get("result", ""))

        if result and result != "-":
            description_parts.extend(
                [
                    "",
                    f"Results: {ics_escape(result)}",
                ]
            )

        description_parts.extend(
            [
                "",
                f"Source: {source_url}",
            ]
        )

        description = "\\n".join(description_parts)

        if match["all_day"]:
            dtstart_line = (
                "DTSTART;VALUE=DATE:"
                f"{match['start'].strftime('%Y%m%d')}"
            )
            dtend_line = (
                "DTEND;VALUE=DATE:"
                f"{match['end'].strftime('%Y%m%d')}"
            )
        else:
            dtstart_line = (
                f"DTSTART;TZID={calendar_timezone}:"
                f"{match['start'].strftime('%Y%m%dT%H%M%S')}"
            )
            dtend_line = (
                f"DTEND;TZID={calendar_timezone}:"
                f"{match['end'].strftime('%Y%m%dT%H%M%S')}"
            )

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{last_modified}",
                f"LAST-MODIFIED:{last_modified}",
                f"SEQUENCE:{sequence}",
                dtstart_line,
                dtend_line,
                f"SUMMARY:{ics_escape(summary)}",
                f"LOCATION:{ics_escape(match['location'])}",
                f"DESCRIPTION:{description}",
                f"URL:{source_url}",
                f"CATEGORIES:{organizer},Soccer",
                "TRANSP:OPAQUE",
            ]
        )

        # Keep canceled GotSport matches visible in subscribed calendars.
        # Their cancellation remains explicit in SUMMARY, DESCRIPTION, and
        # X-GOTSPORT-STATUS rather than using iCalendar STATUS:CANCELLED,
        # which some calendar clients hide or remove.
        lines.append("STATUS:CONFIRMED")

        lines.append(
            f"X-GOTSPORT-STATUS:{ics_escape(match['status'])}"
        )

        if not match["all_day"]:
            for reminder in reminders:
                lines.extend(
                    [
                        "BEGIN:VALARM",
                        f"TRIGGER:{reminder['trigger']}",
                        "ACTION:DISPLAY",
                        f"DESCRIPTION:{ics_escape(reminder['description'])}",
                        "END:VALARM",
                    ]
                )

        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    folded_lines = []

    for line in lines:
        folded_lines.extend(fold_line(line))

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file.write_text(
        "\r\n".join(folded_lines) + "\r\n",
        encoding="utf-8",
        newline=""
    )

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

    identity_errors = validate_team_identities(teams)

    if identity_errors:
        raise RuntimeError(
            "CJSL manifest identity validation failed:\n"
            + "\n".join(identity_errors)
        )

    event_paths = resolve_event_paths(manifest_file, competition)

    data_folder = event_paths.data_dir
    docs_folder = event_paths.docs_dir
    revision_state_file = event_paths.state_dir / "revisions.json"
    

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

    collected = []
    problems = []

    # Phase 1: Collect and validate every configured team export.
    for team in teams:
        team_name = team["name"]
        slug = team["slug"]

        input_file = data_folder / f"{slug}.xlsx"
        output_file = docs_folder / f"{slug}.ics"

        duration_minutes = int(
            team.get(
                "duration_minutes",
                default_duration
            )
        )

        try:
            matches = read_matches(
                input_file=input_file,
                tracked_team_name=team_name,
                duration_minutes=duration_minutes,
            )
        except CJSLInputError as exc:
            exc.team_slugs = (slug,)
            problems.append(exc)
        except CJSLInputErrors as exc:
            for problem in exc.problems:
                problem.team_slugs = (slug,)
                problems.append(problem)
        else:
            collected.append((team, matches, output_file))

    # Phase 2: Validate shared matches before accepting inputs.
    problems.extend(
        validate_shared_matches(teams, collected)
    )

    # Only explicitly recognized warnings are non-blocking.
    if any(problem.severity != "warning" for problem in problems):
        raise CJSLInputErrors(problems)

    for problem in problems:
        print(f"WARNING: {problem.message}", file=sys.stderr)

    revision_state = load_revision_state(
        revision_state_file
    )

    changed_at = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    # Phase 3: Render only after all exports pass validation.
    for team, matches, output_file in collected:
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
        "All configured CJSL calendars built successfully."
    )


if __name__ == "__main__":
    try:
        main()
    except (CJSLInputError, CJSLInputErrors) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
