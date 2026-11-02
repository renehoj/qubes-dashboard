"""The GTK window against the demo backend. Skipped without GTK 3 or a display."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_firewall import window
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    window = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_firewall.backend.mock import MockBackend  # noqa: E402
from qubes_firewall.rules import RuleList  # noqa: E402


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
        win = window.FirewallWindow(
            self.backend, name, firewall=self.backend.get_firewall(name),
            on_close=self.closed.append,
        )
        self.addCleanup(win.dismiss)
        return win

    def rows(self, win):
        return [tuple(row) for row in win.store]

    def select(self, win, index):
        win.tree.set_cursor(Gtk.TreePath.new_from_indices([index]), None, False)

    def test_loads_in_background(self):
        win = window.FirewallWindow(self.backend, "work", on_close=self.closed.append)
        self.addCleanup(win.dismiss)
        wait_until(lambda: win.session is not None)
        self.assertEqual(len(win.store), 6)
        self.assertEqual(win.selected(), 0)
        self.assertIs(win.get_focus(), win.tree)

    def test_unknown_qube_shows_the_error(self):
        win = window.FirewallWindow(self.backend, "nope", on_close=self.closed.append)
        self.addCleanup(win.dismiss)
        wait_until(lambda: "nope" in win.footer.get_text())
        self.assertIn("error", win.footer.get_style_context().list_classes())
        self.assertFalse(win.save_button.get_sensitive())

    def test_rows(self):
        win = self.open("work")
        self.assertEqual(self.rows(win)[2],
                         ("2", "Allow", "mail.example.com", "TCP", "993", "", "IMAP"))

    def test_tools_follow_the_selection(self):
        win = self.open("work")
        self.select(win, 0)
        self.assertFalse(win.tools["up"].get_sensitive())
        self.assertTrue(win.tools["down"].get_sensitive())
        self.select(win, 5)
        self.assertFalse(win.tools["down"].get_sensitive())

    def test_move_and_delete_by_key(self):
        win = self.open("office")
        self.select(win, 0)
        press(win, Gdk.KEY_Down, Gdk.ModifierType.MOD1_MASK)
        self.assertEqual(self.rows(win)[0][1], "Allow")
        self.assertEqual(win.selected(), 1)
        win.tree.grab_focus()
        press(win, Gdk.KEY_Delete)
        self.assertEqual(len(win.store), 1)
        self.assertIn("not saved", win.subtitle.get_text())

    def test_edit_a_rule(self):
        win = self.open("work")
        self.select(win, 2)
        win.edit_rule()
        self.assertEqual(win.page, window.EDIT)
        self.assertEqual(win.f_dsthost.get_text(), "mail.example.com")
        self.assertEqual(win.f_ports.get_text(), "993")
        win.f_ports.set_text("995")
        press(win, Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK)
        self.assertEqual(win.page, window.RULES)
        self.assertEqual(self.rows(win)[2][4], "995")
        self.assertEqual(win.selected(), 2)

    def test_editor_refuses_a_bad_rule(self):
        win = self.open("work")
        win.add_rule()
        win.f_proto.set_active_id("tcp")
        win.f_ports.set_text("0")
        win.accept()
        self.assertEqual(win.page, window.EDIT)
        self.assertTrue(win.form_errors.get_visible())
        self.assertTrue(win.form_errors.get_text().startswith("Ports"))

    def test_protocol_enables_its_fields(self):
        win = self.open("work")
        win.add_rule()
        self.assertFalse(win.f_ports.get_sensitive())
        win.f_proto.set_active_id("udp")
        self.assertTrue(win.f_ports.get_sensitive())
        self.assertFalse(win.f_icmptype.get_sensitive())
        win.f_proto.set_active_id("icmp")
        self.assertTrue(win.f_icmptype.get_sensitive())

    def test_new_rule_goes_below_the_selection(self):
        win = self.open("work")
        self.select(win, 1)
        win.add_rule()
        win.f_dsthost.set_text("example.org")
        win.accept()
        self.assertEqual(self.rows(win)[2][2], "example.org")
        self.assertEqual(win.selected(), 2)

    def test_escape_in_editor_goes_back_without_change(self):
        win = self.open("work")
        win.add_rule()
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.page, window.RULES)
        self.assertEqual(len(win.store), 6)
        self.assertEqual(self.closed, [])

    def test_save_without_changes_just_closes(self):
        win = self.open("work")
        win.save()
        self.assertEqual(self.closed, [False])

    def test_save_writes(self):
        win = self.open("office")
        self.select(win, 0)
        win.delete_rule()
        press(win, Gdk.KEY_s, Gdk.ModifierType.CONTROL_MASK)
        wait_until(lambda: self.closed)
        self.assertEqual(self.closed, [True])
        self.assertEqual(self.backend.get_firewall("office").rules, RuleList(["action=accept"]))

    def test_warnings_need_a_second_save(self):
        win = self.open("office")
        self.select(win, 1)
        win.delete_rule()
        win.add_rule()
        win.f_action.set_active_id("drop")
        win.accept()
        win.save()
        self.assertEqual(win.page, window.CONFIRM)
        self.assertIs(win.get_focus(), win.cancel_button)
        self.assertEqual(win.save_button.get_label(), "Save _anyway")
        win.accept()
        wait_until(lambda: self.closed)
        self.assertEqual(self.closed, [True])
        self.assertEqual(len(self.backend.get_firewall("office").rules), 2)

    def test_back_from_warnings_writes_nothing(self):
        win = self.open("office")
        win.add_rule()
        win.f_action.set_active_id("drop")
        win.accept()
        self.select(win, 0)
        win.move_rule(1)
        win.move_rule(1)
        win.save()
        self.assertEqual(win.page, window.CONFIRM)
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.page, window.RULES)
        self.assertEqual(self.closed, [])
        self.assertEqual(len(self.backend.get_firewall("office").rules), 2)

    def test_changed_elsewhere_is_refused(self):
        win = self.open("office")
        self.select(win, 0)
        win.delete_rule()
        self.backend.change_elsewhere("office", ["action=drop"])
        win.save()
        wait_until(lambda: "changed elsewhere" in win.footer.get_text())
        self.assertEqual(self.closed, [])
        self.assertFalse(win.save_button.get_sensitive())
        self.assertEqual(self.backend.get_firewall("office").rules, RuleList(["action=drop"]))

    def test_dom0_is_read_only(self):
        win = self.open("dom0")
        self.assertFalse(win.stack.get_sensitive())
        self.assertFalse(win.save_button.get_sensitive())
        self.assertTrue(win.note.get_visible())

    def test_no_netvm_note(self):
        win = self.open("vault")
        self.assertEqual(win.note.get_text(), window.model.NO_NETVM_NOTE)

    def test_escape_closes(self):
        win = self.open("work")
        press(win, Gdk.KEY_Escape)
        self.assertEqual(self.closed, [False])

    def test_parent_makes_it_modal_over_the_list(self):
        parent = Gtk.Window()
        self.addCleanup(parent.destroy)
        win = window.FirewallWindow(self.backend, "work", parent=parent,
                                    firewall=self.backend.get_firewall("work"))
        self.addCleanup(win.dismiss)
        self.assertIs(win.get_transient_for(), parent)
        self.assertTrue(win.get_modal())


if __name__ == "__main__":
    unittest.main()
