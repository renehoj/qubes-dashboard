"""Applications: the qube's menu, and qvm-run to start one of them."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qubes_dashboard import model  # noqa: E402
from qubes_dashboard.backend import BackendError  # noqa: E402
from qubes_dashboard.backend.appmenus import desktop_entries  # noqa: E402
from qubes_dashboard.backend.base import start_app_argv  # noqa: E402
from qubes_dashboard.backend.mock import MockBackend  # noqa: E402

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


def entry(vm, ident, name, icon="", extra=""):
    return (
        "[Desktop Entry]\n"
        f"Name={vm}: {name}\n"
        f"Exec=qvm-run -q -a --service -- {vm} qubes.StartApp+{ident}\n"
        f"Icon={icon}\n"
        f"X-Qubes-VmName={vm}\n"
        f"{extra}"
    )


class DesktopEntriesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, name, text):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_reads_the_qubes_entries(self):
        self.write("org.qubes-os.vm.work.firefox-esr.desktop",
                   entry("work", "firefox-esr", "Firefox ESR", "/icons/firefox.png",
                         "Comment=100% browser\nName[da]=Firefox\n"))
        self.write("sub/org.qubes-os.vm.work.org.gnome.Terminal.desktop",
                   entry("work", "org.gnome.Terminal", "Terminal"))
        self.write("org.qubes-os.vm.personal.firefox-esr.desktop",
                   entry("personal", "firefox-esr", "Firefox ESR"))
        self.write("broken.desktop", "not a desktop file\x00")
        self.write("plain.desktop", "[Desktop Entry]\nName=Plain\nExec=plain\n")
        self.assertEqual(desktop_entries("work", dirs=(self.dir, self.dir / "missing")), {
            "firefox-esr": ("Firefox ESR", "/icons/firefox.png"),
            "org.gnome.Terminal": ("Terminal", ""),
        })

    def test_start_app_argv_is_what_the_menu_runs(self):
        self.assertEqual(start_app_argv("work", "firefox-esr"),
                         ["qvm-run", "-q", "-a", "--service", "--", "work",
                          "qubes.StartApp+firefox-esr"])


class MockApplicationsTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def test_lists_a_menu(self):
        apps = self.backend.list_apps("work")
        self.assertIn("Terminal", [app.name for app in apps])
        self.assertEqual(self.backend.list_apps("dom0"), [])
        with self.assertRaises(BackendError):
            self.backend.list_apps("nonexistent")

    def test_starting_an_app_starts_a_halted_qube(self):
        self.backend.start_app("office", "org.gnome.Terminal")
        self.assertEqual(self.backend.apps_started, [("office", "org.gnome.Terminal")])
        deadline = time.monotonic() + 5
        while self.backend.power_states()["office"] != model.RUNNING:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


@unittest.skipIf(SKIP, SKIP)
class ApplicationsDialogTest(unittest.TestCase):
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
        self.app.backend.apps_started.clear()
        self.addCleanup(self.app.hide_window)

    def open(self, name):
        self.app._on_dialog_choose(self.app.qubes[name], model.APPLICATIONS)  # pylint: disable=protected-access
        dialog = self.app.dialog
        self.assertIsInstance(dialog, frontend.ApplicationsDialog)
        deadline = time.monotonic() + 5
        while dialog.apps is None:
            self.assertLess(time.monotonic(), deadline)
            drain()
        return dialog

    def test_lists_the_menu_over_the_list(self):
        dialog = self.open("work")
        self.assertTrue(self.app._covered())  # pylint: disable=protected-access
        self.assertEqual([app for app, _button in dialog.buttons],
                         self.app.backend.list_apps("work"))
        first = dialog.visible_buttons()[0]
        self.assertTrue(first.has_focus() or dialog.get_focus() is first)

    def test_typing_filters(self):
        dialog = self.open("work")
        dialog._set_query("calc")  # pylint: disable=protected-access
        self.assertEqual(len(dialog.visible_buttons()), 1)
        dialog._set_query("nothing like it")  # pylint: disable=protected-access
        self.assertEqual(dialog.visible_buttons(), [])
        self.assertIn("no match", dialog.subtitle.get_text())

    def test_launch_starts_the_app(self):
        dialog = self.open("work")
        app, _button = dialog.buttons[0]
        dialog.launch(app)
        self.assertIsNone(self.app.dialog)
        self.assertEqual(self.app.backend.apps_started, [("work", app.ident)])

    def test_a_failure_stays_in_the_list(self):
        dialog = self.open("work")
        start_app = self.app.backend.start_app

        def fail(_name, _ident):
            raise BackendError("Could not run qvm-run")
        self.app.backend.start_app = fail
        self.addCleanup(setattr, self.app.backend, "start_app", start_app)
        dialog.launch(dialog.buttons[0][0])
        self.assertIsNone(self.app.dialog)
        self.assertIn("Could not run qvm-run", self.app.footer.get_text())

    def test_cancel_goes_back(self):
        self.open("work").cancel()
        self.assertIsNone(self.app.dialog)
        self.assertEqual(self.app.backend.apps_started, [])

    def test_an_error_is_shown_in_the_dialog(self):
        dialog = self.open("work")
        dialog.set_error("No such qube: work")
        self.assertEqual(dialog.subtitle.get_text(), "No such qube: work")


if __name__ == "__main__":
    unittest.main()
