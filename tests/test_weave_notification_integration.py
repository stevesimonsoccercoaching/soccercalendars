import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import watch_weave_calendar as watcher


class WeaveNotificationIntegrationTests(unittest.TestCase):
    def calendar(self, location):
        return (
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\n"
            "UID:match-1\n"
            "DTSTART:20261129T150000Z\n"
            "DTEND:20261129T170000Z\n"
            f"LOCATION:{location}\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )

    def run_watcher(self, state, report, output, feed):
        argv = [
            "watch_weave_calendar.py",
            "--state", str(state),
            "--report", str(report),
            "--name", "SFFK Test",
        ]

        with patch.object(sys, "argv", argv):
            with patch.dict(os.environ, {
                "WEAVE_ICAL_URL": "https://example.invalid/team.ics",
                "GITHUB_OUTPUT": str(output),
            }):
                with patch.object(watcher, "fetch_calendar", return_value=feed):
                    self.assertEqual(watcher.main(), 0)

        return output.read_text(), report.read_text()

    def test_change_generates_identifier_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            report = root / "report.md"
            output = root / "output.txt"

            previous = watcher.parse_feed(self.calendar("Field 1"))
            watcher.save_state(state, "SFFK Test", previous)

            outputs, markdown = self.run_watcher(
                state, report, output, self.calendar("Field 2")
            )

            match = re.search(
                r"^notification_id=([0-9a-f]{24})$",
                outputs,
                re.MULTILINE,
            )
            self.assertIsNotNone(match)
            self.assertIn("changed=true", outputs)
            self.assertIn(
                f"<!-- weave-notification-id:{match.group(1)} -->",
                markdown,
            )
            self.assertIn("Field 2", markdown)

    def test_retry_uses_same_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            report = root / "report.md"
            output = root / "output.txt"

            previous = watcher.parse_feed(self.calendar("Field 1"))
            watcher.save_state(state, "SFFK Test", previous)
            baseline = state.read_bytes()

            first, _ = self.run_watcher(
                state, report, output, self.calendar("Field 2")
            )

            state.write_bytes(baseline)
            output.write_text("")

            second, _ = self.run_watcher(
                state, report, output, self.calendar("Field 2")
            )

            pattern = r"notification_id=([0-9a-f]{24})"
            self.assertEqual(
                re.search(pattern, first).group(1),
                re.search(pattern, second).group(1),
            )


if __name__ == "__main__":
    unittest.main()
