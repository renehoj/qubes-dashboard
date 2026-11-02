"""The headless listing."""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_firewall import cli  # noqa: E402


def run(*argv):
    out = io.StringIO()
    code = cli.main(["--mock", *argv], out=out)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_table(self):
        code, text = run("work")
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "work: net qube sys-firewall · 6 rules")
        self.assertTrue(lines[1].startswith("#  Action  Destination"))
        self.assertIn("mail.example.com", lines[4])
        self.assertEqual(lines[-1], "Everything else is blocked.")

    def test_raw(self):
        _code, text = run("office", "--raw")
        self.assertEqual(text, "action=drop proto=tcp dstports=25-25 comment=No direct mail\n"
                               "action=accept\n")

    def test_warnings(self):
        _code, text = run("browser-untrusted")
        self.assertIn("! rule 1", text)

    def test_unknown_qube(self):
        code, _text = run("nope")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
