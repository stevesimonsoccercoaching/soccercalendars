#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


WATCHED_FIELDS = [
    ("start", "Start"),
    ("end", "End"),
    ("home_team", "Home team"),
    ("away_team", "Away team"),
    ("event", "Event"),
    ("group", "Group"),
    ("pitch_name", "Pitch"),
    ("match_status", "Status"),
    ("surface", "Surface"),
    ("venue_name", "Venue"),
    ("address", "Address"),
    ("location", "Location"),
]

DESCRIPTION_LABELS = {
    "Away team": "away_team",
    "Event": "event",
    "Group": "group",
    "Pitch name": "pitch_name",
    "Match status": "match_status",
    "Surface": "surface",
    "Venue name": "venue_name",
    "Home team": "home_team",
    "Address": "address",
}


def unfold_ics(text: str) -> List[str]:
    """Unfold RFC 5545 continuation lines."""
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: List[str] = []
    for line in raw_lines:
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def unescape_ics_text(value: str) -> str:
    # Order matters: escaped backslashes should be handled last.
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_description(value: str) -> Dict[str, str]:
    text = unescape_ics_text(value)
    lines = [line.strip() for line in text.splitlines()]
    result: Dict[str, str] = {}

    for i, line in enumerate(lines):
        key = DESCRIPTION_LABELS.get(line)
        if not key:
            continue

        j = i + 1
        while j < len(lines) and lines[j] == "":
            j += 1

        if j < len(lines):
            candidate = lines[j]
            # Do not accidentally treat the next label as a value.
            if candidate not in DESCRIPTION_LABELS:
                result[key] = candidate

    return result


def parse_properties(block: List[str]) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for line in block:
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        name = left.split(";", 1)[0].upper()
        # Keep the last instance for fields we care about.
        props[name] = value
    return props


def normalize_status(value: str) -> str:
    value = (value or "").strip()
    return value if value else "Scheduled"


def parse_feed(text: str) -> Dict[str, dict]:
    lines = unfold_ics(text)

    if "BEGIN:VCALENDAR" not in lines or "END:VCALENDAR" not in lines:
        raise RuntimeError("Downloaded content is not a complete VCALENDAR.")

    events: Dict[str, dict] = {}
    in_event = False
    block: List[str] = []

    for line in lines:
        if line == "BEGIN:VEVENT":
            in_event = True
            block = []
            continue

        if line == "END:VEVENT" and in_event:
            props = parse_properties(block)
            uid = props.get("UID", "").strip()
            if not uid:
                raise RuntimeError("A VEVENT is missing UID; cannot compare safely.")

            desc = parse_description(props.get("DESCRIPTION", ""))

            event = {
                "uid": uid,
                "start": props.get("DTSTART", "").strip(),
                "end": props.get("DTEND", "").strip(),
                "summary": unescape_ics_text(props.get("SUMMARY", "")).strip(),
                "location": unescape_ics_text(props.get("LOCATION", "")).strip(),
                "home_team": desc.get("home_team", ""),
                "away_team": desc.get("away_team", ""),
                "event": desc.get("event", ""),
                "group": desc.get("group", ""),
                "pitch_name": desc.get("pitch_name", ""),
                "match_status": normalize_status(desc.get("match_status", "")),
                "surface": desc.get("surface", ""),
                "venue_name": desc.get("venue_name", ""),
                "address": desc.get("address", ""),
                # Stored for diagnostics/context only. They do NOT trigger alerts.
                "created": props.get("CREATED", "").strip(),
                "last_modified": props.get("LAST-MODIFIED", "").strip(),
            }

            events[uid] = event
            in_event = False
            block = []
            continue

        if in_event:
            block.append(line)

    if not events:
        raise RuntimeError("VCALENDAR contained no VEVENT records.")

    return events


def fetch_calendar(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SoccerCalendars-Weave-Watcher/1.0",
            "Accept": "text/calendar,text/plain;q=0.9,*/*;q=0.1",
        },
    )

    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "")

    if not data:
        raise RuntimeError("Calendar endpoint returned an empty response.")

    text = data.decode("utf-8-sig", errors="strict")

    if "BEGIN:VCALENDAR" not in text:
        raise RuntimeError(
            f"Calendar endpoint did not return iCalendar data "
            f"(Content-Type: {content_type or 'unknown'})."
        )

    return text


