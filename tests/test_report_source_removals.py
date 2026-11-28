"""Verify source-file cleanup is distinct from match removal."""
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path

from openpyxl import Workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from report_schedule_changes import build_report


class SourceRemovalTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

        self.git("init", "-q")
        self.git("config", "user.name", "Report Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")

        relative = Path("2026-27/cjsl/league/events/55206")
        self.manifest = (
            self.root / "config" / relative / "manifest.json"
        )
        self.manifest.parent.mkdir(parents=True)

        self.manifest.write_text(json.dumps({
            "competition": {
                "name": "CJSL Test",
                "status_mode": "display",
            },
            "teams": [
                {"slug": "team-a", "name": "Team A"}
            ],
        }))

        self.active = (
            self.root / "data" / relative / "team-a.xlsx"
        )

        self.legacy = (
            self.root / "data/2026/cjsl/fall/team-a.xlsx"
        )

        self.write_workbook(self.active)
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_bytes(self.active.read_bytes())

        self.base = self.commit("Initial source inventory")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=self.root,
            text=True,
        ).strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def write_workbook(self, path, location="Field A"):
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        sheet = workbook.active

        sheet.append([
            "Match #", "Date", "Time",
            "Home Team", "Away Team", "Location",
            "Division", "Status",
        ])

        sheet.append([
            277, "2026-11-28", "10:00 AM",
            "Team A", "Team B", location,
            "U12", "Scheduled",
        ])

        workbook.save(path)
        workbook.close()

    def report(self):
        head = self.commit("Test source changes")

        with chdir(self.root):
            return build_report(self.base, head)

    def test_legacy_deletion_is_not_match_removal(self):
        self.legacy.unlink()
        report = self.report()

        self.assertIn("XLSX files changed in Git: **1**", report)
        self.assertIn("Unregistered source files removed: **1**", report)
        self.assertIn("Files with material schedule changes: **0**", report)
        self.assertIn("Matches removed/missing from new exports: **0**", report)
        self.assertIn("data/2026/cjsl/fall/team-a.xlsx", report)

    def test_registered_deletion_remains_material(self):
        self.legacy.unlink()
        self.active.unlink()
        report = self.report()

        self.assertIn("XLSX files changed in Git: **2**", report)
        self.assertIn("Unregistered source files removed: **1**", report)
        self.assertIn("Files with material schedule changes: **1**", report)
        self.assertIn("Matches removed/missing from new exports: **1**", report)

    def test_removed_registration_still_reports_match_removal(self):
        self.active.unlink()
        self.manifest.unlink()
        report = self.report()

        self.assertIn("Unregistered source files removed: **0**", report)
        self.assertIn("Files with material schedule changes: **1**", report)
        self.assertIn("Matches removed/missing from new exports: **1**", report)

    def test_cleanup_does_not_hide_real_schedule_changes(self):
        self.legacy.unlink()
        self.write_workbook(self.active, location="Field B")
        report = self.report()

        self.assertIn("Unregistered source files removed: **1**", report)
        self.assertIn("Files with material schedule changes: **1**", report)
        self.assertIn("Existing matches changed: **1**", report)
        self.assertIn("Matches removed/missing from new exports: **0**", report)
        self.assertIn("Field A", report)
        self.assertIn("Field B", report)


if __name__ == "__main__":
    unittest.main()
