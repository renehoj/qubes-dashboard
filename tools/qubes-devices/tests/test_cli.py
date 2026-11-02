"""The headless listing."""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_devices import cli  # noqa: E402


def run(*argv):
    out = io.StringIO()
    code = cli.main(["--mock", *argv], out=out)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_table(self):
        code, text = run("games")
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "games: halted · HVM · 3 devices assigned")
        self.assertTrue(lines[1].split() == ["Address", "Class", "Device", "Reset", "Used", "by"])
        self.assertTrue(lines[2].startswith("*  00:01.0 -> 00:00.0  Display"))
        self.assertEqual(len(lines), 2 + 15)

    def test_assigned_only(self):
        _code, text = run("sys-net", "--assigned")
        lines = text.splitlines()
        self.assertEqual(len(lines), 2 + 2 + 1)
        self.assertIn("sys-net is running", lines[-1])

    def test_raw(self):
        _code, text = run("sys-usb", "--raw")
        self.assertEqual(text, "qvm-pci assign --required sys-usb "
                               "dom0:00_14.0:8086:7a60::p0c0330\n")

    def test_warnings(self):
        _code, text = run("sys-usb")
        self.assertNotIn("! ", text)

    def test_unknown_qube(self):
        code, _text = run("nope")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
