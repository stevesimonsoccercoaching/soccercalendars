"""CJSL regression guard: shared matches must not inflate revision history.

This file deliberately records one KNOWN pre-existing defect using
@unittest.expectedFailure. Do not remove that decorator until the CJSL
builder is corrected and the test succeeds without it.

Run from repository root:
    python -m unittest discover -s tests -p 'test_cjsl_revision_views.py' -v

The tests write only to a disposable temporary directory. They never
modify real calendars, source workbooks, or revision-state files.
"""

import copy
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from build_cjsl_calendars import build_calendar


class CJSLRevisionViewTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.state = {"version": 1, "matches": {}}
        self.competition = {
            "organizer": "CJSL", "event_id": "55206",
            "timezone": "America/New_York",
        }
        self.defaults = {"reminders": [
            {"trigger": "-P7D", "description": "CJSL match in 7 days"},
            {"trigger": "-P2D", "description": "CJSL match in 2 days"},
            {"trigger": "-PT12H", "description": "CJSL match in 12 hours"},
            {"trigger": "-PT3H", "description": "CJSL match in 3 hours"},
        ]}
        self.alpha = {
            "name": "Test Club Alpha", "short_name": "Alpha U12",
            "slug": "test-alpha",
            "gotsport": {"schedule_url":
                "https://system.gotsport.com/org_event/events/55206/schedules?team=111"},
        }
        self.beta = {
            "name": "Test Club Beta", "short_name": "Beta U12",
            "slug": "test-beta",
            "gotsport": {"schedule_url":
                "https://system.gotsport.com/org_event/events/55206/schedules?team=222"},
        }
        start = datetime(2026, 9, 26, 18, 40)
        self.match = {
            "match_no": 277, "start": start,
            "end": start + timedelta(minutes=60), "all_day": False,
            "home_team": self.alpha["name"],
            "away_team": self.beta["name"],
            "result": "", "location": "Test Field",
            "division": "U12 (9v9)", "status": "Scheduled",
        }

    def render(self, team, changed_at):
        output = self.root / (team["slug"] + ".ics")
        build_calendar(
            competition=self.competition,
            defaults=self.defaults,
            display_names={},
            team=team,
            matches=[self.match],
            output_file=output,
            revision_state=self.state,
            changed_at=changed_at,
        )
        return output.read_bytes()

    def test_single_team_rebuild_is_stable(self):
        """Existing behavior: the same perspective alone is idempotent."""
        first = self.render(self.alpha, "20261119T130000Z")
        initial = copy.deepcopy(self.state)
        second = self.render(self.alpha, "20261119T140000Z")
        self.assertEqual(first, second)
        self.assertEqual(initial, self.state)

    def test_shared_match_preserves_existing_uid_in_both_feeds(self):
        """Keep published UID identity stable during the repair."""
        first = self.render(self.alpha, "20261119T130000Z")
        second = self.render(self.beta, "20261119T130000Z")
        uid = b"UID:cjsl-55206-match-277@system.gotsport.com\r\n"
        self.assertIn(uid, first)
        self.assertIn(uid, second)
        self.assertNotEqual(first, second)  # Different calendar perspectives.

    @unittest.expectedFailure
    def test_two_team_rebuild_does_not_inflate_sequence(self):
        """KNOWN BUG: alternate team summaries overwrite one shared fingerprint.

        The second round must be byte-identical to the first and must not
        change revision state. Remove expectedFailure only AFTER the fix.
        """
        first_alpha = self.render(self.alpha, "20261119T130000Z")
        first_beta = self.render(self.beta, "20261119T130000Z")
        initial_state = copy.deepcopy(self.state)

        next_alpha = self.render(self.alpha, "20261119T140000Z")
        next_beta = self.render(self.beta, "20261119T140000Z")

        self.assertEqual(first_alpha, next_alpha,
                         "Alpha calendar unexpectedly changed on a no-op rebuild")
        self.assertEqual(first_beta, next_beta,
                         "Beta calendar unexpectedly changed on a no-op rebuild")
        self.assertEqual(initial_state, self.state,
                         "Revision state unexpectedly changed on a no-op rebuild")


if __name__ == "__main__":
    unittest.main()
