"""The demo backend behaves like qubesd closely enough to develop against."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_settings import model  # noqa: E402
from qubes_settings.backend import BackendError, create_backend  # noqa: E402
from qubes_settings.backend.mock import MockBackend  # noqa: E402


class MockBackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def test_apply_and_reset(self):
        self.backend.apply_settings("office", {"netvm": None, "label": "red", "memory": 800})
        s = self.backend.get_settings("office")
        self.assertIsNone(s.netvm.value)
        self.assertFalse(s.netvm.is_default)
        self.assertEqual((s.label, s.memory.value), ("red", 800))
        self.backend.apply_settings("office", {"netvm": model.DEFAULT})
        s = self.backend.get_settings("office")
        self.assertTrue(s.netvm.is_default)
        self.assertEqual(s.netvm.value, "sys-firewall")

    def test_menu_items(self):
        s = self.backend.get_settings("work")
        self.assertIn("thunderbird.desktop", s.menu_items)
        self.assertIn("libreoffice-writer.desktop", [a.ident for a in s.apps])
        self.backend.apply_settings("work", {"menu_items": ["xterm.desktop"]})
        self.assertEqual(self.backend.get_settings("work").menu_items, ["xterm.desktop"])

    def test_disposable_gets_template_chain_apps(self):
        # disp1234 -> browser-untrusted -> debian-13-gnome-browser
        apps = [a.ident for a in self.backend.get_settings("disp1234").apps]
        self.assertIn("torbrowser.desktop", apps)

    def test_template_refused_while_running(self):
        with self.assertRaisesRegex(BackendError, "shut down"):
            self.backend.apply_settings("work", {"template": "debian-13"})

    def test_unknown_qube(self):
        with self.assertRaisesRegex(BackendError, "No such qube"):
            self.backend.get_settings("nope")

    def test_set_states(self):
        self.backend.set_states({"work": model.HALTED, "unknown": model.RUNNING})
        self.assertTrue(self.backend.get_settings("work").is_halted)

    def test_covers_dashboard_demo_set(self):
        # qubes-dashboard's demo backend lists these; each must open here.
        for name in ("dom0", "disp1234", "vault", "work", "browser-streaming"):
            self.backend.get_settings(name)

    def test_create_backend(self):
        self.assertTrue(create_backend("mock").is_demo)
        with self.assertRaises(ValueError):
            create_backend("nope")


if __name__ == "__main__":
    unittest.main()
