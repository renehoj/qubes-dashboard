"""The demo backend, the tool runner (with a stand-in tool) and the qubesadmin
backend's commands (with qubesadmin and the tools faked)."""

import os
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_backup import model  # noqa: E402
from qubes_backup.backend import BackendError, Cancelled, Job, create_backend  # noqa: E402
from qubes_backup.backend import tools  # noqa: E402
from qubes_backup.backend.mock import MockBackend, WRONG_PASSPHRASE  # noqa: E402

BACKUP = model.BackupRequest("vault", "sys-usb", "/media/usb/backups", "pw")


def restore_request(path, **kw):
    return model.RestoreRequest("vault", "sys-usb", path, kw.pop("passphrase", "pw"), **kw)


class MockTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def test_auto_is_mock_here(self):
        try:
            import qubesadmin  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel
        except ImportError:
            self.assertTrue(create_backend("auto").is_demo)

    def test_storage_qubes(self):
        names = self.backend.storage_qubes()
        self.assertEqual(names[0], "dom0")
        self.assertIn("sys-usb", names)
        self.assertNotIn("debian-13", names)

    def test_backup_then_restore(self):
        outputs = []
        self.backend.backup(BACKUP, Job(), outputs.append)
        self.assertEqual(outputs[-2].percent, 100.0)
        files = self.backend.list_backups("sys-usb", "/media/usb/backups")
        self.assertEqual(len(files), 3)
        self.backend.restore(restore_request(f"/media/usb/backups/{files[0]}"), Job(),
                             outputs.append)
        self.assertEqual(self.backend.qube_info("vault1").state, "Halted")

    def test_verify_only_creates_nothing(self):
        path = "/media/usb/backups/qubes-backup-2026-10-19T201103"
        self.backend.restore(restore_request(path, verify_only=True), Job(), lambda o: None)
        with self.assertRaises(BackendError):
            self.backend.qube_info("vault1")

    def test_restore_errors(self):
        with self.assertRaisesRegex(BackendError, "No such file"):
            self.backend.restore(restore_request("/media/usb/backups/nope"), Job(),
                                 lambda o: None)
        with self.assertRaisesRegex(BackendError, "passphrase"):
            self.backend.restore(
                restore_request("/media/usb/backups/qubes-backup-2026-10-19T201103",
                                passphrase=WRONG_PASSPHRASE), Job(), lambda o: None)

    def test_cancel(self):
        backend = MockBackend(delay=1)
        job = Job()
        threading.Timer(0.1, job.cancel).start()
        with self.assertRaises(Cancelled):
            backend.backup(BACKUP, job, lambda o: None)

    def test_listing_a_qube_that_is_off(self):
        with self.assertRaisesRegex(BackendError, "not running"):
            self.backend.list_backups("vault", "/home/user")

    def test_revert(self):
        revisions = self.backend.revisions("vault")
        self.assertEqual(len(revisions), 2)
        self.assertGreater(revisions[0].taken, revisions[1].taken)
        self.backend.revert("vault", revisions[0].id)
        self.assertEqual(self.backend.revisions("vault"), [revisions[1]])
        with self.assertRaisesRegex(BackendError, "not halted"):
            self.backend.revert("work", "x")


def stand_in(script: str) -> str:
    """A stand-in tool: a Python script in a temporary directory."""
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "tool")
    Path(path).write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
    os.chmod(path, stat.S_IRWXU)
    return path


class RunToolTest(unittest.TestCase):
    def test_passphrase_on_stdin_and_progress(self):
        tool = stand_in(
            "import sys\n"
            "pw = sys.stdin.readline().rstrip()\n"
            "print('got', pw, flush=True)\n"
            "for p in (10, 55):\n"
            "    sys.stderr.write(f'\\rMaking a backup... {p}.00%'); sys.stderr.flush()\n"
            "print(flush=True)\n")
        outputs = []
        tools.run_tool([tool], b"s3cret\n", Job(), outputs.append)
        self.assertEqual(outputs, [model.Output(line="got s3cret"),
                                   model.Output(percent=10.0), model.Output(percent=55.0)])

    def test_failure_quotes_the_last_lines(self):
        tool = stand_in("import sys\nprint('one'); print('two'); sys.exit(3)\n")
        with self.assertRaisesRegex(BackendError, "failed: one two"):
            tools.run_tool([tool], b"", Job(), lambda o: None)

    def test_missing_tool(self):
        with self.assertRaisesRegex(BackendError, "Cannot run"):
            tools.run_tool(["/nonexistent/qvm-backup"], b"", Job(), lambda o: None)

    def test_cancel_sends_sigint(self):
        tool = stand_in(
            "import sys, time\n"
            "try:\n"
            "    print('ready', flush=True); time.sleep(10)\n"
            "except KeyboardInterrupt:\n"
            "    print('interrupted', flush=True); sys.exit(1)\n")
        job = Job()
        outputs = []

        def report(output):
            outputs.append(output)
            if output.line == "ready":
                job.cancel()

        started = time.monotonic()
        with self.assertRaises(Cancelled):
            tools.run_tool([tool], b"", job, report)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn(model.Output(line="interrupted"), outputs)

    def test_run_quiet(self):
        self.assertEqual(tools.run_quiet([stand_in("print('a')")]), "a\n")
        with self.assertRaisesRegex(BackendError, "^boom$"):
            tools.run_quiet([stand_in("import sys\nsys.exit('boom')")])


