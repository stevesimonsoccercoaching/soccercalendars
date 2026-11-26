"""Competition-wide CJSL validation regression tests.

All inputs and outputs reside inside a disposable repository.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_cjsl_calendars import CJSLInputErrors, main


class CJSLBuildValidationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

        event = Path("2026-27/cjsl/league/events/55206")

        self.manifest = self.root / "config" / event / "manifest.json"
        self.data = self.root / "data" / event
        self.state = self.root / "state" / event
        self.docs = self.root / "docs/2026/cjsl/fall"

        for folder in (
            self.manifest.parent,
            self.data,
            self.state,
            self.docs,
        ):
            folder.mkdir(parents=True)

        self.teams = [
            {
                "name": f"Test Club {name.title()}",
                "short_name": f"{name.title()} U12",
                "slug": f"test-{name}",
                "gotsport": {
                    "schedule_url":
                        "https://system.gotsport.com/org_event/"
                        f"events/55206/schedules?team={number}"
                },
            }
            for name, number in (
                ("alpha", 111),
                ("beta", 222),
                ("gamma", 333),
            )
        ]

        self.manifest.write_text(
            json.dumps({
                "competition": {
                    "name": "CJSL Test Competition",
                    "organizer": "CJSL",
                    "event_id": "55206",
                    "timezone": "America/New_York",
                    "publication_path": "2026/cjsl/fall",
                    "default_duration_minutes": 60,
                },
                "teams": self.teams,
            }),
            encoding="utf-8",
        )

        self.revision_file = self.state / "revisions.json"

        self.revision_file.write_text(
            json.dumps({
                "version": 2,
                "matches": {},
                "views": {
                    team["slug"]: {}
                    for team in self.teams
                },
            }),
            encoding="utf-8",
        )

        self.headers = [
            "Match #", "Date", "Time", "Home Team",
            "Away Team", "Location", "Division",
            "Status", "Results",
        ]

        self.shared_match = [
            277,
            "Saturday, September 26, 2026",
            "18:40 EDT",
            "Test Club Alpha",
            "Test Club Beta",
            "Test Field",
            "U12 (9v9)",
            "Scheduled",
            "",
        ]

        self.gamma_match = list(self.shared_match)
        self.gamma_match[0] = 278
        self.gamma_match[3] = "Test Club Gamma"
        self.gamma_match[4] = "Unregistered Opponent"

        self.write_export("test-alpha", [self.shared_match])
        self.write_export("test-beta", [self.shared_match])
        self.write_export("test-gamma", [self.gamma_match])

        self.run_builder()

        self.assertEqual(
            {path.name for path in self.docs.glob("*.ics")},
            {
                "test-alpha.ics",
                "test-beta.ics",
                "test-gamma.ics",
            },
        )

    def write_export(self, slug, rows):
        workbook = Workbook()

        try:
            sheet = workbook.active
            sheet.append(self.headers)

            for row in rows:
                sheet.append(row)

            workbook.save(self.data / f"{slug}.xlsx")
        finally:
            workbook.close()

    def run_builder(self):
        with (
            chdir(self.root),
            patch.object(
                sys, "argv",
                ["build_cjsl_calendars.py", str(self.manifest)],
            ),
            redirect_stdout(io.StringIO()),
        ):
            main()

    def snapshot_outputs(self):
        return {
            path.relative_to(self.root): path.read_bytes()
            for folder in (self.docs, self.state)
            for path in folder.rglob("*")
            if path.is_file()
        }

    def test_valid_rebuild_preserves_calendar_and_state_bytes(self):
        before = self.snapshot_outputs()

        self.run_builder()

        self.assertEqual(before, self.snapshot_outputs())

    def test_multiple_team_problems_are_reported(self):
        duplicate = list(self.shared_match)
        duplicate[2] = "not-a-time"

        self.write_export(
            "test-alpha",
            [self.shared_match, duplicate],
        )

        (self.data / "test-beta.xlsx").unlink()

        invalid = list(self.gamma_match)
        invalid[1] = "not-a-date"

        self.write_export("test-gamma", [invalid])

        before = self.snapshot_outputs()

        with self.assertRaises(CJSLInputErrors) as caught:
            self.run_builder()

        problems = caught.exception.problems

        self.assertEqual(len(problems), 4)

        self.assertCountEqual(
            [
                (
                    problem.kind,
                    problem.severity,
                    problem.team_slugs,
                    problem.match_ids,
                )
                for problem in problems
            ],
            [
                (
                    "duplicate_match_id",
                    "error",
                    ("test-alpha",),
                    (277,),
                ),
                (
                    "invalid_match_time",
                    "error",
                    ("test-alpha",),
                    (277,),
                ),
                (
                    "missing_workbook",
                    "error",
                    ("test-beta",),
                    (),
                ),
                (
                    "invalid_match_date",
                    "error",
                    ("test-gamma",),
                    (278,),
                ),
            ],
        )

        for problem in problems:
            self.assertTrue(problem.message)

        self.assertEqual(before, self.snapshot_outputs())

    def test_late_invalid_team_does_not_modify_outputs(self):
        before = self.snapshot_outputs()

        changed = list(self.shared_match)
        changed[2] = "19:40 EDT"

        self.write_export("test-alpha", [changed])
        self.write_export("test-beta", [changed])

        invalid = list(self.gamma_match)
        invalid[1] = "not-a-date"

        self.write_export("test-gamma", [invalid])

        with self.assertRaises(RuntimeError):
            self.run_builder()

        self.assertEqual(before, self.snapshot_outputs())

    def test_rejected_inputs_never_enter_rendering_phase(self):
        self.write_export("test-gamma", [])

        before = self.snapshot_outputs()

        with (
            patch("build_cjsl_calendars.build_calendar") as render,
            patch("build_cjsl_calendars.load_revision_state") as load,
            patch("build_cjsl_calendars.save_revision_state") as save,
        ):
            with self.assertRaises(CJSLInputErrors) as caught:
                self.run_builder()

        render.assert_not_called()
        load.assert_not_called()
        save.assert_not_called()

        problems = caught.exception.problems

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].kind, "empty_export")
        self.assertEqual(problems[0].team_slugs, ("test-gamma",))

        self.assertEqual(before, self.snapshot_outputs())

    def test_rejected_first_build_creates_no_outputs(self):
        for path in self.docs.glob("*.ics"):
            path.unlink()

        self.revision_file.unlink()

        self.write_export("test-gamma", [])

        self.assertEqual(self.snapshot_outputs(), {})

        with self.assertRaises(CJSLInputErrors):
            self.run_builder()

        self.assertEqual(self.snapshot_outputs(), {})

    def test_unexpected_collection_failure_prevents_rendering(self):
        from build_cjsl_calendars import CJSLInputError

        known = CJSLInputError(
            "invalid_match_date",
            "Known input problem",
            (277,),
        )

        unexpected = RuntimeError(
            "Unexpected application failure"
        )

        before = self.snapshot_outputs()

        with (
            patch(
                "build_cjsl_calendars.read_matches",
                side_effect=[known, unexpected],
            ) as reader,
            patch("build_cjsl_calendars.build_calendar") as render,
            patch("build_cjsl_calendars.load_revision_state") as load,
            patch("build_cjsl_calendars.save_revision_state") as save,
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.run_builder()

        self.assertIs(caught.exception, unexpected)
        self.assertEqual(reader.call_count, 2)

        render.assert_not_called()
        load.assert_not_called()
        save.assert_not_called()

        self.assertEqual(before, self.snapshot_outputs())


if __name__ == "__main__":
    unittest.main()
