"""Tests for controlled soccer publication paths."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "scripts")
)

from soccer_publication_paths import expected_publication_paths


class PublicationPathTests(unittest.TestCase):

    def test_only_registered_outputs_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []

            cases = [
                (
                    "2026-27/cjsl/league/events/55206",
                    "2026/cjsl/fall",
                    "cjsl-team",
                    False,
                ),
                (
                    "2026-27/edp/edp-north-atlantic/events/54578",
                    "2026-27/edp-north-atlantic",
                    "edp-team",
                    True,
                ),
            ]

            for directory, publication, slug, preserve in cases:
                manifest = f"config/{directory}/manifest.json"
                path = root / manifest
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({
                    "competition": {
                        "publication_path": publication,
                    },
                    "teams": [{"slug": slug}],
                }), encoding="utf-8")

                events.append({
                    "manifest": manifest,
                    "missing_match_sync": preserve,
                })

            actual = set(expected_publication_paths(root, events))

            expected = {
                "docs/index.html",
                "docs/2026/cjsl/fall/cjsl-team.ics",
                "docs/2026-27/edp-north-atlantic/edp-team.ics",
                "state/2026-27/cjsl/league/events/55206/revisions.json",
                "state/2026-27/edp/edp-north-atlantic/events/54578/revisions.json",
                "state/2026-27/edp/edp-north-atlantic/events/54578/missing_matches.json",
                "state/2026-27/edp/edp-north-atlantic/events/54578/processing_checkpoint.json",
            }

            self.assertEqual(actual, expected)
            self.assertNotIn(
                "state/weave/sffk-2015-2016.json", actual
            )
            self.assertNotIn(
                "docs/2026/cjsl/fall/README.MD", actual
            )
            self.assertFalse(
                any(".checkpoint-" in path for path in actual)
            )

    def test_rejects_unsafe_and_duplicate_team_slugs(self):
        cases = [
            ["safe-team", "../weave"],
            ["same-team", "same-team"],
            ["team.json"],
            ["UPPERCASE"],
            [""],
        ]

        for slugs in cases:
            with self.subTest(slugs=slugs):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    manifest = (
                        "config/2026-27/cjsl/league/"
                        "events/55206/manifest.json"
                    )
                    path = root / manifest
                    path.parent.mkdir(parents=True)
                    path.write_text(
                        json.dumps({
                            "competition": {},
                            "teams": [
                                {"slug": slug} for slug in slugs
                            ],
                        }),
                        encoding="utf-8",
                    )

                    with self.assertRaises(ValueError):
                        expected_publication_paths(root, [{
                            "manifest": manifest,
                            "missing_match_sync": False,
                        }])

    def test_rejects_duplicate_publication_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = []

            for event_id in ("55206", "55207"):
                manifest = (
                    f"config/2026-27/cjsl/league/"
                    f"events/{event_id}/manifest.json"
                )
                path = root / manifest
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({
                        "competition": {
                            "publication_path": "shared/calendars"
                        },
                        "teams": [{"slug": f"team-{event_id}"}],
                    }),
                    encoding="utf-8",
                )
                events.append({
                    "manifest": manifest,
                    "missing_match_sync": False,
                })

            with self.assertRaisesRegex(
                ValueError, "Duplicate publication directory"
            ):
                expected_publication_paths(root, events)

if __name__ == "__main__":
    unittest.main()
