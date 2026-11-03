"""Log paths, reading tails, filtering and highlighting."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_logs import model  # noqa: E402


class LogFilesTest(unittest.TestCase):
    def test_a_qube_has_three(self):
        self.assertEqual([(f.title, f.path) for f in model.log_files("work")], [
            ("Console", "/var/log/xen/console/guest-work.log"),
            ("GUI", "/var/log/qubes/guid.work.log"),
            ("qrexec", "/var/log/qubes/qrexec.work.log"),
        ])

    def test_dom0_has_the_hypervisor(self):
        self.assertEqual([f.path for f in model.log_files("dom0")],
                         ["/var/log/xen/console/hypervisor.log"])

    def test_not_a_qube_name(self):
        for name in ("../../etc/shadow", "work/../x", "", "-work", "a" * 32):
            with self.assertRaises(ValueError, msg=name):
                model.log_files(name)


class CleanTest(unittest.TestCase):
    def test_colours_go(self):
        self.assertEqual(model.clean("[\x1b[0;32m  OK  \x1b[0m] Started x"), "[  OK  ] Started x\n")

    def test_carriage_returns(self):
        self.assertEqual(model.clean("a\r\nb\n"), "a\nb\n")
        self.assertEqual(model.clean("progress 10%\rprogress 100%\n"), "progress 100%\n")
        self.assertEqual(model.clean("login: \r\n"), "login: \n")

    def test_controls_go_tabs_stay(self):
        self.assertEqual(model.clean("a\x07b\tc\x00"), "ab\tc\n")
        # C1 controls (\x9b is a one-byte CSI) and bidi overrides go too.
        self.assertEqual(model.clean("a\x9b31mb\u202ec\u2066d\n"), "a31mbcd\n")

    def test_blank_lines_go(self):
        text = ("[2026-10-24 11:38:42] Fedora Linux 41\n"
                "[2026-10-24 11:38:42] \n"
                "[2026-10-24 11:38:42]\x1b[0m   \r\n"
                "\n"
                "   \n"
                "[2026-10-24 11:38:43] login:\n")
        self.assertEqual(model.clean(text), "[2026-10-24 11:38:42] Fedora Linux 41\n"
                                            "[2026-10-24 11:38:43] login:\n")

    def test_brackets_alone_are_kept(self):
        self.assertEqual(model.clean("[    0.000000] x\n[gap]\n"), "[    0.000000] x\n[gap]\n")

    def test_decode_tail_drops_partial_line(self):
        self.assertEqual(model.decode_tail(b"tial line\nwhole\n", True), "whole\n")
        self.assertEqual(model.decode_tail(b"tial line\nwhole\n", False), "tial line\nwhole\n")
        self.assertEqual(model.decode_tail(b"no newline", True), "")

    def test_decode_tail_bad_utf8(self):
        self.assertEqual(model.decode_tail(b"caf\xe9\n", False), "caf�\n")


class FilterTest(unittest.TestCase):
    LINES = ["usb 2-1: error -71", "Started qrexec agent", "qrexec: denied", ""]

    def test_empty_query_keeps_all(self):
        self.assertEqual(model.filter_lines(self.LINES, "  "), self.LINES)

    def test_every_word_ignoring_case(self):
        self.assertEqual(model.filter_lines(self.LINES, "QREXEC"), self.LINES[1:3])
        self.assertEqual(model.filter_lines(self.LINES, "qrexec denied"), [self.LINES[2]])
        self.assertEqual(model.filter_lines(self.LINES, "zzz"), [])


class SeverityTest(unittest.TestCase):
    def test_errors(self):
        for line in ("usb 2-1: device descriptor read/64, error -71",
                     "[FAILED] Failed to start foo.service.",
                     "Kernel panic - not syncing",
                     "qubes.Filecopy: work -> @default: denied: no matching policy",
                     "Traceback (most recent call last):"):
            with self.subTest(line=line):
                self.assertEqual(model.severity(line), model.ERROR)

    def test_warnings(self):
        self.assertEqual(model.severity("WARNING: No ucode found"), model.WARNING)
        self.assertEqual(model.severity("cups-browsed: Warning: no avahi"), model.WARNING)

    def test_not_errors(self):
        for line in ("recovery complete; 0 errors found", "mounted with errors=remount-ro",
                     "qrexec_timeout=60", "[  OK  ] Started qubes-db.service", "terrorist"):
            with self.subTest(line=line):
                self.assertIsNone(model.severity(line))


class SummaryTest(unittest.TestCase):
    FILE = model.LogFile("GUI", "/var/log/qubes/guid.work.log")

    def test_bytes(self):
        self.assertEqual(model.format_bytes(512), "512 B")
        self.assertEqual(model.format_bytes(1536), "1.5 KiB")
        self.assertEqual(model.format_bytes(model.MAX_BYTES), "1.0 MiB")

    def test_summary(self):
        text = model.summary(model.LogText(self.FILE, "x\n", size=2048, mtime=None,
                                           truncated=True))
        self.assertEqual(text, "/var/log/qubes/guid.work.log · 2.0 KiB · last 1.0 MiB shown")

    def test_summary_of_an_error(self):
        text = model.summary(model.LogText(self.FILE, error="permission denied"))
        self.assertEqual(text, "/var/log/qubes/guid.work.log · permission denied")

    def test_as_text(self):
        self.assertEqual(model.as_text(["a", "b"]), "a\nb\n")
        self.assertEqual(model.as_text([]), "")


if __name__ == "__main__":
    unittest.main()
