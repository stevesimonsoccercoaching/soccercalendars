"""Regression tests for the read-only soccer manifest validator.

Run from repository root:
    python -m unittest discover -s tests -p "test_validate_soccer_manifest.py" -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from validate_soccer_manifest import validate


class ManifestValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_cwd = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.original_cwd)
        self.addCleanup(self.temp.cleanup)

        self.manifest_path = Path("config/2026-27/edp-north-atlantic/manifest.json")
        self.data_dir = Path("data/2026-27/edp-north-atlantic")
        self.manifest_path.parent.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        self.manifest = {
            "competition": {"event_id": "54578"},
            "display_names": {"Other Team": "Other"},
            "teams": [{
                "name": "DV7 NY B2015/16",
                "short_name": "DV7 NY U11",
                "slug": "dv7-ny-b2015-16",
                "gotsport": {
                    "schedule_team_id": "4151617",
                    "export_team_id": "4151617",
                    "schedule_url": "https://system.gotsport.com/org_event/events/54578/schedules?team=4151617",
                    "export_url": "https://system.gotsport.com/org_event/events/54578/matches_export?team=4151617",
                },
            }],
        }
        self.save_manifest()
        self.xlsx = self.data_dir / "dv7-ny-b2015-16.xlsx"
        # The validator checks inventory, not workbook contents.
        self.xlsx.touch()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")

    def check_error(self, phrase):
        errors, _ = validate(self.manifest_path)
        self.assertTrue(any(phrase in error for error in errors), errors)

    def test_valid_registration(self):
        before = self.manifest_path.read_bytes()
        errors, summary = validate(self.manifest_path)
        self.assertEqual(errors, [])
        self.assertEqual(summary, (1, 1))
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_unregistered_xlsx(self):
        (self.data_dir / "unregistered-team.xlsx").touch()
        self.check_error("Unregistered XLSX")

    def test_missing_xlsx(self):
        self.xlsx.unlink()
        self.check_error("Registered team missing XLSX")

    def test_duplicate_slug(self):
        duplicate = dict(self.manifest["teams"][0])
        duplicate["name"] = "Another Team"
        duplicate["gotsport"] = dict(duplicate["gotsport"])
        duplicate["gotsport"]["schedule_team_id"] = "999999"
        self.manifest["teams"].append(duplicate)
        self.save_manifest()
        self.check_error("Duplicate slug")

    def test_mismatched_gotsport_url(self):
        self.manifest["teams"][0]["gotsport"]["schedule_url"] = (
            "https://system.gotsport.com/org_event/events/54578/schedules?team=999999"
        )
        self.save_manifest()
        self.check_error("check event ID, team ID and URL")

    def test_malformed_manifest_json(self):
        self.manifest_path.write_text("{not valid JSON", encoding="utf-8")
        self.check_error("Cannot read manifest")

    def test_display_name_overrides_unchanged(self):
        before = self.manifest_path.read_bytes()
        errors, _ = validate(self.manifest_path)
        self.assertEqual(errors, [])
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertEqual(self.manifest["display_names"]["Other Team"], "Other")


if __name__ == "__main__":
    unittest.main()
