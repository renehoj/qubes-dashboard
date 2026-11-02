"""Markup helpers in the frontend. Skipped where GTK 3 is not installed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_dashboard import frontend
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    frontend = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_dashboard import model  # noqa: E402


@unittest.skipIf(frontend is None, SKIP)
class MarkupTest(unittest.TestCase):
    def test_highlight_bolds_every_match(self):
        self.assertEqual(
            frontend.highlight_markup("sys-sys", ["sys"]), "<b>sys</b>-<b>sys</b>"
        )

    def test_highlight_merges_overlaps_and_escapes(self):
        self.assertEqual(frontend.highlight_markup("a&bc", ["a&b", "bc"]), "<b>a&amp;bc</b>")

    def test_highlight_without_query(self):
        self.assertEqual(frontend.highlight_markup("work", []), "work")

    def test_pending_overrides_state(self):
        qube = model.Qube("x", "AppVM", "red", model.HALTED)
        self.assertIn("Starting…", frontend.status_markup(qube, model.START))

    def test_placeholder_template_is_dim(self):
        qube = model.Qube("t", "TemplateVM", "black", model.HALTED)
        self.assertIn('alpha="50%"', frontend.template_markup(qube))


@unittest.skipIf(frontend is None, SKIP)
class PlatformDataTest(unittest.TestCase):
    def data(self, **values):
        from gi.repository import GLib
        return GLib.Variant("a{sv}", values)

    def test_reads_strings(self):
        from gi.repository import GLib
        data = self.data(qube=GLib.Variant("s", "work"), search=GLib.Variant("s", "sys"))
        read = frontend.DashboardApp._string_from_platform_data
        self.assertEqual(read(data, "qube"), "work")
        self.assertEqual(read(data, "search"), "sys")

    def test_ignores_missing_and_non_strings(self):
        from gi.repository import GLib
        data = self.data(qube=GLib.Variant("i", 3))
        read = frontend.DashboardApp._string_from_platform_data
        self.assertEqual(read(data, "qube"), "")
        self.assertEqual(read(data, "search"), "")
        self.assertEqual(read(None, "qube"), "")


if __name__ == "__main__":
    unittest.main()
