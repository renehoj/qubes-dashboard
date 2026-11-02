"""The headless listing."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_information import cli  # noqa: E402


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(["--mock", *argv], out=out)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_whole_page(self):
        code, text = run("work")
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("Overview  (AppVM · Running"))
        for title in ("Live", "Properties", "Features", "Services", "Tags", "Storage",
                      "Firewall"):
            self.assertTrue(any(line.startswith(title) for line in lines), title)

    def test_filter(self):
        _code, text = run("work", "tags")
        self.assertEqual(text, "Tags  (2)\n  created-by-dom0\n  work-data\n")

    def test_nothing_matches(self):
        code, text = run("work", "zzz")
        self.assertEqual((code, text), (1, ""))

    def test_unknown_qube(self):
        code, _text = run("nope")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
