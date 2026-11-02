"""The headless frontend prints the window's fields and applies --set."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_settings import cli  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["--mock", *argv])
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_show(self):
        code, out, _err = run("work")
        self.assertEqual(code, 0)
        self.assertIn("AppVM · Running", out)
        self.assertIn("read-only", out)

    def test_set(self):
        code, out, _err = run("office", "--set", "netvm=none", "--set", "vcpus=4")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"Networking\s+\(none\)")
        self.assertRegex(out, r"VCPUs\s+4")

    def test_set_menu_items(self):
        code, out, _err = run("office", "--set", "menu_items=xterm.desktop,yelp.desktop")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"Applications\s+Help, XTerm")

    def test_set_refused_while_running(self):
        code, _out, err = run("work", "--set", "memory=800")
        self.assertEqual(code, 2)
        self.assertIn("shut down", err)

    def test_unknown_qube(self):
        code, _out, err = run("nope")
        self.assertEqual(code, 1)
        self.assertIn("No such qube", err)


if __name__ == "__main__":
    unittest.main()
