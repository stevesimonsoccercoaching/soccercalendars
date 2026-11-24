import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from watch_weave_calendar import WATCHED_FIELDS
from weave_notification_id import notification_id


class WeaveNotificationIdTests(unittest.TestCase):
    def setUp(self):
        self.baseline = b'{"events":{"match-1":{"match_status":"Scheduled"}}}'
        self.events = {
            "match-1": {
                "start": "20261129T150000Z",
                "end": "20261129T170000Z",
                "home_team": "Brooklyn Patriots",
                "away_team": "Opponent",
                "match_status": "Rained Out",
                "location": "Field 1",
                "last_modified": "20261101T120000Z",
            }
        }

    def identify(self, events=None, baseline=None):
        return notification_id(
            self.baseline if baseline is None else baseline,
            self.events if events is None else events,
            WATCHED_FIELDS,
        )

    def test_identifier_is_stable_across_retries(self):
        self.assertEqual(self.identify(), self.identify())
        self.assertRegex(self.identify(), r"^[0-9a-f]{24}$")

    def test_metadata_only_changes_are_ignored(self):
        updated = {key: value.copy() for key, value in self.events.items()}
        updated["match-1"]["last_modified"] = "20261124T180000Z"
        updated["match-1"]["summary"] = "Updated summary"
        self.assertEqual(self.identify(), self.identify(updated))

    def test_baseline_timestamp_does_not_change_identifier(self):
        first = (
            b'{"updated_at_utc":"2026-11-01T00:00:00Z",'
            b'"events":{"match-1":{"match_status":"Scheduled"}}}'
        )
        second = (
            b'{"updated_at_utc":"2026-11-24T00:00:00Z",'
            b'"events":{"match-1":{"match_status":"Scheduled"}}}'
        )

        self.assertEqual(
            self.identify(baseline=first),
            self.identify(baseline=second),
        )

    def test_meaningful_change_creates_new_identifier(self):
        updated = {key: value.copy() for key, value in self.events.items()}
        updated["match-1"]["location"] = "Field 2"
        self.assertNotEqual(self.identify(), self.identify(updated))

    def test_different_baselines_create_different_identifiers(self):
        self.assertNotEqual(
            self.identify(),
            self.identify(baseline=b'{"events":{"match-1":{"match_status":"Postponed"}}}'),
        )

    def test_blank_status_and_scheduled_are_equivalent(self):
        blank = {key: value.copy() for key, value in self.events.items()}
        scheduled = {key: value.copy() for key, value in self.events.items()}
        blank["match-1"]["match_status"] = ""
        scheduled["match-1"]["match_status"] = "Scheduled"
        self.assertEqual(self.identify(blank), self.identify(scheduled))


if __name__ == "__main__":
    unittest.main()
