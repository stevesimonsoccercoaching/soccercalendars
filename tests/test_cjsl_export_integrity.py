"""Regression tests for CJSL spreadsheet integrity.

Run from the repository root:
    python -B -m unittest discover -s tests \
        -p 'test_cjsl_export_integrity.py' -v

All workbooks are created in disposable temporary directories.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_cjsl_calendars import (
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

        with self.assertRaises(RuntimeError):
            self.read_export()

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

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_blank_row_is_ignored(self):
        self.write_export([
            [None] * len(self.headers),
            self.match,
        ])

        matches = self.read_export()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_no"], 277)

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

                with self.assertRaises(RuntimeError):
                    self.read_export()

    def test_unrelated_match_is_rejected(self):
        row = list(self.match)
        row[self.headers.index("Home Team")] = "Test Club Gamma"
        row[self.headers.index("Away Team")] = "Test Club Beta"

        self.write_export([row])

        with self.assertRaises(RuntimeError):
            self.read_export()

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

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_empty_worksheet_is_rejected(self):
        workbook = Workbook()
        try:
            workbook.save(self.input_file)
        finally:
            workbook.close()

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_missing_required_column_is_rejected(self):
        self.headers.remove("Status")
        self.write_export([])

        with self.assertRaisesRegex(
            RuntimeError, r"missing required columns"
        ):
            self.read_export()

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

                    with self.assertRaises(RuntimeError):
                        self.read_export()
                finally:
                    self.headers = original_headers

    def test_missing_internal_xlsx_component_is_rejected(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "[Content_Types].xml",
            lambda data: None,
        )

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_malformed_workbook_xml_is_rejected(self):
        self.write_export([self.match])

        self.rewrite_workbook_member(
            "xl/workbook.xml",
            lambda data: b"<workbook>",
        )

        with self.assertRaises(RuntimeError):
            self.read_export()

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

        with self.assertRaises(RuntimeError):
            self.read_export()

    def test_unexpected_programming_errors_propagate(self):
        self.write_export([self.match])

        for error in (
            KeyError("unexpected internal key"),
            TypeError("unexpected programming error"),
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

        with self.assertRaisesRegex(
            RuntimeError,
            "No active worksheet",
        ):
            self.read_export()

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


if __name__ == "__main__":
    unittest.main()
