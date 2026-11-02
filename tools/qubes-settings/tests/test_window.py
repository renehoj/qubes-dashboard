"""The GTK window against the demo backend. Skipped without GTK 3 or a display."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_settings import window
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    window = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_settings import model  # noqa: E402
from qubes_settings.backend.mock import MockBackend  # noqa: E402


def press(widget, keyval):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    event.state = 0
    widget.emit("key-press-event", event)


def wait_until(condition, timeout=5.0):
    """Run the main loop until ``condition()`` holds; worker threads report
    back through it, at no fixed number of iterations."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        drain()
        time.sleep(0.01)
    drain()


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


@unittest.skipIf(window is None, SKIP)
class WindowTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)
        self.closed = []

    def open(self, name):
        win = window.SettingsWindow(
            self.backend, name, settings=self.backend.get_settings(name),
            on_close=self.closed.append,
        )
        self.addCleanup(win.dismiss)
        return win

    def test_edits_become_changes(self):
        win = self.open("office")
        self.assertEqual(win.changes(), {})
        win.fields[model.NETVM].set_active_id("sys-vpn")
        win.fields[model.LABEL].set_active_id("green")
        win.fields[model.VCPUS].set_value(4)
        win.fields[model.INCLUDE_IN_BACKUPS].set_active(False)
        self.assertEqual(win.changes(), {
            "netvm": "sys-vpn", "label": "green", "vcpus": 4,
            "include_in_backups": False,
        })

    def test_running_qube_greys_out(self):
        win = self.open("work")
        self.assertFalse(win.fields[model.TEMPLATE].get_sensitive())
        self.assertFalse(win.fields[model.MEMORY].get_sensitive())
        self.assertTrue(win.fields[model.NETVM].get_sensitive())

    def test_dom0(self):
        win = self.open("dom0")
        self.assertEqual(set(win.fields), {model.LABEL, model.INCLUDE_IN_BACKUPS})
        self.assertFalse(win.fields[model.LABEL].get_sensitive())

    def test_save_applies_and_closes(self):
        win = self.open("office")
        win.fields[model.NETVM].set_active_id(model.NONE_ID)
        win.save()
        wait_until(lambda: self.closed)
        self.assertEqual(self.closed, [True])
        self.assertIsNone(self.backend.get_settings("office").netvm.value)

    def test_save_without_changes_just_closes(self):
        win = self.open("office")
        win.save()
        self.assertEqual(self.closed, [False])

    def test_cancel_reports_once(self):
        win = self.open("office")
        win.cancel()
        win.cancel()
        self.assertEqual(self.closed, [False])

    def test_shown_once_loaded(self):
        win = window.SettingsWindow(self.backend, "office", on_close=self.closed.append)
        self.addCleanup(win.dismiss)
        self.assertFalse(win.get_visible())
        wait_until(lambda: win.settings is not None)
        self.assertTrue(win.get_visible())
        self.assertIn(model.NETVM, win.fields)

    def test_load_error_is_shown(self):
        win = window.SettingsWindow(self.backend, "nope", on_close=self.closed.append)
        self.addCleanup(win.dismiss)
        wait_until(win.get_visible)
        self.assertIn("No such qube", win.footer.get_text())
        win.save()  # nothing loaded: does nothing
        self.assertEqual(self.closed, [])

    def test_chip_removes_application(self):
        win = self.open("office")
        before = list(win.menu_items)
        win.chips[0].clicked()
        self.assertEqual(len(win.menu_items), len(before) - 1)
        self.assertEqual(list(win.changes()), ["menu_items"])
        self.assertIs(win.get_focus(), win.chips[0])

    def test_picker_space_selects_and_close_keeps(self):
        win = self.open("office")
        win.open_picker()
        picker = win.picker
        self.assertEqual(picker.selected(), model.ordered_menu_items(
            win.settings, win.menu_items))
        picker.search_entry.set_text("calcul")
        picker.search_entry.emit("search-changed")  # GTK sends it after a delay
        self.assertEqual(picker._cursor.entry.name, "Calculator")
        press(picker, Gdk.KEY_space)
        press(picker, Gdk.KEY_Escape)  # clears the search
        self.assertEqual(picker.search_entry.get_text(), "")
        press(picker, Gdk.KEY_Escape)  # closes, keeping the choice
        self.assertIsNone(win.picker)
        self.assertIn("org.gnome.Calculator.desktop", win.changes()["menu_items"])

    def test_picker_lists_chosen_first(self):
        win = self.open("office")
        win.open_picker()
        chosen = [row.chosen for row in win.picker.rows]
        self.assertEqual(chosen, sorted(chosen, reverse=True))
        win.picker.close_picker()

    def test_cancel_closes_picker_too(self):
        win = self.open("office")
        win.open_picker()
        picker = win.picker
        win.dismiss()
        self.assertTrue(picker._closed)

    def test_dismiss_does_not_report(self):
        win = self.open("office")
        win.dismiss()
        self.assertEqual(self.closed, [])


if __name__ == "__main__":
    unittest.main()
