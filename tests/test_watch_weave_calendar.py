import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from watch_weave_calendar import (
    compare_events,
    load_state,
    parse_feed,
    save_state,
)


class WatchWeaveCalendarTests(unittest.TestCase):
    def event(self, uid="match-1", status="Scheduled"):
        return {
            "uid": uid,
            "start": "20261129T150000Z",
            "end": "20261129T170000Z",
            "home_team": "Brooklyn Patriots",
            "away_team": "Opponent",
            "match_status": status,
            "location": "Field 1",
            "summary": "Soccer match",
            "created": "20261101T120000Z",
            "last_modified": "20261101T120000Z",
        }

    def test_no_changes_for_identical_events(self):
        event = self.event()
        added, removed, changed = compare_events(
            {"match-1": event},
            {"match-1": event.copy()},
        )
        self.assertEqual((added, removed, changed), ([], [], []))

    def test_metadata_only_changes_do_not_alert(self):
        old = self.event()
        new = old.copy()
        new["created"] = "20261120T120000Z"
        new["last_modified"] = "20261120T120000Z"
        new["summary"] = "Updated summary"

        added, removed, changed = compare_events(
            {"match-1": old},
            {"match-1": new},
        )
        self.assertEqual((added, removed, changed), ([], [], []))

    def test_match_status_change_is_detected(self):
        old = self.event()
        new = self.event(status="Rained Out")

        added, removed, changed = compare_events(
            {"match-1": old},
            {"match-1": new},
        )
        self.assertFalse(added)
        self.assertFalse(removed)
        self.assertEqual(len(changed), 1)
        self.assertTrue(
            any(
                field["field"] == "match_status"
                for field in changed[0]["changes"]
            )
        )

    def test_blank_status_equals_scheduled(self):
        old = self.event(status="")
        new = self.event(status="Scheduled")
        self.assertEqual(
            compare_events({"match-1": old}, {"match-1": new}),
            ([], [], []),
        )

    def test_additions_and_removals_are_detected(self):
        old = {"old-match": self.event(uid="old-match")}
        new = {"new-match": self.event(uid="new-match")}

        added, removed, changed = compare_events(old, new)
        self.assertEqual(len(added), 1)
        self.assertEqual(len(removed), 1)
        self.assertFalse(changed)

    def test_snapshot_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            events = {"match-1": self.event()}

            save_state(path, "SFFK Test", events)

            self.assertEqual(load_state(path), events)
            payload = json.loads(path.read_text())
            self.assertEqual(payload["event_count"], 1)
            self.assertEqual(payload["watch_name"], "SFFK Test")

    def test_incomplete_calendar_rejected(self):
        with self.assertRaises(RuntimeError):
            parse_feed("BEGIN:VCALENDAR\nBEGIN:VEVENT\n")

    def test_valid_calendar_parsed(self):
        feed = (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:match-1\n"
            "DTSTART:20261129T150000Z\n"
            "DESCRIPTION:Match status\\nRained Out\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        events = parse_feed(feed)
        self.assertEqual(events["match-1"]["match_status"], "Rained Out")


if __name__ == "__main__":
    unittest.main()