def load_state(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("events", {})


def save_state(path: Path, watch_name: str, events: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    ordered_events = dict(
        sorted(
            events.items(),
            key=lambda item: (
                item[1].get("start", ""),
                item[0],
            ),
        )
    )

    payload = {
        "watch_name": watch_name,
        "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_count": len(ordered_events),
        "events": ordered_events,
    }

    path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def event_label(event: dict) -> str:
    home = event.get("home_team") or "Unknown home"
    away = event.get("away_team") or "Unknown away"
    start = event.get("start") or "Unknown time"
    return f"{home} vs {away} — {start}"


def compare_events(old: Dict[str, dict], new: Dict[str, dict]) -> Tuple[List[dict], List[dict], List[dict]]:
    added = []
    removed = []
    changed = []

    for uid in sorted(new.keys() - old.keys()):
        added.append(new[uid])

    for uid in sorted(old.keys() - new.keys()):
        removed.append(old[uid])

    for uid in sorted(new.keys() & old.keys()):
        before = old[uid]
        after = new[uid]
        field_changes = []

        for key, label in WATCHED_FIELDS:
            old_value = (before.get(key) or "").strip()
            new_value = (after.get(key) or "").strip()

            if key == "match_status":
                old_value = normalize_status(old_value)
                new_value = normalize_status(new_value)

            if old_value != new_value:
                field_changes.append(
                    {
                        "field": key,
                        "label": label,
                        "old": old_value,
                        "new": new_value,
                    }
                )

        if field_changes:
            changed.append(
                {
                    "uid": uid,
                    "before": before,
                    "after": after,
                    "changes": field_changes,
                }
            )

    return added, removed, changed


def fmt(value: str) -> str:
    value = (value or "").strip()
    return value if value else "(blank)"


def build_report(
    watch_name: str,
    added: List[dict],
    removed: List[dict],
    changed: List[dict],
    current_count: int,
) -> str:
    lines = [
        f"# {watch_name} schedule change",
        "",
        f"Current events in feed: **{current_count}**",
        "",
    ]

    if added:
        lines.extend([f"## Added ({len(added)})", ""])
        for event in added:
            lines.extend(
                [
                    f"### {event_label(event)}",
                    "",
                    f"- Status: {fmt(event.get('match_status', ''))}",
                    f"- Group: {fmt(event.get('group', ''))}",
                    f"- Venue: {fmt(event.get('venue_name', ''))}",
                    f"- Pitch: {fmt(event.get('pitch_name', ''))}",
                    "",
                ]
            )

    if removed:
        lines.extend([f"## Removed ({len(removed)})", ""])
        for event in removed:
            lines.extend(
                [
                    f"### {event_label(event)}",
                    "",
                    f"- Previous status: {fmt(event.get('match_status', ''))}",
                    "",
                ]
            )

    if changed:
        lines.extend([f"## Changed ({len(changed)})", ""])
        for item in changed:
            after = item["after"]
            lines.extend([f"### {event_label(after)}", ""])
            for change in item["changes"]:
                lines.append(
                    f"- **{change['label']}**: "
                    f"`{fmt(change['old'])}` → `{fmt(change['new'])}`"
                )
            if after.get("last_modified"):
                lines.append(
                    f"- Weave LAST-MODIFIED: `{after['last_modified']}`"
                )
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "This report compares meaningful schedule fields only. "
            "Feed-level DTSTAMP changes and LAST-MODIFIED-only changes do not trigger alerts.",
            "",
        ]
    )
    return "\n".join(lines)


def append_github_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown)
            if not markdown.endswith("\n"):
                handle.write("\n")


def set_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch an official Weave/GotSport Team App iCalendar feed for meaningful schedule changes."
    )
    parser.add_argument("--state", required=True, help="Path to persistent JSON snapshot.")
    parser.add_argument("--report", required=True, help="Path to Markdown change report.")
    parser.add_argument("--name", required=True, help="Human-readable watch name.")
    args = parser.parse_args()

    url = os.environ.get("WEAVE_ICAL_URL", "").strip()
    if not url:
        raise SystemExit("WEAVE_ICAL_URL environment variable is not set.")

    state_path = Path(args.state)
    report_path = Path(args.report)

    text = fetch_calendar(url)
    current = parse_feed(text)
    previous = load_state(state_path)

    # Initial run establishes a baseline and intentionally does not alert.
    if not previous:
        save_state(state_path, args.name, current)
        report = (
            f"# {args.name} schedule watch\n\n"
            f"Baseline created with **{len(current)}** events.\n\n"
            "No change alert was generated on the first run."
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report + "\n", encoding="utf-8")
        append_github_summary(report)
        set_output("changed", "false")
        set_output("baseline_created", "true")
        set_output("event_count", str(len(current)))
        print(f"Baseline created with {len(current)} events.")
        return 0

    added, removed, changed = compare_events(previous, current)
    material_change = bool(added or removed or changed)

    if material_change:
        save_state(state_path, args.name, current)
        report = build_report(
            args.name,
            added,
            removed,
            changed,
            len(current),
        )
    else:
        report = (
            f"# {args.name} schedule watch\n\n"
            f"No meaningful schedule changes detected.\n\n"
            f"Current events in feed: **{len(current)}**"
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report + "\n", encoding="utf-8")
    append_github_summary(report)

    set_output("changed", "true" if material_change else "false")
    set_output("baseline_created", "false")
    set_output("event_count", str(len(current)))
    set_output("added_count", str(len(added)))
    set_output("removed_count", str(len(removed)))
    set_output("changed_count", str(len(changed)))

    print(
        f"Events: {len(current)} | "
        f"added={len(added)} removed={len(removed)} changed={len(changed)}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
