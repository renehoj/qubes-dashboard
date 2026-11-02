"""The headless listing."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_logs import cli  # noqa: E402


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(["--mock", *argv], out=out)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_console_by_default(self):
        code, text = run("work")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("[2026-10-24 11:38:42] Logfile Opened\n"))

    def test_other_log_and_filter(self):
        code, text = run("work", "--log", "qrexec", "denied")
        self.assertEqual(code, 0)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn("qubes.Filecopy", text)

    def test_list(self):
        code, text = run("work", "--list")
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[0] for line in text.splitlines()],
                         ["Console", "GUI", "qrexec"])

    def test_unknown_log(self):
        self.assertEqual(run("dom0", "--log", "gui"), (2, ""))

    def test_missing_file(self):
        self.assertEqual(run("nope"), (1, ""))

    def test_nothing_matches(self):
        self.assertEqual(run("work", "zzz"), (1, ""))


if __name__ == "__main__":
    unittest.main()
