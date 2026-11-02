"""The GTK window against the demo backend. Skipped without GTK 3 or a display."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_information import window
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    window = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_information import model  # noqa: E402
from qubes_information.backend.mock import MockBackend  # noqa: E402


def press(widget, keyval, state=0):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    event.state = state
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
        win = window.InformationWindow(
            self.backend, name, info=self.backend.get_info(name),
            on_close=lambda: self.closed.append(True),
        )
        self.addCleanup(win.dismiss)
        drain()
        return win

    def filter(self, win, text):
        win.search_entry.set_text(text)
        wait_until(lambda: win.shown_query == text)

    def titles(self, win):
        return [section.title for section in win.shown]

    def test_loads_in_background(self):
        win = window.InformationWindow(self.backend, "work")
        self.addCleanup(win.dismiss)
        wait_until(lambda: win.info is not None)
        self.assertEqual(self.titles(win), list(model.SECTION_ORDER))
        self.assertIs(win.get_focus(), win.search_entry)
        self.assertIn("sys-firewall", win.subtitle.get_text())

    def test_unknown_qube_shows_the_error(self):
        win = window.InformationWindow(self.backend, "nope")
        self.addCleanup(win.dismiss)
        wait_until(lambda: "nope" in win.footer.get_text())
        self.assertIn("error", win.footer.get_style_context().list_classes())

    def test_filter_and_escape(self):
        win = self.open("work")
        self.filter(win, "services")
        self.assertEqual(self.titles(win), [model.SERVICES])
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.search_entry.get_text(), "")
        wait_until(lambda: win.shown_query == "")
        self.assertEqual(self.closed, [])
        press(win, Gdk.KEY_Escape)
        self.assertEqual(self.closed, [True])

    def test_nothing_matches(self):
        win = self.open("work")
        self.filter(win, "zzzz")
        self.assertEqual(win.shown, [])
        self.assertEqual(win.text(), "")

    def test_text_is_the_filtered_page(self):
        win = self.open("work")
        self.filter(win, "tags")
        self.assertEqual(win.text(), "Tags  (2)\n  created-by-dom0\n  work-data\n")

    def test_jump_to_section(self):
        win = self.open("work")
        win.show_all()
        drain()
        press(win, Gdk.KEY_8, Gdk.ModifierType.CONTROL_MASK)
        drain()
        self.assertGreater(win.scroller.get_vadjustment().get_value(), 0)
        press(win, Gdk.KEY_Home, Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(win.scroller.get_vadjustment().get_value(), 0)

    def test_live_updates_in_place(self):
        win = self.open("work")
        value, _detail = win._live_labels["Memory now"]  # pylint: disable=protected-access
        win._stats_loaded(self.backend.get_stats("work"))  # pylint: disable=protected-access
        self.assertIs(win._live_labels["Memory now"][0], value)  # pylint: disable=protected-access
        self.assertTrue(win._timer)  # pylint: disable=protected-access

    def test_no_timer_for_a_halted_qube(self):
        win = self.open("vault")
        self.assertNotIn(model.LIVE, self.titles(win))
        self.assertFalse(win._timer)  # pylint: disable=protected-access

    def test_stopped_qube_reloads(self):
        win = self.open("work")
        win._stats_loaded(None)  # pylint: disable=protected-access
        wait_until(lambda: not win._loading)  # pylint: disable=protected-access
        self.assertIsNotNone(win.info)

    def test_reload_key(self):
        win = self.open("work")
        press(win, Gdk.KEY_r, Gdk.ModifierType.CONTROL_MASK)
        self.assertTrue(win._loading)  # pylint: disable=protected-access
        wait_until(lambda: not win._loading)  # pylint: disable=protected-access

    def test_dismiss_does_not_report(self):
        win = window.InformationWindow(self.backend, "work",
                                       info=self.backend.get_info("work"),
                                       on_close=lambda: self.closed.append(True))
        win.dismiss()
        self.assertEqual(self.closed, [])


if __name__ == "__main__":
    unittest.main()
