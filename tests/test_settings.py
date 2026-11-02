"""Settings: qubes-settings' window in-process, or a command as fallback."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# qubes-settings under tools/, as bin/qubes-dashboard uses it.
SIBLING = ROOT / "tools" / "qubes-settings"
if (SIBLING / "qubes_settings").is_dir():
    sys.path.append(str(SIBLING))

from qubes_dashboard.backend.base import SETTINGS_COMMANDS, settings_command  # noqa: E402

try:
    from qubes_dashboard import frontend
    from gi.repository import Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    frontend = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = "" if frontend._settings_window_class() else "qubes_settings not importable"


class SettingsCommandTest(unittest.TestCase):
    def test_own_command_first(self):
        self.assertEqual(settings_command(lambda c: "/usr/bin/" + c), "qubes-settings")

    def test_stock_command_next(self):
        which = {"qubes-vm-settings": "/usr/bin/qubes-vm-settings"}.get
        self.assertEqual(settings_command(which), "qubes-vm-settings")

    def test_stock_command_when_nothing_found(self):
        self.assertEqual(settings_command(lambda c: None), SETTINGS_COMMANDS[-1])


class LazyImportTest(unittest.TestCase):
    def test_qubes_settings_not_imported_at_top_level(self):
        import ast  # pylint: disable=import-outside-toplevel
        tree = ast.parse((ROOT / "qubes_dashboard" / "frontend.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {getattr(n, "module", None) or n.names[0].name for n in top}
        self.assertFalse({n for n in names if n and n.startswith("qubes_settings")})


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

    def test_opens_over_the_list_and_returns(self):
        qube = self.app.qubes["work"]
        self.app._open_settings(qube)  # pylint: disable=protected-access
        window = self.app.settings_window
        self.assertIsNotNone(window)
        self.assertIs(window.get_transient_for(), self.app.window)
        self.assertEqual(self.app.backend.settings_opened, [])  # not the command
        window.cancel()
        self.assertIsNone(self.app.settings_window)

    def test_hiding_the_list_closes_settings(self):
        self.app._open_settings(self.app.qubes["office"])  # pylint: disable=protected-access
        self.app.hide_window()
        self.assertIsNone(self.app.settings_window)

    def test_demo_states_follow_the_list(self):
        self.app.qubes["work"] = frontend.dataclasses.replace(
            self.app.qubes["work"], state="Halted")
        self.app._open_settings(self.app.qubes["work"])  # pylint: disable=protected-access
        backend = self.app._get_settings_backend()  # pylint: disable=protected-access
        self.assertTrue(backend.get_settings("work").is_halted)
        self.app.settings_window.dismiss()


if __name__ == "__main__":
    unittest.main()
