"""Logs: qubes-logs's window in-process, or its command as fallback."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# qubes-logs under tools/, as bin/qubes-dashboard uses it.
SIBLING = ROOT / "tools" / "qubes-logs"
if (SIBLING / "qubes_logs").is_dir():
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
    SKIP = "" if frontend._logs_window_class() else "qubes_logs not importable"


class LazyImportTest(unittest.TestCase):
    def test_qubes_logs_not_imported_at_top_level(self):
        tree = ast.parse((ROOT / "qubes_dashboard" / "frontend.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {getattr(n, "module", None) or n.names[0].name for n in top}
        self.assertFalse({n for n in names if n and n.startswith("qubes_logs")})


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
        self.app._on_dialog_choose(self.app.qubes["work"], frontend.model.LOGS)  # pylint: disable=protected-access
        window = self.app.logs_window
        self.assertIsNotNone(window)
        self.assertIs(window.get_transient_for(), self.app.window)
        self.assertEqual(window.qube_name, "work")
        self.assertTrue(self.app._covered())  # pylint: disable=protected-access
        self.assertEqual(self.app.backend.logs_opened, [])  # not the command
        window.close_window()
        self.assertIsNone(self.app.logs_window)

    def test_opens_for_dom0(self):
        self.app._open_logs(self.app.qubes["dom0"])  # pylint: disable=protected-access
        self.assertEqual(self.app.logs_window.qube_name, "dom0")

    def test_backend_follows_the_list(self):
        backend = self.app._get_logs_backend()  # pylint: disable=protected-access
        self.assertTrue(backend.is_demo)
        self.assertIs(backend, self.app._get_logs_backend())  # pylint: disable=protected-access

    def test_dom0_has_the_hypervisor_log(self):
        self.app._open_logs(self.app.qubes["dom0"])  # pylint: disable=protected-access
        self.assertEqual([t.get_label() for t in self.app.logs_window.tabs], ["Hypervisor"])

    def test_hiding_the_list_closes_the_logs(self):
        self.app._open_logs(self.app.qubes["office"])  # pylint: disable=protected-access
        self.app.hide_window()
        self.assertIsNone(self.app.logs_window)


@unittest.skipIf(frontend is None, SKIP)
class FallbackTest(unittest.TestCase):
    def test_runs_the_command_without_the_package(self):
        app = frontend.DashboardApp.__new__(frontend.DashboardApp)
        calls = []
        app.backend = type("B", (), {"open_logs": lambda self, name: calls.append(name)})()
        app.hide_window = lambda: calls.append("hidden")
        original = frontend._logs_window_class  # pylint: disable=protected-access
        frontend._logs_window_class = lambda: None  # pylint: disable=protected-access
        try:
            app._open_logs(frontend.model.Qube("work", "AppVM", "blue", "Running", "t"))  # pylint: disable=protected-access
        finally:
            frontend._logs_window_class = original  # pylint: disable=protected-access
        self.assertEqual(calls, ["work", "hidden"])


if __name__ == "__main__":
    unittest.main()