class QubesAdminBackendTest(unittest.TestCase):
    """The commands it runs, with qubesadmin and the tools faked."""

    def setUp(self):
        fake = types.ModuleType("qubesadmin")
        fake.exc = types.SimpleNamespace(QubesException=type("QubesException", (Exception,), {}))
        vms = [types.SimpleNamespace(name=n, klass=k, get_power_state=lambda s=s: s,
                                     is_running=lambda s=s: s == "Running")
               for n, k, s in (("dom0", "AdminVM", "Running"), ("work", "AppVM", "Halted"),
                               ("sys-usb", "DispVM", "Running"),
                               ("debian-13", "TemplateVM", "Halted"))]

        class Domains(list):
            def __getitem__(self, name):
                if isinstance(name, int):
                    return list.__getitem__(self, name)
                for vm in self:
                    if vm.name == name:
                        return vm
                raise KeyError(name)

        fake.Qubes = lambda: types.SimpleNamespace(domains=Domains(vms))
        patcher = mock.patch.dict(sys.modules, {"qubesadmin": fake, "qubesadmin.exc": fake.exc})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("qubes_backup.backend.qubesadmin_backend", None)
        self.addCleanup(sys.modules.pop, "qubes_backup.backend.qubesadmin_backend", None)
        from qubes_backup.backend import qubesadmin_backend  # pylint: disable=import-outside-toplevel
        self.module = qubesadmin_backend
        self.backend = qubesadmin_backend.QubesAdminBackend()

    def test_info_and_storage(self):
        self.assertEqual(self.backend.qube_info("work"), model.QubeInfo("work", "AppVM", "Halted"))
        self.assertEqual(self.backend.storage_qubes(), ["dom0", "sys-usb", "work"])
        with self.assertRaisesRegex(BackendError, "No such qube"):
            self.backend.qube_info("nope")

    def test_tools(self):
        calls = []
        with mock.patch.object(self.module, "run_tool",
                               lambda argv, stdin, job, report: calls.append((argv, stdin))), \
             mock.patch.object(self.module, "run_quiet",
                               lambda argv: calls.append((argv, None)) or "1729000000-back\n"):
            self.backend.backup(BACKUP, Job(), None)
            self.backend.restore(restore_request("/b/qubes-backup-x"), Job(), None)
            self.assertEqual([r.id for r in self.backend.revisions("work")],
                             ["1729000000-back"])
            self.backend.revert("work", "1729000000-back")
        self.assertEqual(calls[0], (model.backup_command(BACKUP), b"pw\n"))
        self.assertEqual(calls[1][0][0], "qvm-backup-restore")
        self.assertEqual(calls[2][0], ["qvm-volume", "info", "work:private", "revisions"])
        self.assertEqual(calls[3][0], ["qvm-volume", "revert", "work:private",
                                       "1729000000-back"])

    def test_listing(self):
        calls = []
        listing = "qubes-backup-2026-10-19T201103\nother\n"
        with mock.patch.object(self.module, "run_quiet",
                               lambda argv: calls.append(argv) or listing):
            self.assertEqual(self.backend.list_backups("sys-usb", "/media/usb"),
                             ["qubes-backup-2026-10-19T201103"])
            with self.assertRaisesRegex(BackendError, "not running"):
                self.backend.list_backups("work", "/home/user")
            self.backend.list_backups("work", "/home/user", start=True)
        self.assertEqual(calls[0], ["qvm-run", "--pass-io", "--no-gui", "--quiet",
                                    "--no-autostart", "sys-usb", "ls -1 -- /media/usb"])
        self.assertNotIn("--no-autostart", calls[1])

    def test_listing_dom0(self):
        directory = tempfile.mkdtemp()
        Path(directory, "qubes-backup-2026-10-19T201103").touch()
        Path(directory, "x").touch()
        self.assertEqual(self.backend.list_backups("dom0", directory),
                         ["qubes-backup-2026-10-19T201103"])
        with self.assertRaises(BackendError):
            self.backend.list_backups("dom0", os.path.join(directory, "missing"))


if __name__ == "__main__":
    unittest.main()
