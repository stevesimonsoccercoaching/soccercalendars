"""Regression tests for the safety-checked multi-league build dispatcher.

From the repository root:
    python -m unittest discover -s tests -p 'test_run_soccer_builds.py' -v

All execution tests use disposable Git repositories and fake builders. They
never alter the user's calendars, GitHub settings, or real revision state.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCH_SOURCE = REPO_ROOT / "scripts" / "run_soccer_builds.py"
SCRIPTS = REPO_ROOT / "scripts"


class SoccerDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        for name in (
            "run_soccer_builds.py", "plan_soccer_builds.py",
            "discover_soccer_manifests.py", "validate_soccer_manifest.py",
        ):
            shutil.copyfile(SCRIPTS / name, self.root / "scripts" / name)
        self.log = self.root / "dispatch-calls.txt"
        self.fake_builders()

    def fake_builders(self):
        # These deliberately do not build ICS; the test verifies dispatcher
        # selection, ordering and failure barriers with no real mutations.
        fake = (
            "from pathlib import Path\n"
            "import sys\n"
            "with Path('dispatch-calls.txt').open('a', encoding='utf-8') as log:\n"
            "    log.write(Path(__file__).stem + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        )
        for name in (
            "build_cjsl_calendars.py", "build_soccer_calendars.py",
            "sync_missing_soccer_matches.py", "build_calendar_index.py",
        ):
            (self.root / "scripts" / name).write_text(fake, encoding="utf-8")

    def register(self, *, cjsl=True, edp=True, invalid_profile=False):
        if cjsl:
            self.make_event("2026/cjsl/fall", "CJSL", "55206", "cjsl-team")
        if edp:
            self.make_event("2026-27/edp-north-atlantic", "EDP", "54578", "edp-team",
                            metadata=True)
        if invalid_profile:
            path = self.root / "config/2026/cjsl/fall/manifest.json"
            m = json.loads(path.read_text(encoding="utf-8"))
            m["competition"]["calendar_profile"] = "unknown-profile"
            path.write_text(json.dumps(m), encoding="utf-8")

    def make_event(self, relative, organizer, event_id, slug, *, metadata=False):
        got = {
            "schedule_team_id": "12345", "export_team_id": "12345",
            "schedule_url": f"https://system.gotsport.com/org_event/events/{event_id}/schedules?team=12345",
            "export_url": f"https://system.gotsport.com/org_event/events/{event_id}/matches_export?team=12345",
        }
        competition = {"name": f"{organizer} event", "organizer": organizer,
                       "provider": "GotSport", "event_id": event_id}
        if metadata:
            competition.update({"status_mode": "metadata",
                                "missing_match_policy": "preserve-warning",
                                "schedule_safety": {"reject_empty_export": True,
                                                    "max_missing_fraction": 0.5}})
        manifest = self.root / "config" / relative / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"competition": competition,
                                       "teams": [{"name": f"{organizer} Team", "slug": slug,
                                                  "gotsport": got}]}), encoding="utf-8")
        input_path = self.root / "data" / relative / f"{slug}.xlsx"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.touch()  # Inventory validator only; fake builders don't parse XLSX.
        return input_path

    def run_dispatch(self, *args):
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/run_soccer_builds.py"),
             "--root", str(self.root), *args],
            cwd=self.root, text=True, capture_output=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout.strip()

    def history(self):
        self.git("init", "-q")
        self.git("config", "user.name", "CI Test")
        self.git("config", "user.email", "test@example.com")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        base = self.git("rev-parse", "HEAD")
        (self.root / "marker.txt").write_text("second revision\n", encoding="utf-8")
        self.git("add", "marker.txt")
        self.git("commit", "-qm", "later commit")
        head = self.git("rev-parse", "HEAD")
        return base, head

    def test_preview_plans_two_builders_one_sync_and_one_index(self):
        self.register()
        p = self.run_dispatch()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Validated 2 events / 2 teams", p.stdout)
        self.assertIn("PLAN CJSL event [build]", p.stdout)
        self.assertIn("PLAN EDP event [preserve-missing]", p.stdout)
        self.assertIn("PLAN EDP event [build]", p.stdout)
        self.assertEqual(p.stdout.count("[index]"), 1)
        self.assertFalse(self.log.exists())

    def test_preview_does_not_change_any_repository_files(self):
        self.register()
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        p = self.run_dispatch()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_invalid_profile_rejected_before_any_build(self):
        self.register(invalid_profile=True)
        p = self.run_dispatch()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unsupported calendar_profile", p.stderr)
        self.assertFalse(self.log.exists())

    def test_missing_workbook_rejected_before_any_build(self):
        self.register()
        (self.root / "data/2026-27/edp-north-atlantic/edp-team.xlsx").unlink()
        p = self.run_dispatch()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Manifest validation failed", p.stderr)
        self.assertFalse(self.log.exists())

    def test_metadata_execution_requires_explicit_git_comparison(self):
        self.register()
        p = self.run_dispatch("--execute")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("explicit --base and --head", p.stderr)
        self.assertFalse(self.log.exists())

    def test_invalid_git_ref_rejected(self):
        self.register()
        _, head = self.history()
        p = self.run_dispatch("--execute", "--base", "not-a-commit", "--head", head)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Git preflight failed", p.stderr)
        self.assertFalse(self.log.exists())

    def test_head_must_be_checked_out(self):
        self.register()
        base, _ = self.history()
        p = self.run_dispatch("--execute", "--base", base, "--head", base)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("checked-out commit", p.stderr)
        self.assertFalse(self.log.exists())

    def test_base_must_be_ancestor(self):
        self.register()
        base, head = self.history()
        p = self.run_dispatch("--execute", "--base", head, "--head", base)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ancestor", p.stderr)
        self.assertFalse(self.log.exists())

    def test_modified_edp_workbook_is_rejected(self):
        self.register()
        base, head = self.history()
        (self.root / "data/2026-27/edp-north-atlantic/edp-team.xlsx").write_bytes(b"changed")
        p = self.run_dispatch("--execute", "--base", base, "--head", head)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("working-tree XLSX differs", p.stderr)
        self.assertFalse(self.log.exists())

    def test_event_added_after_base_is_rejected(self):
        self.register(cjsl=True, edp=False)
        self.git("init", "-q")
        self.git("config", "user.name", "CI Test")
        self.git("config", "user.email", "test@example.com")
        self.git("add", ".")
        self.git("commit", "-qm", "before edp")
        base = self.git("rev-parse", "HEAD")
        self.register(cjsl=False, edp=True)
        self.git("add", ".")
        self.git("commit", "-qm", "add edp")
        head = self.git("rev-parse", "HEAD")
        p = self.run_dispatch("--execute", "--base", base, "--head", head)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("absent at revision", p.stderr)
        self.assertFalse(self.log.exists())

    def test_successful_execution_runs_sync_before_edp_builder(self):
        self.register()
        base, head = self.history()
        p = self.run_dispatch("--execute", "--base", base, "--head", head)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([v.split(" ", 1)[0] for v in calls], [
            "build_cjsl_calendars", "sync_missing_soccer_matches",
            "build_soccer_calendars", "build_calendar_index",
        ])
        self.assertIn("--base " + base, calls[1])
        self.assertIn("--head " + head, calls[1])
        self.assertEqual(calls[-1], "build_calendar_index ")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_explicit_status_only_can_execute_without_git_history(self):
        self.register(edp=False)
        p = self.run_dispatch("--execute")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([v.split(" ", 1)[0] for v in calls],
                         ["build_cjsl_calendars", "build_calendar_index"])


if __name__ == "__main__":
    unittest.main()
