"""The headless core: pages per state, command lines, output parsing, settings."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_backup import model  # noqa: E402


def qube(state="Halted", klass="AppVM", name="work"):
    return model.QubeInfo(name, klass, state)


class PagesTest(unittest.TestCase):
    def test_shut_down_qube_gets_all_three(self):
        self.assertEqual(model.available_pages(qube("Halted")), set(model.PAGES))

    def test_running_qube_cannot_be_reverted(self):
        for state in ("Running", "Paused", "Transient", "Halting"):
            with self.subTest(state=state):
                self.assertEqual(model.available_pages(qube(state)),
                                 {model.BACKUP, model.RESTORE})

    def test_dom0_gets_nothing(self):
        self.assertEqual(model.available_pages(qube("Running", "AdminVM", "dom0")), set())

    def test_notes_say_why(self):
        self.assertIn("last shutdown", model.page_note(model.BACKUP, qube("Running")))
        self.assertIn("work1", model.page_note(model.RESTORE, qube()))
        self.assertIn("Shut work down", model.page_note(model.REVERT, qube("Running")))
        self.assertIn("is lost", model.page_note(model.REVERT, qube()))


class RevisionsTest(unittest.TestCase):
    def test_newest_first_with_times(self):
        revs = model.parse_revisions("1729000000-back\n1729990000-back\n")
        self.assertEqual([r.id for r in revs], ["1729990000-back", "1729000000-back"])
        self.assertEqual(revs[0].taken, 1729990000.0)

    def test_counter_ids_have_no_time(self):
        revs = model.parse_revisions("1\n2\n")
        self.assertEqual([r.id for r in revs], ["2", "1"])
        self.assertIsNone(revs[0].taken)
        self.assertEqual(model.revision_label(revs[0]), "Revision 2")

    def test_junk_is_dropped(self):
        revs = model.parse_revisions("\n  \n1729000000-back\nrm -rf /\n")
        self.assertEqual([r.id for r in revs], ["1729000000-back"])

    def test_label(self):
        taken = time.mktime((2026, 10, 25, 14, 2, 0, 0, 0, -1))
        label = model.revision_label(model.Revision("x", taken), now=taken + 3 * 3600 + 5)
        self.assertEqual(label, "2026-10-25 14:02  (3 hours ago)")

    def test_ago(self):
        self.assertEqual(model.ago(0, now=30), "just now")
        self.assertEqual(model.ago(0, now=60), "1 minute ago")
        self.assertEqual(model.ago(0, now=2 * 86400 + 5), "2 days ago")


class CommandsTest(unittest.TestCase):
    def backup(self, **kw):
        values = dict(qube="work", dest_qube="sys-usb", directory="/media/usb/backups",
                      passphrase="secret")
        values.update(kw)
        return model.BackupRequest(**values)

    def restore(self, **kw):
        values = dict(qube="work", source_qube="sys-usb",
                      path="/media/usb/backups/qubes-backup-2026-10-26T143012",
                      passphrase="secret")
        values.update(kw)
        return model.RestoreRequest(**values)

    def test_backup_to_a_qube(self):
        self.assertEqual(model.backup_command(self.backup()), [
            "qvm-backup", "--yes", "--passphrase-file", "-", "--dest-vm", "sys-usb",
            "/media/usb/backups", "work"])

    def test_backup_to_dom0_uncompressed(self):
        argv = model.backup_command(self.backup(dest_qube="dom0", compress=False))
        self.assertEqual(argv, ["qvm-backup", "--yes", "--passphrase-file", "-",
                                "--no-compress", "/media/usb/backups", "work"])

    def test_restore_renames(self):
        self.assertEqual(model.restore_command(self.restore()), [
            "qvm-backup-restore", "--passphrase-file", "-", "--rename-conflicting",
            "--dest-vm", "sys-usb", "/media/usb/backups/qubes-backup-2026-10-26T143012",
            "work"])

    def test_verify_only(self):
        self.assertIn("--verify-only",
                      model.restore_command(self.restore(verify_only=True)))

    def test_passphrase_never_on_the_command_line(self):
        for argv in (model.backup_command(self.backup()),
                     model.restore_command(self.restore())):
            self.assertNotIn("secret", " ".join(argv))
        self.assertEqual(model.stdin_for(self.backup()), b"secret\n")

    def test_volume_commands(self):
        self.assertEqual(model.revisions_command("work"),
                         ["qvm-volume", "info", "work:private", "revisions"])
        self.assertEqual(model.revert_command("work", "1729000000-back"),
                         ["qvm-volume", "revert", "work:private", "1729000000-back"])

    def test_command_text_quotes(self):
        self.assertEqual(model.command_text(["a", "b c"]), "a 'b c'")

    def test_checks(self):
        model.check_backup(self.backup(), "secret")
        model.check_restore(self.restore())
        bad = [
            (self.backup(dest_qube=""), "secret", "Pick"),
            (self.backup(dest_qube="-x"), "secret", "not a qube"),
            (self.backup(directory=""), "secret", "Enter the directory"),
            (self.backup(directory="usb"), "secret", "absolute"),
            (self.backup(directory="/a\nb"), "secret", "line break"),
            (self.backup(passphrase=""), "", "passphrase"),
            (self.backup(), "other", "not the same"),
        ]
        for request, repeated, message in bad:
            with self.subTest(message=message):
                with self.assertRaisesRegex(model.InvalidRequest, message):
                    model.check_backup(request, repeated)
        with self.assertRaisesRegex(model.InvalidRequest, "absolute"):
            model.check_restore(self.restore(path="backup"))


class OutputTest(unittest.TestCase):
    def test_lines_and_progress(self):
        parser = model.OutputParser()
        out = parser.feed("summary\nMaking a backup... 1.00%\rMaking a backup... 2")
        self.assertEqual(out, [model.Output(line="summary"), model.Output(percent=1.0)])
        out = parser.feed("5.50%\r\n\nDone\n")
        self.assertEqual(out, [model.Output(percent=25.5), model.Output(line="Done")])
        self.assertEqual(parser.close(), [])

    def test_close_flushes(self):
        parser = model.OutputParser()
        self.assertEqual(parser.feed("no newline"), [])
        self.assertEqual(parser.close(), [model.Output(line="no newline")])

    def test_colours_stripped(self):
        parser = model.OutputParser()
        self.assertEqual(parser.feed("\x1b[0;31mbad\x1b[0m\n"), [model.Output(line="bad")])


class BackupFilesTest(unittest.TestCase):
    def test_only_backups_newest_first(self):
        names = ["qubes-backup-2026-10-12T091544", "notes.txt",
                 "qubes-backup-2026-10-19T201103", "qubes-backup-x; rm -rf /",
                 "\x1b]0;evil\x07"]
        self.assertEqual(model.backup_files(names), [
            "qubes-backup-2026-10-19T201103", "qubes-backup-2026-10-12T091544"])

    def test_listing_script_quotes(self):
        self.assertEqual(model.list_backups_script("/media/my usb"), "ls -1 -- '/media/my usb'")

    def test_split_path(self):
        self.assertEqual(model.split_path("/a/b/f"), ("/a/b", "f"))
        self.assertEqual(model.split_path("/f"), ("/", "f"))


class SettingsTest(unittest.TestCase):
    def test_round_trip(self):
        path = os.path.join(tempfile.mkdtemp(), "sub", "settings.json")
        self.assertEqual(model.load_settings(path), model.Settings())
        settings = model.Settings("sys-usb", "/media/usb", "sys-usb", "/media/usb", False)
        model.save_settings(settings, path)
        self.assertEqual(model.load_settings(path), settings)

    def test_bad_file_gives_defaults(self):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        for content in ("not json", "[1]", '{"backup_qube": 3, "compress": false}'):
            with self.subTest(content=content):
                Path(path).write_text(content, encoding="utf-8")
                settings = model.load_settings(path)
                self.assertEqual(settings.backup_qube, "")
        self.assertFalse(model.load_settings(path).compress)

    def test_no_passphrase_field(self):
        names = {f.name for f in model.dataclasses.fields(model.Settings)}
        self.assertFalse({n for n in names if "pass" in n})


if __name__ == "__main__":
    unittest.main()
