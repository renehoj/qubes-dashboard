"""The headless CLI against the demo backend."""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_backup import cli  # noqa: E402


def run(*argv):
    out = io.StringIO()
    code = cli.main(["--mock", *argv], out=out)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_halted_qube(self):
        code, out = run("vault", "--commands")
        self.assertEqual(code, 0)
        self.assertIn("vault: AppVM, Halted", out)
        self.assertIn("+ Revert", out)
        self.assertIn("qvm-backup --yes --passphrase-file - --dest-vm sys-usb", out)
        self.assertIn("qvm-volume revert vault:private ", out)

    def test_running_qube(self):
        code, out = run("work")
        self.assertEqual(code, 0)
        self.assertIn("- Revert", out)
        self.assertIn("  none", out)

    def test_backups(self):
        code, out = run("x", "--backups", "sys-usb:/media/usb/backups")
        self.assertEqual(code, 0)
        self.assertEqual(out.split(), ["qubes-backup-2026-10-19T201103",
                                       "qubes-backup-2026-10-12T091544"])

    def test_unknown_qube(self):
        code, _out = run("nope")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
