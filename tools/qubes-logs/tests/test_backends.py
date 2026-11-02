"""The file backend against a directory standing in for /, and the demo data."""

import fcntl
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_logs import model  # noqa: E402
from qubes_logs.backend import BackendError, create_backend  # noqa: E402
from qubes_logs.backend.files import FileBackend  # noqa: E402
from qubes_logs.backend.mock import MockBackend  # noqa: E402


class FileBackendTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.backend = FileBackend(str(self.root), max_bytes=32)

    def write(self, path, data: bytes):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def test_reads_a_small_file_whole(self):
        self.write("/var/log/qubes/guid.work.log", b"Icon size: 128x128\nconnected\n")
        log = self.backend.read_log(model.log_files("work")[1])
        self.assertIsNone(log.error)
        self.assertEqual(log.lines, ["Icon size: 128x128", "connected"])
        self.assertFalse(log.truncated)
        self.assertEqual(log.size, 29)
        self.assertIsNotNone(log.mtime)

    def test_reads_only_the_tail(self):
        data = b"".join(b"line %02d\n" % i for i in range(20))
        self.write("/var/log/xen/console/guest-work.log", data)
        log = self.backend.read_log(model.log_files("work")[0])
        self.assertTrue(log.truncated)
        self.assertEqual(log.size, len(data))
        # 32 bytes is "e 16\n" plus three whole lines; the partial one goes.
        self.assertEqual(log.lines, ["line 17", "line 18", "line 19"])

    def test_missing_file(self):
        logs = self.backend.read_logs("work")
        self.assertEqual(len(logs), 3)
        for log in logs:
            self.assertIn("no such file", log.error)
            self.assertEqual(log.text, "")

    @unittest.skipIf(os.geteuid() == 0, "root reads anything")
    def test_permission_denied(self):
        target = self.write("/var/log/qubes/qrexec.work.log", b"secret\n")
        target.chmod(0)
        log = self.backend.read_log(model.log_files("work")[2])
        self.assertEqual(log.error, "permission denied")

    def test_global_clipboard(self):
        (self.root / "run" / "qubes").mkdir(parents=True)
        self.backend.copy_to_global_clipboard("two\nlines\n")
        run = self.root / "run" / "qubes"
        self.assertEqual((run / "qubes-clipboard.bin").read_text(), "two\nlines\n")
        self.assertEqual((run / "qubes-clipboard.bin.source").read_text(), "dom0")
        # The lock is released again.
        with open(run / "appviewer.lock", "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_global_clipboard_unavailable(self):
        with self.assertRaises(BackendError):
            self.backend.copy_to_global_clipboard("x")  # no /run/qubes

    def test_never_writes_a_log(self):
        source = (Path(__file__).resolve().parents[1] / "qubes_logs" / "backend"
                  / "files.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"w"'), 2)  # the clipboard's two files
        self.assertNotIn("/var/log", source)


class MockBackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def test_every_demo_qube_of_the_list_has_a_console_log(self):
        for name in ("work", "sys-net", "sys-usb", "vault", "disp1234", "personal"):
            with self.subTest(name=name):
                console = self.backend.read_logs(name)[0]
                self.assertIsNone(console.error)
                self.assertIn(f"{name} login:", console.text)
                self.assertNotIn("\x1b", console.text)

    def test_dom0(self):
        (log,) = self.backend.read_logs("dom0")
        self.assertTrue(log.text.startswith("(XEN) Xen version"))

    def test_something_to_highlight(self):
        lines = self.backend.read_logs("sys-usb")[0].lines
        self.assertTrue(any(model.severity(line) == model.ERROR for line in lines))

    def test_missing_logs(self):
        self.assertIsNotNone(self.backend.read_logs("sys-firewall")[1].error)  # no GUI
        self.assertTrue(all(log.error for log in self.backend.read_logs("nope")))

    def test_clipboard_recorded(self):
        self.backend.copy_to_global_clipboard("x\n")
        self.assertEqual(self.backend.clipboard, ["x\n"])
        with self.assertRaises(BackendError):
            self.backend.copy_to_global_clipboard("")


class CreateBackendTest(unittest.TestCase):
    def test_kinds(self):
        self.assertIsInstance(create_backend("mock"), MockBackend)
        self.assertIsInstance(create_backend("files"), FileBackend)
        with self.assertRaises(ValueError):
            create_backend("qubesadmin")


if __name__ == "__main__":
    unittest.main()
