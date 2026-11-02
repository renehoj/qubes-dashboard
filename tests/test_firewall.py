"""Firewall: qubes-firewall's window in-process, or its command as fallback."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# qubes-firewall under tools/, as bin/qubes-dashboard uses it.
SIBLING = ROOT / "tools" / "qubes-firewall"
if (SIBLING / "qubes_firewall").is_dir():
    sys.path.append(str(SIBLING))

try:
    from qubes_dashboard import frontend
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    frontend = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = "" if frontend._firewall_window_class() else "qubes_firewall not importable"


class LazyImportTest(unittest.TestCase):
    def test_qubes_firewall_not_imported_at_top_level(self):
        tree = ast.parse((ROOT / "qubes_dashboard" / "frontend.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {getattr(n, "module", None) or n.names[0].name for n in top}
        self.assertFalse({n for n in names if n and n.startswith("qubes_firewall")})


@unittest.skipIf(SKIP, SKIP)
class InProcessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One application per process: a GApplication exports itself on the
        # session bus. NON_UNIQUE keeps it off a resident list's bus name.
        cls.app = frontend.DashboardApp()
        cls.app.set_flags(cls.app.get_flags() | frontend.Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)
        cls.app.backend_kind = "mock"
        cls.app.perform_setup()
        cls.app.backend.delay = 0

    @classmethod
    def tearDownClass(cls):
        cls.app.window.destroy()

    def setUp(self):
        self.app.qubes = {q.name: q for q in self.app.backend.list_qubes()}
        # Over the list, as with --keep-visible; without it the list closes.
        self.app.keep_visible = True
        self.addCleanup(self.app.hide_window)

    def test_dialog_choice_opens_over_the_list_and_returns(self):
        self.app._on_dialog_choose(self.app.qubes["work"], frontend.model.FIREWALL)  # pylint: disable=protected-access
        window = self.app.firewall_window
        self.assertIsNotNone(window)
        self.assertIs(window.get_transient_for(), self.app.window)
        self.assertEqual(window.qube_name, "work")
        self.assertTrue(self.app._covered())  # pylint: disable=protected-access
        self.assertEqual(self.app.backend.firewall_opened, [])  # not the command
        window.cancel()
        self.assertIsNone(self.app.firewall_window)

    def test_backend_follows_the_list(self):
        backend = self.app._get_firewall_backend()  # pylint: disable=protected-access
        self.assertTrue(backend.is_demo)
        self.assertIs(backend, self.app._get_firewall_backend())  # pylint: disable=protected-access

    def test_hiding_the_list_closes_the_firewall(self):
        self.app._open_firewall(self.app.qubes["office"])  # pylint: disable=protected-access
        self.app.hide_window()
        self.assertIsNone(self.app.firewall_window)

    def test_the_list_closes_and_leaves_the_firewall_alone(self):
        self.app.keep_visible = False
        self.app._present()  # pylint: disable=protected-access
        before = set(Gtk.Window.list_toplevels())
        self.app._on_dialog_choose(self.app.qubes["work"], frontend.model.FIREWALL)  # pylint: disable=protected-access
        (window,) = set(Gtk.Window.list_toplevels()) - before
        self.addCleanup(window.destroy)
        self.assertIsNone(window.get_transient_for())
        self.assertEqual(window.qube_name, "work")
        self.assertFalse(self.app.window.get_visible())
        self.assertIsNone(self.app.firewall_window)
        self.assertFalse(self.app._covered())  # pylint: disable=protected-access


@unittest.skipIf(frontend is None, SKIP)
class FallbackTest(unittest.TestCase):
    def test_runs_the_command_without_the_package(self):
        app = frontend.DashboardApp.__new__(frontend.DashboardApp)
        calls = []
        app.backend = type("B", (), {"open_firewall": lambda self, name: calls.append(name)})()
        app.hide_window = lambda: calls.append("hidden")
        original = frontend._firewall_window_class  # pylint: disable=protected-access
        frontend._firewall_window_class = lambda: None  # pylint: disable=protected-access
        try:
            app._open_firewall(frontend.model.Qube("work", "AppVM", "blue", "Running", "t"))  # pylint: disable=protected-access
        finally:
            frontend._firewall_window_class = original  # pylint: disable=protected-access
        self.assertEqual(calls, ["work", "hidden"])


if __name__ == "__main__":
    unittest.main()
