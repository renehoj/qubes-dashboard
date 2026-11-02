"""The GTK window against the demo backend. Skipped without GTK 3 or a display."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_logs import window
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    window = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_logs import model  # noqa: E402
from qubes_logs.backend.mock import MockBackend  # noqa: E402


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


CTRL = Gdk.ModifierType.CONTROL_MASK if window else 0


@unittest.skipIf(window is None, SKIP)
class WindowTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)
        self.closed = []

    def open(self, name):
        win = window.LogsWindow(
            self.backend, name, logs=self.backend.read_logs(name),
            on_close=lambda: self.closed.append(True),
        )
        self.addCleanup(win.dismiss)
        drain()
        return win

    def filter(self, win, text):
        win.search_entry.set_text(text)
        wait_until(lambda: win.shown_query == text)

    def buffer_text(self, win):
        start, end = win.buffer.get_bounds()
        return win.buffer.get_text(start, end, False)

    def test_loads_in_background(self):
        win = window.LogsWindow(self.backend, "work")
        self.addCleanup(win.dismiss)
        wait_until(lambda: win.logs is not None)
        self.assertEqual([tab.get_label() for tab in win.tabs], ["Console", "GUI", "qrexec"])
        self.assertEqual(win.current, 0)
        self.assertIs(win.get_focus(), win.search_entry)
        self.assertIn("guest-work.log", win.subtitle.get_text())
        self.assertIn("work login:", self.buffer_text(win))
        self.assertEqual(win.stack.get_visible_child_name(), "text")

    def test_dom0_has_one_tab(self):
        win = self.open("dom0")
        self.assertEqual([tab.get_label() for tab in win.tabs], ["Hypervisor"])
        press(win, Gdk.KEY_2, CTRL)
        self.assertEqual(win.current, 0)

    def test_switching_tabs(self):
        win = self.open("work")
        press(win, Gdk.KEY_3, CTRL)
        self.assertEqual(win.current, 2)
        self.assertIn("qrexec.work.log", win.subtitle.get_text())
        self.assertIn("active", win.tabs[2].get_style_context().list_classes())
        self.assertNotIn("active", win.tabs[0].get_style_context().list_classes())
        press(win, Gdk.KEY_Tab, CTRL)
        self.assertEqual(win.current, 0)
        press(win, Gdk.KEY_Page_Up, CTRL)
        self.assertEqual(win.current, 2)
        win.tabs[1].clicked()
        self.assertEqual(win.current, 1)

    def test_digits_without_ctrl_filter(self):
        win = self.open("work")
        win.view.grab_focus()
        press(win, Gdk.KEY_2)
        self.assertEqual(win.current, 0)
        self.assertIs(win.get_focus(), win.search_entry)

    def test_missing_log_shows_why(self):
        win = self.open("sys-firewall")  # no GUI daemon
        self.assertIn("missing", win.tabs[1].get_style_context().list_classes())
        win.select(1)
        self.assertEqual(win.stack.get_visible_child_name(), "placeholder")
        self.assertIn("no such file", win.placeholder.get_text())
        self.assertEqual(win.text(), "")

    def test_opens_on_a_log_that_exists(self):
        win = window.LogsWindow(self.backend, "work", logs=[
            model.LogText(model.log_files("work")[0], error="permission denied"),
            *self.backend.read_logs("work")[1:],
        ])
        self.addCleanup(win.dismiss)
        self.assertEqual(win.current, 1)

    def test_filter_and_escape(self):
        win = self.open("work")
        self.filter(win, "qubes db")
        self.assertEqual(len(win.shown), 1)
        self.assertEqual(self.buffer_text(win), win.text())
        self.assertTrue(win.footer.get_text().startswith("1 of "))
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.search_entry.get_text(), "")
        wait_until(lambda: win.shown_query == "")
        self.assertEqual(self.closed, [])
        press(win, Gdk.KEY_Escape)
        self.assertEqual(self.closed, [True])

    def test_filter_stays_across_tabs(self):
        win = self.open("work")
        self.filter(win, "denied")
        self.assertEqual(win.shown, [])
        self.assertIn("Nothing matches", win.placeholder.get_text())
        win.select(2)
        self.assertEqual(len(win.shown), 1)

    def test_errors_are_highlighted(self):
        win = self.open("sys-usb")
        tag = win.buffer.get_tag_table().lookup(model.ERROR)
        tagged = [n for n, line in enumerate(win.shown)
                  if win.buffer.get_iter_at_line(n).has_tag(tag)]
        self.assertEqual([win.shown[n].split("] ", 2)[2][:3] for n in tagged],
                         ["usb", "[FA"])

    def test_copy_to_global_clipboard(self):
        win = self.open("work")
        self.filter(win, "qubes db")
        win.copy_button.clicked()
        wait_until(lambda: self.backend.clipboard)
        self.assertEqual(self.backend.clipboard, [win.text()])
        wait_until(lambda: "Copied 1 line " in win.footer.get_text())

    def test_reload_key(self):
        win = self.open("work")
        win.select(2)
        press(win, Gdk.KEY_r, CTRL)
        self.assertTrue(win._loading)  # pylint: disable=protected-access
        wait_until(lambda: not win._loading)  # pylint: disable=protected-access
        self.assertEqual(win.current, 2)

    def test_scrolled_to_the_end(self):
        win = self.open("work")
        win.show_all()
        wait_until(lambda: win.scroller.get_vadjustment().get_value() > 0, timeout=2)
        adjustment = win.scroller.get_vadjustment()
        self.assertAlmostEqual(adjustment.get_value(),
                               adjustment.get_upper() - adjustment.get_page_size(), delta=1)
        press(win, Gdk.KEY_Home, CTRL)
        self.assertEqual(adjustment.get_value(), 0)

    def test_dismiss_does_not_report(self):
        win = window.LogsWindow(self.backend, "work", logs=self.backend.read_logs("work"),
                                on_close=lambda: self.closed.append(True))
        win.dismiss()
        self.assertEqual(self.closed, [])


if __name__ == "__main__":
    unittest.main()
