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

from openpyxl import Workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_cjsl_calendars import read_matches


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


if __name__ == "__main__":
    unittest.main()
