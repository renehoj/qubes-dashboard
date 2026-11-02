"""Devices: qubes-devices' window in-process, or its command as fallback."""

import ast
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# qubes-devices under tools/, as bin/qubes-dashboard uses it.
SIBLING = ROOT / "tools" / "qubes-devices"
if (SIBLING / "qubes_devices").is_dir():
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
    SKIP = "" if frontend._devices_window_class() else "qubes_devices not importable"


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


class LazyImportTest(unittest.TestCase):
    def test_qubes_devices_not_imported_at_top_level(self):
        tree = ast.parse((ROOT / "qubes_dashboard" / "frontend.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {getattr(n, "module", None) or n.names[0].name for n in top}
        self.assertFalse({n for n in names if n and n.startswith("qubes_devices")})


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
        self.app._get_devices_backend().delay = 0  # pylint: disable=protected-access
        self.addCleanup(self.app.hide_window)

    def open(self, name):
        self.app._on_dialog_choose(self.app.qubes[name], frontend.model.DEVICES)  # pylint: disable=protected-access
        window = self.app.devices_window
        for _ in range(500):
            if window.devices is not None:
                break
            drain()
            time.sleep(0.01)
        return window

    def test_dialog_choice_opens_over_the_list_and_returns(self):
        window = self.open("games")
        self.assertIsNotNone(window)
        self.assertIs(window.get_transient_for(), self.app.window)
        self.assertEqual(window.qube_name, "games")
        self.assertTrue(self.app._covered())  # pylint: disable=protected-access
        self.assertEqual(self.app.backend.devices_opened, [])  # not the command
        self.assertTrue(window.editable)
        window.cancel()
        self.assertIsNone(self.app.devices_window)

    def test_a_running_qube_is_shown_read_only(self):
        window = self.open("sys-net")
        self.assertFalse(window.editable)
        self.assertFalse(window.save_button.get_visible())

    def test_states_follow_the_list(self):
        # The list says games runs; the devices window must not offer edits.
        self.app.qubes["games"] = frontend.dataclasses.replace(
            self.app.qubes["games"], state=frontend.model.RUNNING)
        window = self.open("games")
        self.assertFalse(window.editable)

    def test_backend_follows_the_list(self):
        backend = self.app._get_devices_backend()  # pylint: disable=protected-access
        self.assertTrue(backend.is_demo)
        self.assertIs(backend, self.app._get_devices_backend())  # pylint: disable=protected-access

    def test_hiding_the_list_closes_the_devices(self):
        self.app._open_devices(self.app.qubes["office"])  # pylint: disable=protected-access
        self.app.hide_window()
        self.assertIsNone(self.app.devices_window)


@unittest.skipIf(frontend is None, SKIP)
class FallbackTest(unittest.TestCase):
    def test_runs_the_command_without_the_package(self):
        app = frontend.DashboardApp.__new__(frontend.DashboardApp)
        calls = []
        app.backend = type("B", (), {"open_devices": lambda self, name: calls.append(name)})()
        app.hide_window = lambda: calls.append("hidden")
        original = frontend._devices_window_class  # pylint: disable=protected-access
        frontend._devices_window_class = lambda: None  # pylint: disable=protected-access
        try:
            app._open_devices(frontend.model.Qube("games", "StandaloneVM", "green", "Halted"))  # pylint: disable=protected-access
        finally:
            frontend._devices_window_class = original  # pylint: disable=protected-access
        self.assertEqual(calls, ["games", "hidden"])


if __name__ == "__main__":
    unittest.main()
