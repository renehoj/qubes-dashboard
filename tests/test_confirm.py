"""Stop and Restart ask first; the other actions do not."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from qubes_dashboard import frontend
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    frontend = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""


@unittest.skipIf(SKIP, SKIP)
class ConfirmTest(unittest.TestCase):
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
        cls.app.keep_visible = True

    @classmethod
    def tearDownClass(cls):
        cls.app.window.destroy()

    def setUp(self):
        self.app.qubes = {q.name: q for q in self.app.backend.list_qubes()}
        self.app.pending.clear()
        self.addCleanup(self.app.hide_window)

    def choose(self, name, action):
        self.app._on_dialog_choose(self.app.qubes[name], action)  # pylint: disable=protected-access
        return self.app.dialog

    def test_stop_asks_with_the_focus_on_cancel(self):
        dialog = self.choose("work", frontend.model.STOP)
        self.assertIsInstance(dialog, frontend.ConfirmDialog)
        self.assertTrue(self.app._covered())  # pylint: disable=protected-access
        self.assertTrue(dialog.cancel_button.has_focus() or dialog.get_focus() is dialog.cancel_button)
        self.assertNotIn("work", self.app.pending)

    def test_cancel_leaves_the_qube_alone(self):
        self.choose("work", frontend.model.RESTART).cancel()
        self.assertIsNone(self.app.dialog)
        self.assertNotIn("work", self.app.pending)

    def test_confirm_runs_the_action(self):
        self.choose("work", frontend.model.STOP).confirm()
        self.assertIsNone(self.app.dialog)
        self.assertEqual(self.app.pending.get("work"), frontend.model.STOP)

    def test_start_does_not_ask(self):
        self.app.backend.shutdown("personal")
        self.app.qubes = {q.name: q for q in self.app.backend.list_qubes()}
        self.assertIsNone(self.choose("personal", frontend.model.START))
        self.assertEqual(self.app.pending.get("personal"), frontend.model.START)


if __name__ == "__main__":
    unittest.main()
