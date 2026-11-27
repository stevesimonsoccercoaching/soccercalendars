"""Competition-wide CJSL validation regression tests.

All inputs and outputs reside inside a disposable repository.
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from cjsl_corruption_fixtures import corrupt_deflate_member

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

    def test_duplicate_manifest_team_name_is_rejected(self):
        before = self.snapshot_outputs()

        replacement = list(self.shared_match)
        replacement[0] = 279
        replacement[4] = "Unregistered Opponent"

        self.write_export(
            "test-alpha",
            [replacement],
        )

        manifest = json.loads(
            self.manifest.read_text(encoding="utf-8")
        )

        manifest["teams"][2]["name"] = "Test Club Alpha"

        self.manifest.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        self.write_export(
            "test-gamma",
            [self.shared_match, replacement],
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Duplicate team name",
        ):
            self.run_builder()

        self.assertEqual(
            before,
            self.snapshot_outputs(),
        )

    def test_duplicate_manifest_team_slug_is_rejected(self):
        before = self.snapshot_outputs()

        manifest = json.loads(
            self.manifest.read_text(encoding="utf-8")
        )

        manifest["teams"][1]["slug"] = "test-alpha"

        self.manifest.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Duplicate slug",
        ):
            self.run_builder()

        self.assertEqual(
            before,
            self.snapshot_outputs(),
        )

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

    def test_compressed_corruption_and_empty_export_are_aggregated(self):
        before = self.snapshot_outputs()

        corrupt_deflate_member(
            self.data / "test-alpha.xlsx",
            "xl/workbook.xml",
        )

        self.write_export("test-beta", [])

        changed = list(self.gamma_match)
        changed[2] = "19:40 EDT"
        self.write_export("test-gamma", [changed])

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
                ("unreadable_workbook", "error", ("test-alpha",), ()),
                ("empty_export", "error", ("test-beta",), ()),
            ],
        )

        for problem in problems:
            slug = problem.team_slugs[0]
            self.assertIn(
                f"{slug}.xlsx", problem.message
            )

        self.assertEqual(before, self.snapshot_outputs())

    def test_identical_team_identity_prevents_rendering(self):
        before = self.snapshot_outputs()

        changed = list(self.shared_match)
        changed[2] = "19:40 EDT"

        self.write_export("test-alpha", [changed])
        self.write_export("test-beta", [changed])

        invalid = list(self.gamma_match)
        invalid[4] = invalid[3]

        self.write_export("test-gamma", [invalid])

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

        problem = problems[0]

        self.assertEqual(problem.kind, "identical_team_identity")
        self.assertEqual(problem.severity, "error")
        self.assertEqual(problem.team_slugs, ("test-gamma",))
        self.assertEqual(problem.match_ids, (278,))
        self.assertIn("test-gamma.xlsx", problem.message)
        self.assertIn("Test Club Gamma", problem.message)

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

    def test_cli_known_input_rejection_has_no_traceback(self):
        before = self.snapshot_outputs()

        self.write_export("test-alpha", [])
        self.write_export("test-beta", [])

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(REPO_ROOT / "scripts/build_cjsl_calendars.py"),
                str(self.manifest),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)

        self.assertIn(
            "CJSL input validation failed:",
            result.stderr,
        )

        diagnostics = [
            line for line in result.stderr.splitlines()
            if line.startswith("ERROR:")
        ]

        self.assertEqual(len(diagnostics), 2)
        self.assertTrue(
            any("test-alpha.xlsx" in line for line in diagnostics)
        )
        self.assertTrue(
            any("test-beta.xlsx" in line for line in diagnostics)
        )

        self.assertNotIn("Created ", result.stdout)
        self.assertNotIn(
            "All configured CJSL calendars built successfully.",
            result.stdout,
        )

        self.assertEqual(before, self.snapshot_outputs())
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_unexpected_failure_preserves_traceback(self):
        before = self.snapshot_outputs()

        script = REPO_ROOT / "scripts/build_cjsl_calendars.py"

        injected_failure = """
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

script, manifest = sys.argv[1:]
sys.path.insert(0, str(Path(script).parent))
sys.argv = [script, manifest]

with patch(
    "openpyxl.load_workbook",
    side_effect=RuntimeError("Injected unexpected application failure"),
):
    runpy.run_path(script, run_name="__main__")
