"""qvm-appmenus output parsing and invocation, with a stand-in command."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_settings.backend import BackendError, appmenus  # noqa: E402

OUTPUT = """\
firefox-esr.desktop|Firefox ESR|Browse the World Wide Web
xterm.desktop|XTerm|
not-a-desktop-line
odd.desktop||Name missing
"""


class ParseTest(unittest.TestCase):
    def test_lines(self):
        entries = appmenus.parse_available(OUTPUT, [])
        self.assertEqual([(e.ident, e.name, e.comment) for e in entries], [
            ("firefox-esr.desktop", "Firefox ESR", "Browse the World Wide Web"),
            ("xterm.desktop", "XTerm", ""),
            ("odd.desktop", "odd.desktop", "Name missing"),
        ])

    def test_icon_from_first_dir_that_has_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            own, template = Path(tmp, "own"), Path(tmp, "template")
            own.mkdir()
            template.mkdir()
            (template / "xterm.png").write_bytes(b"")
            entries = appmenus.parse_available(OUTPUT, [str(own), str(template)])
        icons = {e.ident: e.icon for e in entries}
        self.assertEqual(icons["xterm.desktop"], str(template / "xterm.png"))
        self.assertEqual(icons["firefox-esr.desktop"], "")


class RunTest(unittest.TestCase):
    def fake(self, script):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: [os.remove(os.path.join(tmp, f)) for f in os.listdir(tmp)]
                        or os.rmdir(tmp))
        path = os.path.join(tmp, "qvm-appmenus")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n" + script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        old = appmenus.APPMENUS_COMMAND
        appmenus.APPMENUS_COMMAND = path
        self.addCleanup(setattr, appmenus, "APPMENUS_COMMAND", old)
        return tmp

    def test_stdin_and_args_reach_the_command(self):
        self.fake('echo "$@"; cat\n')
        out = appmenus.run_appmenus("--set-whitelist", "-", "work", stdin="a.desktop\n")
        self.assertEqual(out, "--set-whitelist - work\na.desktop\n")

    def test_failure_reports_last_stderr_line(self):
        self.fake('echo "Traceback" >&2; echo "qvm-appmenus: no such qube" >&2; exit 2\n')
        with self.assertRaisesRegex(BackendError, "^qvm-appmenus: no such qube$"):
            appmenus.run_appmenus("work")

    def test_get_available_pledges_unstable_format(self):
        # Without it, qvm-appmenus up to 4.3 exits with an error.
        self.assertIn("--i-understand-format-is-unstable", appmenus.GET_AVAILABLE_ARGS)
        self.assertIn("--file-field", appmenus.GET_AVAILABLE_ARGS)

    def test_missing_command(self):
        old = appmenus.APPMENUS_COMMAND
        appmenus.APPMENUS_COMMAND = "/nonexistent/qvm-appmenus"
        self.addCleanup(setattr, appmenus, "APPMENUS_COMMAND", old)
        with self.assertRaisesRegex(BackendError, "not installed"):
            appmenus.run_appmenus("work")


if __name__ == "__main__":
    unittest.main()
