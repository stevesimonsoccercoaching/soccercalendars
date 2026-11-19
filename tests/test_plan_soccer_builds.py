"""Read-only build-profile selection tests.

Run from repository root:
    python -m unittest discover -s tests -p 'test_plan_soccer_builds.py' -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from plan_soccer_builds import plan


class BuildPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()

    def write_manifest(self, name, *, event_id="55206", organizer="CJSL",
                       profile=None, provider="GotSport", status_mode=None,
                       missing_policy=None, safety=None, team_count=1):
        path = self.root / "config" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        c = {"event_id": event_id, "organizer": organizer, "provider": provider,
             "name": "Event", "calendar_profile": profile}
        if profile is None:
            del c["calendar_profile"]
        if status_mode is not None:
            c["status_mode"] = status_mode
        if missing_policy is not None:
            c["missing_match_policy"] = missing_policy
        if safety is not None:
            c["schedule_safety"] = safety
        body = {"competition": c, "teams": [
            {"name": f"Team {i}", "slug": f"team-{i}"} for i in range(team_count)
        ]}
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_legacy_cjsl_and_edp_profiles(self):
        self.write_manifest("2026/cjsl/fall/manifest.json", team_count=37)
        self.write_manifest("2026-27/edp-north-atlantic/manifest.json", event_id="54578",
                            organizer="EDP", status_mode="metadata",
                            missing_policy="preserve-warning",
                            safety={"reject_empty_export": True, "max_missing_fraction": .5},
                            team_count=3)
        events, errors, warnings = plan(self.root)
        self.assertFalse(errors, errors)
        self.assertEqual((len(events), sum(e["teams"] for e in events)), (2, 40))
        self.assertEqual({e["calendar_profile"] for e in events},
                         {"explicit-status-cjsl-v1", "metadata-warning-v1"})
        self.assertEqual(sum(e["missing_match_sync"] for e in events), 1)
        self.assertTrue(warnings)

    def test_canonical_explicit_profile(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json",
                            profile="explicit-status-cjsl-v1")
        events, errors, _ = plan(self.root)
        self.assertFalse(errors, errors)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["builder"], "scripts/build_cjsl_calendars.py")

    def test_canonical_requires_profile(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json")
        events, errors, _ = plan(self.root)
        self.assertEqual(events, [])
        self.assertTrue(any("calendar_profile is required" in e for e in errors), errors)

    def test_unknown_profile_rejected(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json", profile="random")
        events, errors, _ = plan(self.root)
        self.assertEqual(events, [])
        self.assertTrue(any("unsupported" in e for e in errors), errors)

    def test_metadata_profile_requires_preservation_policy(self):
        self.write_manifest("2026-27/edp/north-atlantic/events/54578/manifest.json",
                            organizer="EDP", event_id="54578", profile="metadata-warning-v1",
                            status_mode="metadata", safety={"reject_empty_export": True,
                                                            "max_missing_fraction": .5})
        _, errors, _ = plan(self.root)
        self.assertTrue(any("requires status_mode" in e for e in errors), errors)

    def test_metadata_profile_requires_safety_policy(self):
        self.write_manifest("2026-27/edp/north-atlantic/events/54578/manifest.json",
                            organizer="EDP", event_id="54578", profile="metadata-warning-v1",
                            status_mode="metadata", missing_policy="preserve-warning")
        _, errors, _ = plan(self.root)
        self.assertTrue(any("reject_empty_export" in e for e in errors), errors)

    def test_explicit_profile_conflict_rejected(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json",
                            profile="explicit-status-cjsl-v1", status_mode="metadata")
        _, errors, _ = plan(self.root)
        self.assertTrue(any("conflicts" in e for e in errors), errors)

    def test_non_gotsport_provider_rejected(self):
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json",
                            profile="explicit-status-cjsl-v1", provider="OtherProvider")
        _, errors, _ = plan(self.root)
        self.assertTrue(any("currently requires GotSport" in e for e in errors), errors)

    def test_duplicate_event_invalidates_whole_plan(self):
        self.write_manifest("2026/cjsl/fall/manifest.json")
        self.write_manifest("2026-27/cjsl/league/events/55206/manifest.json",
                            profile="explicit-status-cjsl-v1")
        events, errors, _ = plan(self.root)
        self.assertEqual(events, [])
        self.assertTrue(any("duplicate" in e for e in errors), errors)

    def test_read_only(self):
        f = self.write_manifest("2026/cjsl/fall/manifest.json")
        original = f.read_bytes()
        before = [p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()]
        events, errors, _ = plan(self.root)
        after = [p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()]
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(before, after)
        self.assertEqual(original, f.read_bytes())


if __name__ == "__main__":
    unittest.main()