"""

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                injected_failure,
                str(script),
                str(self.manifest),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Traceback", result.stderr)
        self.assertIn(
            "RuntimeError: Injected unexpected application failure",
            result.stderr,
        )
        self.assertNotIn(
            "CJSL input validation failed:",
            result.stderr,
        )

        self.assertNotIn("Created ", result.stdout)
        self.assertEqual(before, self.snapshot_outputs())

    def test_unexpected_time_parser_index_error_prevents_rendering(self):
        before = self.snapshot_outputs()

        unexpected = IndexError(
            "Injected unexpected time parser failure"
        )

        with (
            patch(
                "build_cjsl_calendars.parse_match_start",
                side_effect=unexpected,
            ) as parser,
            patch("build_cjsl_calendars.build_calendar") as render,
            patch("build_cjsl_calendars.load_revision_state") as load,
            patch("build_cjsl_calendars.save_revision_state") as save,
        ):
            with self.assertRaises(IndexError) as caught:
                self.run_builder()

        self.assertIs(caught.exception, unexpected)
        parser.assert_called_once()

        render.assert_not_called()
        load.assert_not_called()
        save.assert_not_called()

        self.assertEqual(before, self.snapshot_outputs())

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

    def test_material_shared_match_conflicts_are_rejected(self):
        scenarios = [
            ("date", {1: "Sunday, September 27, 2026"}),
            ("time", {2: "19:40 EDT"}),
            ("location", {5: "Different Test Field"}),
            (
                "home_away",
                {
                    3: "Test Club Beta",
                    4: "Test Club Alpha",
                },
            ),
            ("status", {7: "Rained Out"}),
        ]

        for field, changes in scenarios:
            with self.subTest(field=field):
                changed = list(self.shared_match)

                for index, value in changes.items():
                    changed[index] = value

                self.write_export(
                    "test-beta",
                    [changed],
                )

                before = self.snapshot_outputs()

                with self.assertRaises(CJSLInputErrors) as caught:
                    self.run_builder()

                conflicts = [
                    problem
                    for problem in caught.exception.problems
                    if problem.kind == "shared_match_conflict"
                ]

                self.assertTrue(conflicts)

                for problem in conflicts:
                    self.assertEqual(problem.severity, "error")
                    self.assertEqual(problem.match_ids, (277,))
                    self.assertCountEqual(
                        problem.team_slugs,
                        ("test-alpha", "test-beta"),
                    )

                self.assertEqual(
                    before,
                    self.snapshot_outputs(),
                )

    def test_missing_shared_match_counterpart_is_rejected(self):
        for missing_slug, team_name in (
            ("test-alpha", "Test Club Alpha"),
            ("test-beta", "Test Club Beta"),
        ):
            with self.subTest(missing_slug=missing_slug):
                self.write_export(
                    "test-alpha",
                    [self.shared_match],
                )

                self.write_export(
                    "test-beta",
                    [self.shared_match],
                )

                replacement = list(self.shared_match)
                replacement[0] = 279
                replacement[3] = team_name
                replacement[4] = "Unregistered Opponent"

                self.write_export(
                    missing_slug,
                    [replacement],
                )

                before = self.snapshot_outputs()

                with self.assertRaises(CJSLInputErrors) as caught:
                    self.run_builder()

                missing = [
                    problem
                    for problem in caught.exception.problems
                    if problem.kind == "missing_shared_match"
                ]

                self.assertEqual(len(missing), 1)

                problem = missing[0]

                self.assertEqual(problem.severity, "error")
                self.assertEqual(problem.match_ids, (277,))

                self.assertCountEqual(
                    problem.team_slugs,
                    ("test-alpha", "test-beta"),
                )

                self.assertEqual(
                    before,
                    self.snapshot_outputs(),
                )

    def test_multiple_shared_match_conflicts_are_aggregated(self):
        second = list(self.shared_match)
        second[0] = 279

        first_conflict = list(self.shared_match)
        first_conflict[1] = "Sunday, September 27, 2026"
        first_conflict[2] = "19:40 EDT"
        first_conflict[5] = "Different Test Field"

        second_conflict = list(second)
        second_conflict[7] = "Rained Out"

        self.write_export(
            "test-alpha",
            [self.shared_match, second],
        )

        self.write_export(
            "test-beta",
            [first_conflict, second_conflict],
        )

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

        self.assertEqual(len(problems), 2)

        self.assertCountEqual(
            [problem.match_ids for problem in problems],
            [(277,), (279,)],
        )

        for problem in problems:
            self.assertEqual(
                problem.kind,
                "shared_match_conflict",
            )
            self.assertEqual(problem.severity, "error")
            self.assertCountEqual(
                problem.team_slugs,
                ("test-alpha", "test-beta"),
            )

        by_match = {
            problem.match_ids[0]: problem
            for problem in problems
        }

        for field in ("date:", "time:", "location:"):
            self.assertIn(field, by_match[277].message)

        self.assertIn("status:", by_match[279].message)

        self.assertEqual(before, self.snapshot_outputs())

    def test_match_identity_collision_across_three_teams(self):
        collision = list(self.gamma_match)
        collision[0] = 277

        self.write_export(
            "test-gamma",
            [collision],
        )

        before = self.snapshot_outputs()

        with self.assertRaises(CJSLInputErrors) as caught:
            self.run_builder()

        problems = caught.exception.problems

        self.assertEqual(len(problems), 1)

        problem = problems[0]

        self.assertEqual(
            problem.kind,
            "shared_match_conflict",
        )

        self.assertEqual(problem.severity, "error")
        self.assertEqual(problem.match_ids, (277,))

        self.assertCountEqual(
            problem.team_slugs,
            ("test-alpha", "test-beta", "test-gamma"),
        )

        self.assertIn("home_team:", problem.message)
        self.assertIn("away_team:", problem.message)

        self.assertEqual(before, self.snapshot_outputs())

    def test_invalid_opponent_produces_incomplete_comparison(self):
        for expected_kind in (
            "missing_workbook",
            "invalid_match_date",
        ):
            with self.subTest(workbook_problem=expected_kind):
                if expected_kind == "missing_workbook":
                    (self.data / "test-beta.xlsx").unlink()
                else:
                    invalid = list(self.shared_match)
                    invalid[1] = "not-a-date"
                    self.write_export("test-beta", [invalid])

                before = self.snapshot_outputs()

                with self.assertRaises(CJSLInputErrors) as caught:
                    self.run_builder()

                problems = caught.exception.problems

                self.assertCountEqual(
                    [problem.kind for problem in problems],
                    [
                        expected_kind,
                        "incomplete_shared_match_comparison",
                    ],
                )

                incomplete = next(
                    problem for problem in problems
                    if problem.kind ==
                    "incomplete_shared_match_comparison"
                )

                self.assertEqual(incomplete.severity, "warning")
                self.assertEqual(incomplete.match_ids, (277,))
                self.assertCountEqual(
                    incomplete.team_slugs,
                    ("test-alpha", "test-beta"),
                )
                self.assertIn("test-beta", incomplete.message)
                self.assertEqual(before, self.snapshot_outputs())

    def test_incomplete_comparison_does_not_hide_other_conflicts(self):
        second = list(self.shared_match)
        second[0] = 279
        second[4] = "Test Club Gamma"

        self.write_export(
            "test-alpha",
            [self.shared_match, second],
        )

        conflict = list(self.shared_match)
        conflict[7] = "Rained Out"

        self.write_export("test-beta", [conflict])
        (self.data / "test-gamma.xlsx").unlink()

        before = self.snapshot_outputs()

        with self.assertRaises(CJSLInputErrors) as caught:
            self.run_builder()

        self.assertCountEqual(
            [
                (p.kind, p.severity, p.match_ids)
                for p in caught.exception.problems
            ],
            [
                ("missing_workbook", "error", ()),
                ("shared_match_conflict", "error", (277,)),
                (
                    "incomplete_shared_match_comparison",
                    "warning",
                    (279,),
                ),
            ],
        )

        self.assertEqual(before, self.snapshot_outputs())

    def test_results_and_division_disagreements_are_non_blocking(self):
        scenarios = [
            ("result", {8: "2-1"}),
            ("division", {6: "Different Division"}),
            (
                "both",
                {
                    6: "Different Division",
                    8: "2-1",
                },
            ),
        ]

        for scenario, changes in scenarios:
            with self.subTest(scenario=scenario):
                changed = list(self.shared_match)

                for index, value in changes.items():
                    changed[index] = value

                self.write_export("test-beta", [changed])

                warnings = io.StringIO()

                with redirect_stderr(warnings):
                    self.run_builder()

                output = warnings.getvalue()

                self.assertIn("WARNING:", output)
                self.assertIn("277", output)
                self.assertIn("test-alpha", output)
                self.assertIn("test-beta", output)

                if 8 in changes:
                    self.assertIn("result:", output)

                if 6 in changes:
                    self.assertIn("division:", output)

                for slug in (
                    "test-alpha",
                    "test-beta",
                    "test-gamma",
                ):
                    self.assertTrue(
                        (self.docs / f"{slug}.ics").is_file()
                    )

                after = self.snapshot_outputs()

                with redirect_stderr(io.StringIO()):
                    self.run_builder()

                self.assertEqual(
                    after,
                    self.snapshot_outputs(),
                )

    def test_non_blocking_disagreements_are_reported_with_errors(self):
        changed = list(self.shared_match)
        changed[7] = "Rained Out"
        changed[8] = "2-1"
        changed[6] = "Different Division"

        self.write_export("test-beta", [changed])

        before = self.snapshot_outputs()

        with self.assertRaises(CJSLInputErrors) as caught:
            self.run_builder()

        problems = caught.exception.problems

        self.assertCountEqual(
            [
                (p.kind, p.severity, p.match_ids)
                for p in problems
            ],
            [
                ("shared_match_conflict", "error", (277,)),
                ("shared_match_warning", "warning", (277,)),
            ],
        )

        warning = next(
            p for p in problems
            if p.kind == "shared_match_warning"
        )

        self.assertCountEqual(
            warning.team_slugs,
            ("test-alpha", "test-beta"),
        )

        self.assertIn("result:", warning.message)
        self.assertIn("division:", warning.message)

        report = str(caught.exception)

        self.assertIn("ERROR:", report)
        self.assertIn("WARNING:", report)

        self.assertEqual(before, self.snapshot_outputs())

    def test_equivalent_shared_match_values_are_accepted(self):
        scenarios = [
            ("status", 7, "Scheduled", ""),
            ("location", 5, "Test Field", "Test  Field"),
            ("time", 2, "18:40 EDT", "18:40"),
            (
                "date",
                1,
                "Saturday, September 26, 2026",
                "Saturday,  September 26, 2026",
            ),
            (
                "division",
                6,
                "U12 (9v9) D2 Metro",
                "U12 (9v9)  D2 Metro",
            ),
            ("result", 8, "2 - 1", "2  -  1"),
            ("undecided_time", 2, "TBD", "TBA"),
        ]

        for field, index, alpha_value, beta_value in scenarios:
            with self.subTest(field=field):
                alpha = list(self.shared_match)
                beta = list(self.shared_match)

                alpha[index] = alpha_value
                beta[index] = beta_value

                self.write_export("test-alpha", [alpha])
                self.write_export("test-beta", [beta])

                warnings = io.StringIO()

                with redirect_stderr(warnings):
                    self.run_builder()

                self.assertEqual(warnings.getvalue(), "")

    def test_undecided_and_midnight_kickoffs_conflict(self):
        alpha = list(self.shared_match)
        beta = list(self.shared_match)

        alpha[2] = "TBD"
        beta[2] = "00:00 EDT"

        self.write_export("test-alpha", [alpha])
        self.write_export("test-beta", [beta])

        before = self.snapshot_outputs()

        with self.assertRaises(CJSLInputErrors) as caught:
            self.run_builder()

        problems = caught.exception.problems

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].kind, "shared_match_conflict")
        self.assertEqual(problems[0].severity, "error")
        self.assertEqual(problems[0].match_ids, (277,))
        self.assertIn("time:", problems[0].message)

        self.assertEqual(before, self.snapshot_outputs())

    def test_synchronized_reschedule_is_accepted(self):
        changed = list(self.shared_match)

        changed[1] = "Sunday, September 27, 2026"
        changed[2] = "19:40 EDT"
        changed[5] = "Different Test Field"
        changed[7] = "Rescheduled"

        before = self.snapshot_outputs()

        self.write_export("test-alpha", [changed])
        self.write_export("test-beta", [changed])

        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.run_builder()

        self.assertEqual(warnings.getvalue(), "")

        after = self.snapshot_outputs()

        for slug in ("test-alpha", "test-beta"):
            relative = (
                self.docs / f"{slug}.ics"
            ).relative_to(self.root)

            self.assertNotEqual(
                before[relative],
                after[relative],
            )

        gamma = (
            self.docs / "test-gamma.ics"
        ).relative_to(self.root)

        self.assertEqual(before[gamma], after[gamma])

        self.run_builder()

        self.assertEqual(after, self.snapshot_outputs())

    def test_new_shared_match_is_accepted(self):
        new_match = list(self.shared_match)
        new_match[0] = 279

        before = self.snapshot_outputs()

        self.write_export(
            "test-alpha",
            [self.shared_match, new_match],
        )
        self.write_export(
            "test-beta",
            [self.shared_match, new_match],
        )

        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.run_builder()

        self.assertEqual(warnings.getvalue(), "")

        after = self.snapshot_outputs()

        for slug in ("test-alpha", "test-beta"):
            path = self.docs / f"{slug}.ics"
            relative = path.relative_to(self.root)

            self.assertNotEqual(
                before[relative],
                after[relative],
            )

            self.assertEqual(
                path.read_text(
                    encoding="utf-8"
                ).count("BEGIN:VEVENT"),
                2,
            )

        gamma = (
            self.docs / "test-gamma.ics"
        ).relative_to(self.root)

        self.assertEqual(before[gamma], after[gamma])

        self.run_builder()

        self.assertEqual(after, self.snapshot_outputs())

    def test_team_specific_durations_are_preserved(self):
        manifest = json.loads(
            self.manifest.read_text(encoding="utf-8")
        )

        for team in manifest["teams"]:
            if team["slug"] == "test-beta":
                team["duration_minutes"] = 80

        self.manifest.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

        before = self.snapshot_outputs()
        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.run_builder()

        self.assertEqual(warnings.getvalue(), "")

        for slug, expected_end in (
            ("test-alpha", "20260926T194000"),
            ("test-beta", "20260926T200000"),
        ):
            calendar = (
                self.docs / f"{slug}.ics"
            ).read_text(encoding="utf-8")

            lines = calendar.splitlines()

            self.assertIn(
                "UID:cjsl-55206-match-277@system.gotsport.com",
                lines,
            )

            self.assertIn(
                "DTSTART;TZID=America/New_York:20260926T184000",
                lines,
            )

            self.assertIn(
                f"DTEND;TZID=America/New_York:{expected_end}",
                lines,
            )

        after = self.snapshot_outputs()

        for slug in ("test-alpha", "test-gamma"):
            relative = (
                self.docs / f"{slug}.ics"
            ).relative_to(self.root)

            self.assertEqual(before[relative], after[relative])

        self.run_builder()

        self.assertEqual(after, self.snapshot_outputs())

    def test_warning_disagreements_preserve_source_content(self):
        alpha = list(self.shared_match)
        beta = list(self.shared_match)

        alpha[6] = "U12 Division Two"
        alpha[8] = "1-0"

        beta[6] = "U12 Division One"
        beta[8] = "2-1"

        self.write_export("test-alpha", [alpha])
        self.write_export("test-beta", [beta])

        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.run_builder()

        self.assertIn("WARNING:", warnings.getvalue())

        for slug, own, other in (
            ("test-alpha", alpha, beta),
            ("test-beta", beta, alpha),
        ):
            calendar = (
                self.docs / f"{slug}.ics"
            ).read_text(encoding="utf-8")

            unfolded = calendar.replace("\n ", "")

            self.assertIn(
                f"Division: {own[6]}",
                unfolded,
            )

            self.assertIn(
                f"Results: {own[8]}",
                unfolded,
            )

            self.assertNotIn(
                f"Division: {other[6]}",
                unfolded,
            )

            self.assertNotIn(
                f"Results: {other[8]}",
                unfolded,
            )

        after = self.snapshot_outputs()

        with redirect_stderr(io.StringIO()):
            self.run_builder()

        self.assertEqual(after, self.snapshot_outputs())

    def test_shared_reschedule_preserves_revision_continuity(self):
        before_state = json.loads(
            self.revision_file.read_text(encoding="utf-8")
        )

        changed = list(self.shared_match)
        changed[1] = "Sunday, September 27, 2026"
        changed[2] = "19:40 EDT"
        changed[7] = "Rescheduled"

        self.write_export("test-alpha", [changed])
        self.write_export("test-beta", [changed])

        warnings = io.StringIO()

        with redirect_stderr(warnings):
            self.run_builder()

        self.assertEqual(warnings.getvalue(), "")

        after_state = json.loads(
            self.revision_file.read_text(encoding="utf-8")
        )

        key = "55206:277"

        for slug in ("test-alpha", "test-beta"):
            previous = before_state["views"][slug][key]
            current = after_state["views"][slug][key]

            self.assertEqual(
                current["sequence"],
                previous["sequence"] + 1,
            )

            self.assertNotEqual(
                current["fingerprint"],
                previous["fingerprint"],
            )

            lines = (
                self.docs / f"{slug}.ics"
            ).read_text(encoding="utf-8").splitlines()

            self.assertIn(
                "UID:cjsl-55206-match-277@system.gotsport.com",
                lines,
            )

            self.assertIn(
                f"SEQUENCE:{current['sequence']}",
                lines,
            )

            self.assertIn(
                f"LAST-MODIFIED:{current['last_modified']}",
                lines,
            )

            self.assertIn(
                f"DTSTAMP:{current['last_modified']}",
                lines,
            )

            self.assertIn(
                "DTSTART;TZID=America/New_York:20260927T194000",
                lines,
            )

        self.assertEqual(
            before_state["views"]["test-gamma"],
            after_state["views"]["test-gamma"],
        )

        after = self.snapshot_outputs()

        self.run_builder()

        self.assertEqual(after, self.snapshot_outputs())


if __name__ == "__main__":
    unittest.main()
