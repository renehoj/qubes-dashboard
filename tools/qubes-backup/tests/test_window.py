"""The GTK window against the demo backend. Skipped without GTK 3 or a display."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from qubes_backup import window
    from gi.repository import Gdk, Gtk
    if not Gtk.init_check()[0]:
        raise ImportError("no display")
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    window = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_backup import model  # noqa: E402
from qubes_backup.backend.mock import MockBackend  # noqa: E402

BACKUP_DIR = "/media/usb/backups"


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
        self.settings = os.path.join(tempfile.mkdtemp(), "settings.json")
        self.closed = []
        self.finished = []

    def open(self, name, backend=None):
        win = window.BackupWindow(backend or self.backend, name,
                                  settings_path=self.settings,
                                  on_close=self.closed.append)
        win.on_finished = lambda: self.finished.append(True)
        self.addCleanup(win.dismiss)
        wait_until(lambda: win.info is not None)
        return win

    def wait_finished(self, count=1):
        wait_until(lambda: len(self.finished) >= count)
        self.assertEqual(len(self.finished), count)

    def fill_backup(self, win, repeat="pw"):
        win.backup_qube.get_child().set_text("sys-usb")
        win.backup_directory.set_text(BACKUP_DIR)
        win.backup_passphrase.set_text("pw")
        win.backup_repeat.set_text(repeat)

    def log_text(self, win):
        start, end = win.log_buffer.get_bounds()
        return win.log_buffer.get_text(start, end, False)

    def test_loads_and_offers_storage_qubes(self):
        win = self.open("vault")
        self.assertEqual(win.current, model.BACKUP)
        self.assertIn("halted", win.subtitle.get_text())
        names = [row[0] for row in win.backup_qube.get_model()]
        self.assertEqual(names[0], "dom0")
        self.assertIn("sys-usb", names)
        self.assertNotIn("vault", names)  # not to itself
        self.assertIs(win.get_focus(), win.backup_qube.get_child())
        self.assertTrue(win.run_button.get_sensitive())

    def test_backup(self):
        win = self.open("vault")
        self.fill_backup(win)
        press(win, Gdk.KEY_Return, CTRL)
        self.assertIsNotNone(win.job)
        self.assertTrue(win.cancel_button.get_visible())
        self.assertFalse(win.close_button.get_sensitive())
        self.wait_finished()
        self.assertIsNone(win.job)
        self.assertEqual(self.backend.backups[0],
                         model.BackupRequest("vault", "sys-usb", BACKUP_DIR, "pw"))
        self.assertIn("$ qvm-backup --yes", self.log_text(win))
        self.assertIn("Backup written", self.log_text(win))
        self.assertEqual(win.progress.get_fraction(), 1)
        self.assertEqual(win.backup_passphrase.get_text(), "")
        self.assertIn("is backed up", win.footer.get_text())
        # Remembered, without the passphrase.
        settings = model.load_settings(self.settings)
        self.assertEqual((settings.backup_qube, settings.backup_directory),
                         ("sys-usb", BACKUP_DIR))
        self.assertNotIn("pw", Path(self.settings).read_text(encoding="utf-8"))

    def test_remembered_next_time(self):
        model.save_settings(model.Settings("sys-usb", BACKUP_DIR, "sys-usb", BACKUP_DIR),
                            self.settings)
        win = self.open("vault")
        self.assertEqual(win.backup_qube.get_active_text(), "sys-usb")
        self.assertIs(win.get_focus(), win.backup_passphrase)

    def test_backup_checks_fields(self):
        win = self.open("vault")
        self.fill_backup(win, repeat="other")
        win.run()
        self.assertIsNone(win.job)
        self.assertIn("not the same", win.footer.get_text())
        self.assertIn("failed", win.footer.get_style_context().list_classes())
        self.assertEqual(self.backend.backups, [])

    def test_cancel_and_no_escape_while_running(self):
        win = self.open("vault", MockBackend(delay=1))
        self.fill_backup(win)
        win.run()
        press(win, Gdk.KEY_Escape)
        self.assertEqual(self.closed, [])
        self.assertIn("Cancel", win.footer.get_text())
        press(win, Gdk.KEY_2, CTRL)
        self.assertEqual(win.current, model.BACKUP)  # pages stay while it runs
        win.cancel_button.clicked()
        self.wait_finished()
        self.assertIn("Cancelled", self.log_text(win))
        press(win, Gdk.KEY_Escape)
        self.assertEqual(self.closed, [False])

    def test_restore_finds_backups_and_renames(self):
        model.save_settings(model.Settings(restore_qube="sys-usb",
                                           restore_directory=BACKUP_DIR), self.settings)
        win = self.open("vault")
        press(win, Gdk.KEY_2, CTRL)
        self.assertEqual(win.current, model.RESTORE)
        # Looked on its own: the qube is running.
        wait_until(lambda: win.restore_file.get_active_text())
        self.assertEqual(win.restore_file.get_active_text(), "qubes-backup-2026-10-19T201103")
        win.restore_passphrase.set_text("pw")
        win.restore_passphrase.activate()
        self.wait_finished()
        request = self.backend.restores[0]
        self.assertEqual(request.path, f"{BACKUP_DIR}/qubes-backup-2026-10-19T201103")
        self.assertIn("--rename-conflicting", self.log_text(win))
        self.assertTrue(win.changed)
        win.close_window()
        self.assertEqual(self.closed, [True])

    def test_verify_only(self):
        win = self.open("vault")
        win.select(model.RESTORE)
        win.verify_only.set_active(True)
        self.assertEqual(win.run_button.get_label(), "_Verify")
        win.restore_qube.get_child().set_text("sys-usb")
        win.restore_directory.set_text(BACKUP_DIR)
        win.restore_file.get_child().set_text("qubes-backup-2026-10-12T091544")
        win.restore_passphrase.set_text("pw")
        win.run()
        self.wait_finished()
        self.assertTrue(self.backend.restores[0].verify_only)
        self.assertFalse(win.changed)
        self.assertIn("is good", win.footer.get_text())

    def test_failed_restore(self):
        win = self.open("vault")
        win.select(model.RESTORE)
        win.restore_qube.get_child().set_text("sys-usb")
        win.restore_directory.set_text(BACKUP_DIR)
        win.restore_file.get_child().set_text("qubes-backup-2026-10-12T091544")
        win.restore_passphrase.set_text("wrong")
        win.run()
        self.wait_finished()
        self.assertIn("wrong passphrase", win.footer.get_text())
        self.assertEqual(win.progress.get_text(), "Failed")
        self.assertFalse(win.changed)

    def test_find_button_reports_errors(self):
        win = self.open("vault")
        win.select(model.RESTORE)
        win.restore_qube.get_child().set_text("sys-usb")
        win.restore_directory.set_text("/nowhere")
        win.find_button.clicked()
        wait_until(lambda: "Could not list" in win.footer.get_text())
        self.assertIn("No such file", win.footer.get_text())

    def test_revert_asks_first(self):
        win = self.open("vault")
        press(win, Gdk.KEY_3, CTRL)
        wait_until(lambda: not win.busy)
        newest = self.backend.revisions("vault")[0]
        self.assertEqual(win.revision_combo.get_active_id(), newest.id)
        self.assertEqual(len(win.revision_combo.get_model()), 2)
        press(win, Gdk.KEY_Return, CTRL)
        self.assertEqual(win.bottom_stack.get_visible_child_name(), "confirm")
        self.assertIs(win.get_focus(), win.confirm_cancel)
        press(win, Gdk.KEY_Escape)
        self.assertEqual(win.bottom_stack.get_visible_child_name(), "buttons")
        self.assertEqual(self.closed, [])
        self.assertEqual(self.backend.reverts, [])
        win.run()
        press(win, Gdk.KEY_v)
        self.wait_finished()
        self.assertEqual(self.backend.reverts, [("vault", newest.id)])
        wait_until(lambda: len(win.revisions) == 1)
        self.assertIn("is reverted", win.footer.get_text())

    def test_running_qube_cannot_revert(self):
        win = self.open("work")
        win.select(model.REVERT)
        wait_until(lambda: not win.busy)
        self.assertFalse(win.run_button.get_sensitive())
        self.assertIn("Shut work down", win.notes[model.REVERT].get_text())
        win.run()
        self.assertEqual(win.bottom_stack.get_visible_child_name(), "buttons")

    def test_revert_follows_the_state(self):
        backend = MockBackend(delay=0)
        win = self.open("office", backend)
        backend.set_state("office", "Running")
        win.select(model.REVERT)
        wait_until(lambda: not win.busy)
        self.assertFalse(win.run_button.get_sensitive())

    def test_dom0_has_nothing(self):
        win = self.open("dom0")
        self.assertFalse(win.run_button.get_sensitive())
        self.assertFalse(win.forms[model.BACKUP].get_sensitive())
        self.assertIn("dom0 is not", win.notes[model.BACKUP].get_text())

    def test_dismiss_while_running_keeps_the_window(self):
        win = self.open("vault", MockBackend(delay=1))
        self.fill_backup(win)
        win.run()
        win.dismiss()
        self.assertFalse(win._closed)  # pylint: disable=protected-access
        win.cancel()
        self.wait_finished()
        win.close_window()
        self.assertEqual(self.closed, [])  # the caller let go

    def test_dismiss_does_not_report(self):
        win = self.open("vault")
        win.dismiss()
        self.assertEqual(self.closed, [])


if __name__ == "__main__":
    unittest.main()
