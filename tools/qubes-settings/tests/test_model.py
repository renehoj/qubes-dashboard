"""Field rules, combo contents and change detection, without GTK."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_settings import model  # noqa: E402
from qubes_settings.backend.mock import MockBackend  # noqa: E402


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend(delay=0)

    def get(self, name):
        return self.backend.get_settings(name)

    def test_no_edits_no_changes(self):
        for name in ("office", "work", "dom0", "games", "sys-firewall"):
            with self.subTest(name=name):
                s = self.get(name)
                self.assertEqual(model.changes(s, model.initial_values(s)), {})

    def test_changes_are_backend_values(self):
        s = self.get("office")
        values = model.initial_values(s)
        values.update(netvm="sys-vpn", label="green", vcpus=4,
                      default_dispvm=model.NONE_ID, virt_mode="hvm")
        self.assertEqual(model.changes(s, values), {
            "netvm": "sys-vpn", "label": "green", "vcpus": 4,
            "default_dispvm": None, "virt_mode": "hvm",
        })

    def test_back_to_default(self):
        s = self.get("browser-social")  # netvm explicitly sys-vpn
        values = model.initial_values(s)
        values["netvm"] = model.DEFAULT_ID
        self.assertIs(model.changes(s, values)["netvm"], model.DEFAULT)

    def test_running_qube_keeps_template_and_memory(self):
        s = self.get("work")
        self.assertEqual(model.disabled_reason(s, model.TEMPLATE), model.HALTED_ONLY)
        self.assertEqual(model.disabled_reason(s, model.MEMORY), model.HALTED_ONLY)
        self.assertIsNone(model.disabled_reason(s, model.NETVM))
        values = model.initial_values(s)
        values.update(template="debian-13", memory=999, netvm=model.NONE_ID)
        self.assertEqual(model.changes(s, values), {"netvm": None})

    def test_dom0_has_label_and_backups_only(self):
        s = self.get("dom0")
        self.assertEqual(model.shown_fields(s), [model.LABEL, model.INCLUDE_IN_BACKUPS])
        values = model.initial_values(s)
        values.update(label="red", include_in_backups=False, netvm="sys-net")
        self.assertEqual(model.changes(s, values), {"include_in_backups": False})

    def test_template_row_only_with_template(self):
        self.assertIn(model.TEMPLATE, model.shown_fields(self.get("office")))
        self.assertNotIn(model.TEMPLATE, model.shown_fields(self.get("games")))
        self.assertNotIn(model.TEMPLATE, model.shown_fields(self.get("debian-13")))

    def test_disposable_template_only_for_app_and_standalone(self):
        self.assertIsNone(model.disabled_reason(self.get("games"), model.TEMPLATE_FOR_DISPVMS))
        self.assertEqual(model.disabled_reason(self.get("sys-firewall"), model.TEMPLATE_FOR_DISPVMS),
                         model.NOT_DISPOSABLE_BASE)

    def test_dispvm_template_choices(self):
        s = self.get("sys-usb")
        self.assertEqual(s.template_choices, ["browser-untrusted", "default-dvm"])

    def test_netvm_options(self):
        s = self.get("office")
        options = model.netvm_options(s)
        self.assertEqual(options[0], (model.DEFAULT_ID, "default (sys-firewall)"))
        self.assertEqual(options[1], (model.NONE_ID, "(none)"))
        self.assertIn(("sys-vpn", "sys-vpn"), options)

    def test_qube_is_not_its_own_netvm(self):
        s = self.get("sys-vpn")
        self.assertNotIn("sys-vpn", s.netvm_choices)

    def test_current_value_always_offered(self):
        options = model.vm_options(["a"], current="gone")
        self.assertEqual(options, [("a", "a"), ("gone", "gone")])
        options = model.vm_options(["a"], default="a", allow_none=True, current=model.NONE_ID)
        self.assertEqual([i for i, _t in options], [model.DEFAULT_ID, model.NONE_ID, "a"])

    def test_virt_mode_options(self):
        ids = [i for i, _t in model.virt_mode_options(self.get("office"))]
        self.assertEqual(ids, [model.DEFAULT_ID, "pvh", "hvm", "pv"])

    def test_ids_round_trip(self):
        for prop, expected in ((model.PropValue("x", True, "x"), model.DEFAULT),
                               (model.PropValue(None), None),
                               (model.PropValue("sys-net"), "sys-net")):
            with self.subTest(prop=prop):
                self.assertEqual(model.id_value(model.prop_id(prop)), expected)

    def test_menu_changes_ignore_order(self):
        s = self.get("office")
        values = model.initial_values(s)
        values["menu_items"] = list(reversed(values["menu_items"]))
        self.assertEqual(model.changes(s, values), {})

    def test_menu_changes_saved_in_picker_order(self):
        s = self.get("office")
        values = model.initial_values(s)
        values["menu_items"] = ["xterm.desktop", "org.gnome.Calculator.desktop"]
        self.assertEqual(model.changes(s, values),
                         {"menu_items": ["org.gnome.Calculator.desktop", "xterm.desktop"]})

    def test_unavailable_menu_item_is_listed_not_dropped(self):
        s = self.get("vault")  # has old-password-tool.desktop, not in its template
        idents = [e.ident for e in model.app_entries(s)]
        self.assertEqual(idents[-1], "old-password-tool.desktop")
        self.assertEqual(model.changes(s, model.initial_values(s)), {})

    def test_menu_editable_while_running(self):
        s = self.get("work")
        self.assertIsNone(model.disabled_reason(s, model.MENU_ITEMS))
        values = model.initial_values(s)
        values["menu_items"] = []
        self.assertEqual(model.changes(s, values), {"menu_items": []})

    def test_menu_not_editable_without_list(self):
        s = self.get("office")
        s.apps_error = "qvm-appmenus is not installed here"
        self.assertEqual(model.disabled_reason(s, model.MENU_ITEMS), model.NO_APP_LIST)
        values = model.initial_values(s)
        values["menu_items"] = []
        self.assertEqual(model.changes(s, values), {})

    def test_dom0_has_no_menu(self):
        self.assertNotIn(model.MENU_ITEMS, model.shown_fields(self.get("dom0")))

    def test_search(self):
        entry = model.AppEntry("org.gnome.TextEditor.desktop", "Text Editor")
        self.assertTrue(model.matches(entry, "edit"))
        self.assertTrue(model.matches(entry, "GNOME.text"))
        self.assertTrue(model.matches(entry, ""))
        self.assertFalse(model.matches(entry, "firefox"))

    def test_summary(self):
        self.assertEqual(model.summary(self.get("work")),
                         "AppVM · Running · debian-13-gnome-office")
        self.assertEqual(model.summary(self.get("dom0")), "AdminVM · Running")


if __name__ == "__main__":
    unittest.main()
