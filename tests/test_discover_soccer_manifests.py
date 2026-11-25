"""Regression tests for read-only multi-league manifest discovery.

From the repository root:
    python -m unittest discover -s tests -p "test_discover_soccer_manifests.py" -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from discover_soccer_manifests import discover


class ManifestDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()

    def write_manifest(self, relative_path, *, event_id="55206", organizer="CJSL",
                       provider="GotSport", teams=None):
        path = self.root / "config" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if teams is None:
            teams = [{"name": "Team A", "slug": "team-a"}]
        body = {
            "competition": {
                "event_id": event_id,
                "organizer": organizer,
                "provider": provider,
                "name": "Test competition",
            },
            "teams": teams,
        }
        path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        return path

    def test_current_legacy_cjsl_and_edp_layouts(self):
        self.write_manifest(
            "2026/cjsl/fall/manifest.json",
            teams=[{"name": f"CJSL {n}", "slug": f"cjsl-{n}"} for n in range(37)],
        )
        self.write_manifest(
            "2026-27/edp-north-atlantic/manifest.json",
            event_id="54578", organizer="EDP",
            teams=[{"name": f"EDP {n}", "slug": f"edp-{n}"} for n in range(3)],
        )
        records, errors, warnings = discover(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 2)
        self.assertEqual(sum(r["teams"] for r in records), 40)
        self.assertEqual({r["layout"] for r in records}, {"legacy"})
        self.assertEqual(len(warnings), 2)

    def test_new_canonical_layout(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json")
        records, errors, warnings = discover(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["layout"], "canonical")

    def test_two_events_in_one_competition(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json")
        self.write_manifest("2026-27/cjsl/league/events/66206/manifest.json", event_id="66206")
        records, errors, _ = discover(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 2)

    def test_duplicate_event_detected_across_old_and_new_paths(self):
        self.write_manifest("2026/cjsl/fall/manifest.json")
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json")
        _, errors, _ = discover(self.root)
        self.assertTrue(any("duplicate gotsport event 55206" in e for e in errors), errors)

    def test_duplicate_slugs_rejected_within_one_event(self):
        self.write_manifest(
            "2026-27/cjsl/league/events/55206/manifest.json",
            teams=[{"name": "A", "slug": "team-a"}, {"name": "B", "slug": "team-a"}],
        )
        _, errors, _ = discover(self.root)
        self.assertTrue(any("duplicate team slug" in e for e in errors), errors)

    def test_same_slug_allowed_in_different_events(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json")
        self.write_manifest("2026-27/cjsl/league/events/66206/manifest.json", event_id="66206")
        _, errors, _ = discover(self.root)
        self.assertEqual(errors, [])

    def test_event_id_path_mismatch(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json", event_id="99999")
        _, errors, _ = discover(self.root)
        self.assertTrue(any("directory event ID" in e for e in errors), errors)

    def test_organizer_path_mismatch(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json", organizer="EDP")
        _, errors, _ = discover(self.root)
        self.assertTrue(any("directory organizer" in e for e in errors), errors)

    def test_noncanonical_event_path_rejected(self):
        self.write_manifest("2026-27/cjsl/league/fall/manifest.json")
        _, errors, _ = discover(self.root)
        self.assertTrue(any("invalid event path" in e for e in errors), errors)

    def test_non_numeric_event_id_rejected(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json", event_id="abc")
        _, errors, _ = discover(self.root)
        self.assertTrue(any("ASCII digits" in e for e in errors), errors)

    def test_empty_teams_rejected(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json", teams=[])
        _, errors, _ = discover(self.root)
        self.assertTrue(any("teams must not be empty" in e for e in errors), errors)

    def test_malformed_json_rejected(self):
        path = self.root / "config/2026-27/cjsl/league/events/55206/manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text("{malformed", encoding="utf-8")
        _, errors, _ = discover(self.root)
        self.assertTrue(any("cannot read manifest" in e for e in errors), errors)

    def test_missing_config_rejected(self):
        (self.root / "config").rmdir()
        _, errors, _ = discover(self.root)
        self.assertTrue(any("Missing config/" in e for e in errors), errors)

    def test_no_manifests_rejected(self):
        _, errors, _ = discover(self.root)
        self.assertTrue(any("No manifests found" in e for e in errors), errors)

    def test_discovery_does_not_write_files(self):
        path = self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json")
        old_bytes = path.read_bytes()
        old_files = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        records, errors, _ = discover(self.root)
        new_files = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(path.read_bytes(), old_bytes)
        self.assertEqual(new_files, old_files)


    def test_two_events_cannot_share_publication_path(self):
        first = self.write_manifest(
            "2026-27/cjsl/league/events/55206/manifest.json"
        )
        second = self.write_manifest(
            "2026-27/cjsl/league/events/66206/manifest.json",
            event_id="66206",
        )
        for path in (first, second):
            body = json.loads(path.read_text(encoding="utf-8"))
            body["competition"]["publication_path"] = "2026/cjsl/fall"
            path.write_text(json.dumps(body), encoding="utf-8")

        _, errors, _ = discover(self.root)
        self.assertTrue(
            any("public directory collision" in e for e in errors),
            errors,
        )

    def test_explicit_path_cannot_claim_legacy_directory(self):
        self.write_manifest("2026/cjsl/fall/manifest.json")
        other = self.write_manifest(
            "2026-27/edp/north-atlantic/events/54578/manifest.json",
            event_id="54578",
            organizer="EDP",
        )
        body = json.loads(other.read_text(encoding="utf-8"))
        body["competition"]["publication_path"] = "2026/cjsl/fall"
        other.write_text(json.dumps(body), encoding="utf-8")

        _, errors, _ = discover(self.root)
        self.assertTrue(
            any("public directory collision" in e for e in errors),
            errors,
        )

    def test_distinct_publication_directories_are_allowed(self):
        first = self.write_manifest(
            "2026-27/cjsl/league/events/55206/manifest.json"
        )
        second = self.write_manifest(
            "2026-27/cjsl/league/events/66206/manifest.json",
            event_id="66206",
        )
        for path, public_dir in (
            (first, "2026/cjsl/fall"),
            (second, "2026/cjsl/spring"),
        ):
            body = json.loads(path.read_text(encoding="utf-8"))
            body["competition"]["publication_path"] = public_dir
            path.write_text(json.dumps(body), encoding="utf-8")

        records, errors, _ = discover(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 2)

    def test_invalid_publication_directories_are_rejected(self):
        path = self.write_manifest(
            "2026-27/cjsl/league/events/55206/manifest.json"
        )
        for invalid in (
            "../outside", "/absolute", "2026//cjsl",
            "2026/CJSL", "2026\\cjsl", "",
        ):
            with self.subTest(publication=invalid):
                body = json.loads(path.read_text(encoding="utf-8"))
                body["competition"]["publication_path"] = invalid
                path.write_text(json.dumps(body), encoding="utf-8")

                _, errors, _ = discover(self.root)
                self.assertTrue(
                    any("invalid competition.publication_path" in e
                        for e in errors),
                    errors,
                )


if __name__ == "__main__":
    unittest.main()
