"""The headless listing."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_dashboard import cli  # noqa: E402


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli.main(["--mock", *args])
    return out.getvalue().splitlines()


class CliTest(unittest.TestCase):
    def test_default_lists_running_only(self):
        lines = run()
        self.assertEqual(lines[0].split(), ["NAME", "STATUS", "TEMPLATE"])
        self.assertTrue(all("Halted" not in line for line in lines))

    def test_all_and_query(self):
        lines = run("--all", "vault")
        self.assertEqual(len(lines), 2)
        self.assertIn("Halted", lines[1])

    def test_actions(self):
        lines = run("--all", "--actions", "vault")
        self.assertTrue(lines[1].endswith("start applications settings firewall devices information logs backup"))


if __name__ == "__main__":
    unittest.main()
