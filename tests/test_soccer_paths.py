"""Unit tests for stable public URLs and event storage path resolution.

Run from repository root:
    python -m unittest discover -s tests -p 'test_soccer_paths.py' -v

Uses disposable directories; does not change real calendars or GitHub files.
"""

import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from soccer_paths import SoccerEventPaths, resolve_event_paths


class SoccerPathTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="soccer-path-tests-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def resolve(self, location, publication=None):
        competition = {}
        if publication is not None:
            competition["publication_path"] = publication
        return resolve_event_paths(location, competition, repo_root=self.root)

    def test_cjsl_legacy_fallback_keeps_existing_paths(self):
        result = self.resolve("config/2026/cjsl/fall/manifest.json")
        self.assertEqual(result.event_relative, Path("2026/cjsl/fall"))
        self.assertEqual(result.publication_relative, Path("2026/cjsl/fall"))
        self.assertEqual(result.data_dir, self.root / "data/2026/cjsl/fall")
        self.assertEqual(result.state_dir, self.root / "state/2026/cjsl/fall")
        self.assertEqual(result.docs_dir, self.root / "docs/2026/cjsl/fall")

    def test_edp_legacy_fallback_keeps_existing_paths(self):
        result = self.resolve("config/2026-27/edp-north-atlantic/manifest.json")
        self.assertEqual(result.docs_dir, self.root / "docs/2026-27/edp-north-atlantic")
        self.assertEqual(result.data_dir, self.root / "data/2026-27/edp-north-atlantic")
        self.assertEqual(result.state_dir, self.root / "state/2026-27/edp-north-atlantic")

    def test_canonical_event_defaults_to_canonical_publication(self):
        location = "config/2026-27/cjsl/league/events/55206/manifest.json"
        result = self.resolve(location)
        suffix = Path("2026-27/cjsl/league/events/55206")
        self.assertEqual(result.event_relative, suffix)
        self.assertEqual(result.publication_relative, suffix)
        self.assertEqual(result.docs_dir, self.root / "docs" / suffix)

    def test_canonical_event_can_keep_legacy_public_url(self):
        location = "config/2026-27/cjsl/league/events/55206/manifest.json"
        result = self.resolve(location, "2026/cjsl/fall")
        self.assertEqual(result.data_dir, self.root / "data/2026-27/cjsl/league/events/55206")
        self.assertEqual(result.state_dir, self.root / "state/2026-27/cjsl/league/events/55206")
        self.assertEqual(result.docs_dir, self.root / "docs/2026/cjsl/fall")
        self.assertNotEqual(result.event_relative, result.publication_relative)

    def test_edp_canonical_event_can_keep_legacy_public_url(self):
        location = "config/2026-27/edp/north-atlantic/events/54578/manifest.json"
        result = self.resolve(location, "2026-27/edp-north-atlantic")
        self.assertEqual(result.docs_dir, self.root / "docs/2026-27/edp-north-atlantic")
        self.assertEqual(result.state_dir, self.root / "state/2026-27/edp/north-atlantic/events/54578")

    def test_absolute_manifest_under_config_is_supported(self):
        absolute = self.root / "config/2026/cjsl/fall/manifest.json"
        result = self.resolve(absolute)
        self.assertEqual(result.manifest, absolute)
        self.assertEqual(result.docs_dir, self.root / "docs/2026/cjsl/fall")

    def test_invalid_publication_paths_are_rejected(self):
        invalid = (
            "", ".", "..", "../outside", "2026/../outside",
            "/tmp/outside", "2026//cjsl", "2026/cjsl/", "./2026/cjsl",
            "2026\\cjsl", "2026/CJSL", "2026/cjsl with spaces",
            "2026/cjsl/fall.ics", "https://example.com", "2026/cjsl?x=1",
            7, True, [], {},
        )
        location = "config/2026-27/cjsl/league/events/55206/manifest.json"
        for publication in invalid:
            with self.subTest(publication=publication):
                with self.assertRaises(ValueError):
                    resolve_event_paths(
                        location,
                        {"publication_path": publication},
                        repo_root=self.root,
                    )

    def test_manifest_outside_config_is_rejected(self):
        for location in (
            "config-backup/2026/cjsl/manifest.json",
            "data/2026/cjsl/fall/manifest.json",
            "config/../../outside/manifest.json",
            self.root.parent / "outside/manifest.json",
        ):
            with self.subTest(location=str(location)):
                with self.assertRaises(ValueError):
                    self.resolve(location)

    def test_manifest_filename_and_event_directory_are_required(self):
        for location in ("config/manifest.json", "config/cjsl/event.json"):
            with self.subTest(location=location):
                with self.assertRaises(ValueError):
                    self.resolve(location)

    def test_competition_must_be_an_object(self):
        for competition in (None, [], "", "CJSL", 123):
            with self.subTest(competition=competition):
                with self.assertRaises(ValueError):
                    resolve_event_paths(
                        "config/2026/cjsl/fall/manifest.json",
                        competition,
                        repo_root=self.root,
                    )

    def test_result_is_immutable_and_paths_are_absolute(self):
        result = self.resolve("config/2026/cjsl/fall/manifest.json")
        self.assertIsInstance(result, SoccerEventPaths)
        self.assertTrue(all(
            p.is_absolute() for p in
            (result.manifest, result.data_dir, result.state_dir, result.docs_dir)
        ))
        with self.assertRaises(FrozenInstanceError):
            result.docs_dir = self.root / "other"

    def test_resolver_does_not_create_directories_or_files(self):
        self.resolve("config/2026-27/cjsl/league/events/55206/manifest.json",
                     "2026/cjsl/fall")
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
