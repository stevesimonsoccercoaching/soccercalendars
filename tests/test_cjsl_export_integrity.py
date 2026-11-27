"""Regression tests for CJSL spreadsheet integrity.

Run from the repository root:
    python -B -m unittest discover -s tests \
        -p 'test_cjsl_export_integrity.py' -v

All workbooks are created in disposable temporary directories.
"""

import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import Workbook, load_workbook

from cjsl_corruption_fixtures import corrupt_deflate_member

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_cjsl_calendars import (
    CJSLInputError,
    CJSLInputErrors,
    parse_match_number,
    read_matches,
)


class CJSLExportIntegrityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.input_file = self.root / "test-alpha.xlsx"

        self.team_name = "Test Club Alpha"

        self.headers = [
            "Match #",
            "Date",
            "Time",
            "Home Team",
            "Away Team",
            "Location",
            "Division",
            "Status",
            "Results",
        ]

        self.match = [
            277,
            "Saturday, September 26, 2026",
            "18:40 EDT",
            self.team_name,
            "Test Club Beta",
            "Test Field",
            "U12 (9v9)",
            "Scheduled",
            "",
        ]

    def write_export(self, rows):
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.append(self.headers)

            for row in rows:
                sheet.append(row)

            workbook.save(self.input_file)
        finally:
            workbook.close()

    def read_export(self):
        return read_matches(
            input_file=self.input_file,
            tracked_team_name=self.team_name,
            duration_minutes=60,
        )

    def test_valid_single_match_export(self):
        self.write_export([self.match])

        matches = self.read_export()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_no"], 277)
        self.assertEqual(matches[0]["status"], "Scheduled")

    def test_header_only_export_is_rejected(self):
        self.write_export([])

        self.assert_structured_input_error('empty_export', ())

    def test_identical_duplicate_match_id_is_rejected(self):
        self.write_export([
            self.match,
            list(self.match),
        ])

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_conflicting_duplicate_match_id_is_rejected(self):
        duplicate = list(self.match)
        duplicate[self.headers.index("Time")] = "19:40 EDT"

        self.write_export([
            self.match,
            duplicate,
        ])

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_valid_match_id_representations(self):
        for value in (277, "277", 277.0):
            with self.subTest(match_id=value):
                row = list(self.match)
                row[0] = value

                self.write_export([row])
                matches = self.read_export()

                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["match_no"], 277)

    def test_invalid_match_ids_are_rejected(self):
        for value in (277.5, 0, -277, True, "ABC"):
            with self.subTest(match_id=value):
                row = list(self.match)
                row[0] = value

                self.write_export([row])

                with self.assertRaises(RuntimeError):
                    self.read_export()

    def test_populated_row_without_match_id_is_rejected(self):
        incomplete = list(self.match)
        incomplete[0] = None

        self.write_export([self.match, incomplete])

        self.assert_structured_input_error('missing_match_id', ())

    def test_blank_row_is_ignored(self):
        self.write_export([
            [None] * len(self.headers),
            self.match,
        ])

        matches = self.read_export()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_no"], 277)

    def test_identical_team_identity_is_rejected(self):
        row = list(self.match)
        row[self.headers.index("Away Team")] = self.team_name

        self.write_export([row])

        with self.assertRaises(CJSLInputError) as caught:
            self.read_export()

        error = caught.exception

        self.assertEqual(error.kind, "identical_team_identity")
        self.assertEqual(error.match_ids, (277,))
        self.assertIn(str(self.input_file), str(error))
        self.assertIn(self.team_name, str(error))
        self.assertIn("Row 2", str(error))

    def test_identical_team_identity_after_trimming_is_rejected(self):
        row = list(self.match)
        row[self.headers.index("Away Team")] = (
            "  " + self.team_name + "  "
        )

        self.write_export([row])

        self.assert_structured_input_error(
            "identical_team_identity",
            (277,),
        )

    def test_identical_team_identity_and_invalid_time_are_aggregated(self):
        row = list(self.match)
        row[self.headers.index("Away Team")] = self.team_name
        row[self.headers.index("Time")] = "not-a-time"

        self.write_export([row])

        self.assert_aggregated_input_errors([
            ("identical_team_identity", (277,)),
            ("invalid_match_time", (277,)),
        ])

    def test_missing_team_identity_is_rejected(self):
        for missing_column, other_column in (
            ("Home Team", "Away Team"),
            ("Away Team", "Home Team"),
        ):
            with self.subTest(column=missing_column):
                row = list(self.match)
                row[self.headers.index(missing_column)] = None
                row[self.headers.index(other_column)] = self.team_name

                self.write_export([row])

                self.assert_structured_input_error('missing_team_identity', (277,))

    def test_unrelated_match_is_rejected(self):
        row = list(self.match)
        row[self.headers.index("Home Team")] = "Test Club Gamma"
        row[self.headers.index("Away Team")] = "Test Club Beta"

        self.write_export([row])

        self.assert_structured_input_error('unrelated_match', (277,))

    def test_match_number_parser_accepts_valid_values(self):
        for value in (
            277, 277.0, "277", " 277 ", "0277",
        ):
            with self.subTest(value=value):
                result = parse_match_number(value)
                self.assertEqual(result, 277)
                self.assertIs(type(result), int)

    def test_match_number_parser_rejects_invalid_values(self):
        for value in (
            None, "", " ", True, False,
            0, -277, 277.5,
            "ABC", "277.5", "277.0",
            "+277", "-277", "2_77", "2.77e2",
            float("nan"), float("inf"), float("-inf"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_match_number(value)

    def test_normalized_duplicate_match_ids_are_rejected(self):
        duplicate = list(self.match)
        duplicate[0] = "0277"

        self.write_export([self.match, duplicate])

        with self.assertRaisesRegex(
            RuntimeError, r"Duplicate Match # 277"
        ):
            self.read_export()

    def test_whitespace_only_row_is_ignored(self):
        self.write_export([
            ["   "] * len(self.headers),
            self.match,
        ])

        matches = self.read_export()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_no"], 277)

    def test_whitespace_only_match_id_is_rejected(self):
        incomplete = list(self.match)
        incomplete[0] = "   "

        self.write_export([self.match, incomplete])

        with self.assertRaisesRegex(
            RuntimeError, r"Row 3 is missing Match #"
        ):
            self.read_export()

    def test_missing_workbook_is_rejected(self):
        self.assertFalse(self.input_file.exists())

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_corrupt_workbook_is_rejected(self):
        self.input_file.write_bytes(
            b"This is not a valid XLSX file."
        )

        self.assert_structured_input_error('unreadable_workbook', ())

    def test_empty_worksheet_is_rejected(self):
        workbook = Workbook()
        try:
            workbook.save(self.input_file)
        finally:
            workbook.close()

        self.assert_structured_input_error('empty_worksheet', ())

    def test_missing_required_column_is_rejected(self):
        self.headers.remove("Status")
        self.write_export([])

        self.assert_structured_input_error('missing_columns', (), message='missing required columns')

    def test_workbook_is_closed_after_successful_read(self):
        self.write_export([self.match])

        workbook = load_workbook(
            self.input_file,
            read_only=True,
            data_only=True,
        )

        try:
            with patch.object(
                workbook, "close", wraps=workbook.close
            ) as close:
                with patch(
                    "build_cjsl_calendars.load_workbook",
                    return_value=workbook,
                ):
                    matches = self.read_export()

                self.assertEqual(len(matches), 1)
                close.assert_called_once()
        finally:
            workbook.close()

    def test_workbook_is_closed_after_validation_failure(self):
        for failure in ("missing column", "invalid match ID"):
            with self.subTest(failure=failure):
                original_headers = list(self.headers)

                try:
                    if failure == "missing column":
                        self.headers.remove("Status")
                        self.write_export([])
                    else:
                        row = list(self.match)
                        row[0] = 277.5
                        self.write_export([row])
                finally:
                    self.headers = original_headers

                workbook = load_workbook(
                    self.input_file,
                    read_only=True,
                    data_only=True,
                )

                try:
                    with patch.object(
                        workbook, "close", wraps=workbook.close
                    ) as close:
                        with patch(
                            "build_cjsl_calendars.load_workbook",
                            return_value=workbook,
                        ):
                            with self.assertRaises(RuntimeError):
                                self.read_export()

                        close.assert_called_once()
                finally:
                    workbook.close()

    def rewrite_workbook_member(self, member, transform):
        rewritten = self.input_file.with_name("rewritten.xlsx")

        with ZipFile(self.input_file, "r") as original:
            self.assertIn(member, original.namelist())

            with ZipFile(rewritten, "w") as modified:
                for item in original.infolist():
                    data = original.read(item.filename)

                    if item.filename == member:
                        data = transform(data)

                    if data is not None:
                        modified.writestr(item, data)

        rewritten.replace(self.input_file)

    def test_compressed_workbook_corruption_is_structured(self):
        self.write_export([self.match])
        self.assertEqual(len(self.read_export()), 1)

        corrupt_deflate_member(
            self.input_file, "xl/workbook.xml"
        )

        with self.assertRaises(CJSLInputError) as caught:
            self.read_export()

        error = caught.exception
        self.assertEqual(error.kind, "unreadable_workbook")
        self.assertEqual(error.match_ids, ())
        self.assertIn(str(self.input_file), str(error))
        self.assertIsInstance(error.__cause__, zlib.error)

    def test_compressed_worksheet_corruption_is_structured(self):
        rows = []

        for match_no in range(1, 301):
            row = list(self.match)
            row[0] = match_no
            row[self.headers.index("Location")] = "Field " + "x" * 500
            rows.append(row)

        self.write_export(rows)
        self.assertEqual(len(self.read_export()), 300)

        corrupt_deflate_member(
            self.input_file,
            "xl/worksheets/sheet1.xml",
            late=True,
        )

        with self.assertRaises(CJSLInputError) as caught:
            self.read_export()

        error = caught.exception
        self.assertEqual(error.kind, "unreadable_worksheet")
        self.assertEqual(error.match_ids, ())
        self.assertIn(str(self.input_file), str(error))
        self.assertIsInstance(error.__cause__, zlib.error)

    def test_invalid_workbook_metadata_is_structured(self):
        self.write_export([self.match])

        namespace = (
            "{http://schemas.openxmlformats.org/"
            "spreadsheetml/2006/main}"
        )

        def invalidate_sheet_state(data):
            root = ET.fromstring(data)
            sheet = root.find(f".//{namespace}sheet")

            self.assertIsNotNone(sheet)
            sheet.set("state", "invalid-state")

            return ET.tostring(root, encoding="utf-8")

        self.rewrite_workbook_member(
            "xl/workbook.xml",
            invalidate_sheet_state,
        )

        with patch(
            "build_cjsl_calendars.load_workbook",
            wraps=load_workbook,
        ) as loader:
            try:
                self.read_export()
            except CJSLInputError as error:
                self.assertEqual(
                    error.kind,
                    "unreadable_workbook",
                )

                self.assertEqual(error.match_ids, ())
                self.assertEqual(error.severity, "error")
                self.assertIsInstance(
                    error.__cause__,
                    ValueError,
                )

                loader.assert_called_once()
                self.assertTrue(
                    loader.call_args.args[0].closed
                )
            else:
                self.fail(
                    "Invalid workbook metadata was accepted"
                )

    def corrupt_numeric_match_cell(self, coordinate):
        namespace = (
            "{http://schemas.openxmlformats.org/"
            "spreadsheetml/2006/main}"
        )

        def invalidate_numeric_cell(data):
            root = ET.fromstring(data)

            cell = root.find(
                f".//{namespace}c[@r='{coordinate}']"
            )

            self.assertIsNotNone(cell)
            self.assertEqual(cell.get("t"), "n")

            value = cell.find(f"{namespace}v")
            self.assertIsNotNone(value)

            value.text = "not-a-number"

            return ET.tostring(root, encoding="utf-8")

        self.rewrite_workbook_member(
            "xl/worksheets/sheet1.xml",
            invalidate_numeric_cell,
        )

    def test_invalid_numeric_cell_is_structured(self):
        self.write_export([self.match])
        self.corrupt_numeric_match_cell("A2")

        with patch(
            "build_cjsl_calendars.load_workbook",
            wraps=load_workbook,
        ) as loader:
            try:
                self.read_export()
            except CJSLInputError as error:
                self.assertEqual(
                    error.kind,
                    "unreadable_worksheet",
                )

                self.assertEqual(error.match_ids, ())
                self.assertEqual(error.severity, "error")
                self.assertIsInstance(
                    error.__cause__,
                    ValueError,
                )

                loader.assert_called_once()
                self.assertTrue(
                    loader.call_args.args[0].closed
                )
            else:
                self.fail(
                    "Invalid numeric cell was accepted"
                )

    def test_row_errors_survive_numeric_decoding_failure(self):
        first = list(self.match)
        first[1] = "not-a-date"

        second = list(self.match)
        second[0] = 278

        self.write_export([first, second])
        self.corrupt_numeric_match_cell("A3")

        self.assert_aggregated_input_errors([
            ("invalid_match_date", (277,)),
            ("unreadable_worksheet", ()),
        ])

    def test_unexpected_application_value_error_propagates(self):
        from build_cjsl_calendars import clean_text

        self.write_export([self.match])

        unexpected = ValueError(
            "Unexpected application failure"
        )

        def unexpected_location_failure(value):
            if value == "Test Field":
                raise unexpected

            return clean_text(value)

        with patch(
            "build_cjsl_calendars.clean_text",
            side_effect=unexpected_location_failure,
        ):
            with self.assertRaises(ValueError) as caught:
                self.read_export()

        self.assertIs(caught.exception, unexpected)

    def test_duplicate_required_column_is_rejected(self):
        original_headers = list(self.headers)

        for header, status in (
            ("Status", "Canceled"),
            (" Status ", "Scheduled"),
        ):
            with self.subTest(header=header, status=status):
                self.headers = original_headers + [header]

                try:
                    self.write_export([self.match + [status]])

                    self.assert_structured_input_error('duplicate_column', ())
                finally:
                    self.headers = original_headers

    def test_missing_internal_xlsx_component_is_rejected(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "[Content_Types].xml",
            lambda data: None,
        )

        self.assert_structured_input_error('missing_workbook_component', ())

    def test_malformed_workbook_xml_is_rejected(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "xl/workbook.xml",
            lambda data: b"<workbook>",
        )

        self.assert_structured_input_error('unreadable_workbook', ())

    def test_malformed_worksheet_xml_is_rejected(self):
        self.write_export([self.match])

        def truncate_xml(data):
            closing_tag = b"</worksheet>"
            self.assertTrue(data.endswith(closing_tag))
            return data.removesuffix(closing_tag)

        self.rewrite_workbook_member(
            "xl/worksheets/sheet1.xml",
            truncate_xml,
        )

        self.assert_structured_input_error('unreadable_worksheet', ())

    def test_unexpected_programming_errors_propagate(self):
        self.write_export([self.match])

        for error in (
            KeyError("unexpected internal key"),
            TypeError("unexpected programming error"),
            ValueError("unexpected acquisition failure"),
            RuntimeError("unexpected runtime failure"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch(
                    "build_cjsl_calendars.load_workbook",
                    side_effect=error,
                ):
                    with self.assertRaises(type(error)) as caught:
                        self.read_export()

                    self.assertIs(caught.exception, error)

    def test_duplicate_optional_results_column_is_rejected(self):
        self.headers = self.headers + ["Results"]
        self.write_export([self.match + ["2-1"]])

        with self.assertRaisesRegex(
            RuntimeError,
            r"Duplicate column 'Results'.*columns 9 and 10",
        ):
            self.read_export()

    def test_duplicate_unused_columns_and_blank_headers_are_allowed(self):
        self.headers = self.headers + [
            "Unused", "Unused", None, " ",
        ]

        self.write_export([
            self.match + ["First", "Second", None, None],
        ])

        matches = self.read_export()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_no"], 277)
        self.assertEqual(matches[0]["status"], "Scheduled")
        self.assertEqual(matches[0]["result"], "")

    def test_workbook_is_closed_after_worksheet_parse_failure(self):
        self.write_export([self.match])

        def truncate_xml(data):
            closing_tag = b"</worksheet>"
            self.assertTrue(data.endswith(closing_tag))
            return data.removesuffix(closing_tag)

        self.rewrite_workbook_member(
            "xl/worksheets/sheet1.xml",
            truncate_xml,
        )

        workbook = load_workbook(
            self.input_file,
            read_only=True,
            data_only=True,
        )

        try:
            with patch.object(
                workbook,
                "close",
                wraps=workbook.close,
            ) as close:
                with patch(
                    "build_cjsl_calendars.load_workbook",
                    return_value=workbook,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Unable to read worksheet",
                    ) as caught:
                        self.read_export()

                    close.assert_called_once()
                    self.assertIsNotNone(
                        caught.exception.__cause__
                    )
        finally:
            workbook.close()

    def test_missing_workbook_xml_is_rejected(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "xl/workbook.xml",
            lambda data: None,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "Missing internal XLSX component",
        ):
            self.read_export()

    def test_missing_active_worksheet_is_rejected(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "xl/worksheets/sheet1.xml",
            lambda data: None,
        )

        self.assert_structured_input_error('missing_worksheet', (), message='No active worksheet')

    def test_source_file_is_closed_after_successful_read(self):
        self.write_export([self.match])

        with patch(
            "build_cjsl_calendars.load_workbook",
            wraps=load_workbook,
        ) as loader:
            matches = self.read_export()

        self.assertEqual(len(matches), 1)
        loader.assert_called_once()

        source = loader.call_args.args[0]

        self.assertTrue(
            getattr(source, "closed", False),
            "Builder must own and close the binary source file",
        )

    def test_source_file_is_closed_after_failed_acquisition(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "xl/workbook.xml",
            lambda data: b"<workbook>",
        )

        with patch(
            "build_cjsl_calendars.load_workbook",
            wraps=load_workbook,
        ) as loader:
            try:
                self.read_export()
            except RuntimeError as exc:
                self.assertIn(
                    "Unable to open workbook",
                    str(exc),
                )

                loader.assert_called_once()
                source = loader.call_args.args[0]

                self.assertTrue(
                    getattr(source, "closed", False),
                    "Source file must be closed while "
                    "the acquisition exception is retained",
                )
            else:
                self.fail(
                    "Malformed workbook was unexpectedly accepted"
                )

    def assert_structured_input_error(self, kind, match_ids, message=None):
        with self.assertRaises(CJSLInputError) as caught:
            self.read_export()

        error = caught.exception
        self.assertEqual(error.kind, kind)
        self.assertEqual(error.match_ids, match_ids)

        if message is not None:
            self.assertRegex(str(error), message)

    def test_structured_missing_workbook_error(self):
        self.assertFalse(self.input_file.exists())

        self.assert_structured_input_error(
            "missing_workbook",
            (),
        )

    def test_structured_invalid_match_id_error(self):
        row = list(self.match)
        row[0] = "ABC"

        self.write_export([row])

        self.assert_structured_input_error(
            "invalid_match_id",
            (),
        )

    def test_structured_invalid_match_date_error(self):
        row = list(self.match)
        row[1] = "not-a-date"

        self.write_export([row])

        self.assert_structured_input_error(
            "invalid_match_date",
            (277,),
        )

    def test_structured_invalid_match_time_error(self):
        row = list(self.match)
        row[self.headers.index("Time")] = "not-a-time"
        self.write_export([row])

        self.assert_structured_input_error(
            "invalid_match_time",
            (277,),
        )

    def test_structured_duplicate_match_id_error(self):
        self.write_export([
            self.match,
            list(self.match),
        ])

        self.assert_structured_input_error(
            "duplicate_match_id",
            (277,),
        )

    def assert_aggregated_input_errors(self, expected):
        with self.assertRaises(CJSLInputErrors) as caught:
            self.read_export()

        problems = caught.exception.problems

        actual = [
            (problem.kind, problem.match_ids)
            for problem in problems
        ]

        self.assertCountEqual(actual, expected)

    def test_multiple_invalid_rows_are_reported(self):
        invalid_date = list(self.match)
        invalid_date[0] = 278
        invalid_date[1] = "not-a-date"

        invalid_id = list(self.match)
        invalid_id[0] = "ABC"

        invalid_time = list(self.match)
        invalid_time[0] = 280
        invalid_time[2] = "not-a-time"

        self.write_export([
            self.match,
            invalid_date,
            invalid_id,
            invalid_time,
        ])

        self.assert_aggregated_input_errors([
            ("invalid_match_date", (278,)),
            ("invalid_match_id", ()),
            ("invalid_match_time", (280,)),
        ])

    def test_duplicate_id_in_invalid_row_is_reported(self):
        duplicate = list(self.match)
        duplicate[2] = "not-a-time"

        self.write_export([
            self.match,
            duplicate,
        ])

        self.assert_aggregated_input_errors([
            ("duplicate_match_id", (277,)),
            ("invalid_match_time", (277,)),
        ])

    def test_independent_errors_in_same_row_are_reported(self):
        row = list(self.match)
        row[self.headers.index("Home Team")] = None
        row[self.headers.index("Date")] = "not-a-date"

        self.write_export([row])

        self.assert_aggregated_input_errors([
            ("missing_team_identity", (277,)),
            ("invalid_match_date", (277,)),
        ])

    def test_independent_errors_with_unknown_match_id(self):
        row = list(self.match)
        row[0] = "ABC"
        row[1] = "not-a-date"
        row[2] = "not-a-time"

        self.write_export([row])

        self.assert_aggregated_input_errors([
            ("invalid_match_id", ()),
            ("invalid_match_date", ()),
            ("invalid_match_time", ()),
        ])

    def test_row_errors_survive_late_worksheet_failure(self):
        row = list(self.match)
        row[1] = "not-a-date"

        self.write_export([row])

        def truncate_xml(data):
            closing_tag = b"</worksheet>"
            self.assertTrue(data.endswith(closing_tag))
            return data.removesuffix(closing_tag)

        self.rewrite_workbook_member(
            "xl/worksheets/sheet1.xml",
            truncate_xml,
        )

        self.assert_aggregated_input_errors([
            ("invalid_match_date", (277,)),
            ("unreadable_worksheet", ()),
        ])

    def test_unexpected_failure_after_known_row_problem(self):
        second = list(self.match)
        second[0] = 278

        self.write_export([self.match, second])

        unexpected = RuntimeError(
            "Unexpected failure during row validation"
        )

        with patch(
            "build_cjsl_calendars.parse_match_date",
            side_effect=[
                ValueError("Invalid date"),
                unexpected,
            ],
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.read_export()

        self.assertIs(caught.exception, unexpected)

    def test_multiple_header_errors_are_reported(self):
        self.headers.remove("Division")
        self.headers.extend(["Status", "Results"])

        self.write_export([])

        self.assert_aggregated_input_errors([
            ("duplicate_column", ()),
            ("duplicate_column", ()),
            ("missing_columns", ()),
        ])

    def test_invalid_headers_prevent_row_validation(self):
        self.headers.extend(["Status", "Results"])

        row = list(self.match)
        row[0] = "ABC"
        row.extend(["Canceled", "2-1"])

        self.write_export([row])

        with patch(
            "build_cjsl_calendars.parse_match_number"
        ) as parser:
            self.assert_aggregated_input_errors([
                ("duplicate_column", ()),
                ("duplicate_column", ()),
            ])

        parser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
