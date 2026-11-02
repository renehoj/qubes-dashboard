"""The GTK window against the demo backend. Skipped without GTK 3 or a display."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_devices import window
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    window = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_devices import model  # noqa: E402
from qubes_devices.backend.mock import MockBackend  # noqa: E402

GPU = "00_01.0-00_00.0"
GPU_AUDIO = "00_01.0-00_00.1"
SRAM = "00_14.2"


def press(widget, keyval, state=0):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    event.state = state
    event.window = widget.get_window()
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
        win = window.DevicesWindow(
            self.backend, name, devices=self.backend.get_devices(name),
            on_close=self.closed.append,
        )
        self.addCleanup(win.dismiss)
        return win

    def select(self, win, port_id):
        for row in win.store:
            if row[window.COL_PORT] == port_id:
                win.tree.set_cursor(row.path, None, False)
                return
        self.fail(f"{port_id} is not listed")

    def assigned(self, win):
        return {row[window.COL_PORT] for row in win.store if row[window.COL_ASSIGNED]}

    def test_loads_in_background(self):
        win = window.DevicesWindow(self.backend, "games", on_close=self.closed.append)
        self.addCleanup(win.dismiss)
        wait_until(lambda: win.session is not None)
        self.assertEqual(len(win.store), 15)
        self.assertEqual(win.selected(), GPU)
        self.assertIs(win.get_focus(), win.tree)
        self.assertTrue(win.save_button.get_visible())

    def test_unknown_qube_shows_the_error(self):
        win = window.DevicesWindow(self.backend, "nope", on_close=self.closed.append)
        self.addCleanup(win.dismiss)
        wait_until(lambda: "nope" in win.footer.get_text())
        self.assertIn("error", win.footer.get_style_context().list_classes())
        self.assertFalse(win.save_button.get_visible())

    def test_space_toggles(self):
        win = self.open("games")
        self.select(win, SRAM)
        press(win, Gdk.KEY_space)
        self.assertIn(SRAM, self.assigned(win))
        self.assertIn("not saved", win.subtitle.get_text())
        press(win, Gdk.KEY_space)
        self.assertNotIn(SRAM, self.assigned(win))
        self.assertNotIn("not saved", win.subtitle.get_text())

    def test_strict_reset(self):
        win = self.open("games")
        self.select(win, GPU)
        win.toggle_strict_reset()
        self.assertTrue(win.session.assignment(GPU).no_strict_reset)
        self.assertEqual(win.store[0][window.COL_CELLS + 3], "not strict")
        self.assertEqual(win.reset_button.get_label(), "Turn strict _reset on")
        self.select(win, SRAM)
        self.assertFalse(win.reset_button.get_sensitive())

    def test_running_qube_is_read_only(self):
        win = self.open("sys-net")
        self.assertFalse(win.save_button.get_visible())
        self.assertFalse(win.tools_box.get_visible())
        self.assertIn("Shut it down", win.note.get_text())
        self.select(win, SRAM)
        press(win, Gdk.KEY_space)
        self.assertNotIn(SRAM, self.assigned(win))
        win.accept()
        self.assertEqual(self.closed, [])
        press(win, Gdk.KEY_Escape)
        self.assertEqual(self.closed, [False])

    def test_dom0_is_read_only(self):
        win = self.open("dom0")
        self.assertFalse(win.save_button.get_visible())
        self.assertEqual(win.note.get_text(), model.ADMIN_NOTE)

    def test_filter(self):
        win = self.open("games")
        win.filter_entry.set_text("nvidia")
        drain()
        self.assertEqual([row[window.COL_PORT] for row in win.store], [GPU, GPU_AUDIO])
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.filter_entry.get_text(), "")
        self.assertEqual(self.closed, [])

    def test_typing_filters(self):
        win = self.open("games")
        win.show_all()
        drain()
        for key in (Gdk.KEY_f, Gdk.KEY_r, Gdk.KEY_e):
            press(win, key)
        self.assertEqual(win.filter_entry.get_text(), "fre")
        self.assertEqual([row[window.COL_PORT] for row in win.store], ["00_1c.1-00_00.0"])
        press(win, Gdk.KEY_Down)
        self.assertIs(win.get_focus(), win.tree)
        press(win, Gdk.KEY_space)
        self.assertNotIn("00_1c.1-00_00.0", self.assigned(win))

    def test_assigned_only(self):
        win = self.open("games")
        press(win, Gdk.KEY_o, Gdk.ModifierType.MOD1_MASK)
        self.assertEqual(len(win.store), 3)
        press(win, Gdk.KEY_o, Gdk.ModifierType.MOD1_MASK)
        self.assertEqual(len(win.store), 15)

    def test_save_without_changes_just_closes(self):
        win = self.open("games")
        win.accept()
        self.assertEqual(self.closed, [False])

    def test_save(self):
        win = self.open("games")
        self.select(win, "00_1c.1-00_00.0")
        win.toggle_selected()
        press(win, Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK)
        wait_until(lambda: self.closed)
        self.assertEqual(self.closed, [True])
        self.assertEqual({a.port_id for a in self.backend.get_devices("games").assignments},
                         {GPU, GPU_AUDIO})

    def test_warnings_then_back(self):
        win = self.open("games")
        self.select(win, GPU_AUDIO)
        win.toggle_selected()
        win.accept()
        self.assertEqual(win.page, window.CONFIRM)
        self.assertIs(win.get_focus(), win.cancel_button)
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.page, window.LIST)
        self.assertEqual(self.closed, [])

    def test_save_anyway(self):
        win = self.open("games")
        self.select(win, GPU_AUDIO)
        win.toggle_selected()
        win.accept()
        win.accept()
        wait_until(lambda: self.closed)
        self.assertEqual(self.closed, [True])

    def test_started_meanwhile(self):
        win = self.open("games")
        self.select(win, "00_1c.1-00_00.0")
        win.toggle_selected()
        self.backend.set_states({"games": model.RUNNING})
        win.accept()
        wait_until(lambda: "running" in win.footer.get_text())
        self.assertEqual(self.closed, [])
        self.assertIn("error", win.footer.get_style_context().list_classes())


if __name__ == "__main__":
    unittest.main()
