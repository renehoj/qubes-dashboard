"""The demo backend behaves like qubesd closely enough to develop against."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_dashboard import model  # noqa: E402
from qubes_dashboard.backend import BackendError, create_backend  # noqa: E402
from qubes_dashboard.backend.mock import MockBackend  # noqa: E402


class MockBackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def state(self, name):
        return self.backend.power_states()[name]

    def test_list_and_states_agree(self):
        qubes = self.backend.list_qubes()
        self.assertEqual({q.name: q.state for q in qubes}, self.backend.power_states())

    def test_start_brings_up_netvm(self):
        self.backend.shutdown("work")
        self.assertEqual(self.state("work"), model.HALTED)
        self.backend.start("development")
        self.assertEqual(self.state("development"), model.RUNNING)

    def test_resume_paused(self):
        self.backend.start("browser-streaming")
        self.assertEqual(self.state("browser-streaming"), model.RUNNING)

    def test_restart(self):
        self.backend.restart("work")
        self.assertEqual(self.state("work"), model.RUNNING)

    def test_cannot_stop_netvm_in_use(self):
        with self.assertRaises(BackendError):
            self.backend.shutdown("sys-firewall")

    def test_dom0_cannot_be_stopped(self):
        with self.assertRaises(BackendError):
            self.backend.shutdown("dom0")

    def test_settings_recorded(self):
        self.backend.open_settings("work")
        self.assertEqual(self.backend.settings_opened, ["work"])

    def test_firewall_recorded(self):
        self.backend.open_firewall("work")
        self.assertEqual(self.backend.firewall_opened, ["work"])

    def test_devices_recorded(self):
        self.backend.open_devices("games")
        self.assertEqual(self.backend.devices_opened, ["games"])

    def test_information_recorded(self):
        self.backend.open_information("work")
        self.assertEqual(self.backend.information_opened, ["work"])

    def test_logs_recorded(self):
        self.backend.open_logs("work")
        self.assertEqual(self.backend.logs_opened, ["work"])

    def test_backup_recorded(self):
        self.backend.open_backup("work")
        self.assertEqual(self.backend.backup_opened, ["work"])

    def test_console_recorded(self):
        self.backend.open_console("work")
        self.assertEqual(self.backend.console_opened, ["work"])

    def test_unknown_qube(self):
        with self.assertRaises(BackendError):
            self.backend.open_settings("nope")
        with self.assertRaises(BackendError):
            self.backend.open_firewall("nope")
        with self.assertRaises(BackendError):
            self.backend.open_devices("nope")
        with self.assertRaises(BackendError):
            self.backend.open_information("nope")
        with self.assertRaises(BackendError):
            self.backend.open_logs("nope")
        with self.assertRaises(BackendError):
            self.backend.open_backup("nope")
        with self.assertRaises(BackendError):
            self.backend.open_console("nope")

    def test_create_backend(self):
        self.assertTrue(create_backend("mock").is_demo)
        with self.assertRaises(ValueError):
            create_backend("bogus")


if __name__ == "__main__":
    unittest.main()
