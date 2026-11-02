"""The device rows, the working copy and the save check."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qubes_devices import model  # noqa: E402
from qubes_devices.model import Assignment, PciDevice, QubeDevices  # noqa: E402

GPU = PciDevice("00_01.0-00_00.0", "10de:2803::p030000",
                "Display: NVIDIA Corporation AD106 [GeForce RTX 4060 Ti]")
GPU_AUDIO = PciDevice("00_01.0-00_00.1", "10de:22bd::p040300",
                      "Audio: NVIDIA Corporation AD106M High Definition Audio Controller")
USB = PciDevice("00_14.0", "8086:7a60::p0c0330",
                "PCI_USB: Intel Corporation Raptor Lake USB 3.2 XHCI Host Controller",
                attached_to="sys-usb", assigned_to=("sys-usb",))
SRAM = PciDevice("00_14.2", "8086:7a27::p050000",
                 "Memory: Intel Corporation Raptor Lake PCH Shared SRAM")
NIC = PciDevice("00_1c.2-00_00.0", "8086:125c::p020000",
                "Network: Intel Corporation Ethernet Controller I225-V",
                assigned_to=("sys-net",))


def qube(*assigned, state=model.HALTED, virt_mode="hvm", maxmem=0, devices=None):
    assignments = tuple(Assignment(d.port_id, d.device_id) for d in assigned)
    devices = devices or [GPU, GPU_AUDIO, USB, SRAM, NIC]
    return QubeDevices("games", "StandaloneVM", state, virt_mode, maxmem,
                       model.merge_devices(devices, assignments), assignments)


class TextTest(unittest.TestCase):
    def test_address(self):
        self.assertEqual(model.address_text("00_01.0-00_00.0"), "00:01.0 -> 00:00.0")
        self.assertEqual(model.address_text("00_14.0"), "00:14.0")

    def test_description_split(self):
        self.assertEqual(USB.class_text, "PCI USB")
        self.assertEqual(GPU.name_text, "NVIDIA Corporation AD106 [GeForce RTX 4060 Ti]")
        self.assertEqual(model.split_description("no class"), ("", "no class"))

    def test_cells(self):
        cells = model.device_cells(GPU, Assignment(GPU.port_id, no_strict_reset=True))
        self.assertEqual(cells, ("00:01.0 -> 00:00.0", "Display",
                                 "NVIDIA Corporation AD106 [GeForce RTX 4060 Ti]",
                                 "not strict", ""))
        self.assertEqual(model.device_cells(SRAM, None)[3], "")

    def test_used_by(self):
        self.assertEqual(model.used_by_text(USB), "sys-usb (running)")
        self.assertEqual(model.used_by_text(NIC), "sys-net")
        attached = PciDevice("00_02.0", attached_to="work")
        self.assertEqual(model.used_by_text(attached), "work (attached)")

    def test_unknown_assigned_device_is_listed(self):
        qd = QubeDevices("games", "StandaloneVM", model.HALTED, "hvm", 0,
                         model.merge_devices([GPU], [Assignment("00_03.0")]),
                         (Assignment("00_03.0"),))
        self.assertEqual([d.port_id for d in qd.devices], ["00_01.0-00_00.0", "00_03.0"])
        self.assertFalse(qd.device("00_03.0").present)
        self.assertEqual(model.device_cells(qd.device("00_03.0"), None)[2],
                         model.UNKNOWN_DEVICE)

    def test_addresses_sort_numerically_by_field(self):
        devices = model.merge_devices([NIC, SRAM, GPU, USB], [])
        self.assertEqual([d.port_id for d in devices],
                         ["00_01.0-00_00.0", "00_14.0", "00_14.2", "00_1c.2-00_00.0"])

    def test_filter(self):
        self.assertTrue(model.matches(GPU, "nvidia 4060"))
        self.assertTrue(model.matches(USB, "sys-usb"))
        self.assertTrue(model.matches(GPU, "00:01.0"))
        self.assertFalse(model.matches(GPU, "intel"))

    def test_is_true(self):
        for value in (True, "True", "yes", "1"):
            self.assertTrue(model.is_true(value))
        for value in (False, "False", "", "no"):
            self.assertFalse(model.is_true(value))

    def test_summary(self):
        self.assertEqual(model.summary(qube(GPU)), "halted · HVM · 1 device assigned")
        self.assertEqual(model.summary(qube(GPU), assigned=2),
                         "halted · HVM · 2 devices assigned")

    def test_editable_only_when_halted(self):
        self.assertTrue(qube().editable)
        self.assertFalse(qube(state=model.RUNNING).editable)
        admin = QubeDevices("dom0", model.ADMIN_VM, model.RUNNING, "", 0, [], ())
        self.assertFalse(admin.editable)


class EditSessionTest(unittest.TestCase):
    def test_toggle(self):
        session = model.EditSession(qube(GPU))
        self.assertFalse(session.dirty)
        self.assertTrue(session.toggle(GPU_AUDIO.port_id))
        self.assertEqual(session.assignment(GPU_AUDIO.port_id).device_id, GPU_AUDIO.device_id)
        self.assertTrue(session.dirty)
        self.assertFalse(session.toggle(GPU_AUDIO.port_id))
        self.assertFalse(session.dirty)

    def test_reassigning_keeps_the_original(self):
        qd = qube()
        qd.assignments = (Assignment(GPU.port_id, "*", no_strict_reset=True),)
        session = model.EditSession(qd)
        session.toggle(GPU.port_id)
        session.toggle(GPU.port_id)
        self.assertFalse(session.dirty)
        self.assertTrue(session.assignment(GPU.port_id).no_strict_reset)

    def test_strict_reset(self):
        session = model.EditSession(qube(GPU))
        self.assertIsNone(session.toggle_strict_reset(SRAM.port_id))
        self.assertTrue(session.toggle_strict_reset(GPU.port_id))
        self.assertEqual(session.changes(), ([], [], [session.assignment(GPU.port_id)]))
        self.assertFalse(session.toggle_strict_reset(GPU.port_id))
        self.assertFalse(session.dirty)

    def test_changes(self):
        session = model.EditSession(qube(GPU, GPU_AUDIO))
        session.toggle(GPU_AUDIO.port_id)
        session.toggle(SRAM.port_id)
        added, removed, changed = session.changes()
        self.assertEqual([a.port_id for a in added], [SRAM.port_id])
        self.assertEqual([a.port_id for a in removed], [GPU_AUDIO.port_id])
        self.assertEqual(changed, [])
        self.assertEqual([a.port_id for a in session.assignments()],
                         [GPU.port_id, SRAM.port_id])


class CheckSaveTest(unittest.TestCase):
    def check(self, qd, *toggles, reset=()):
        session = model.EditSession(qd)
        for port_id in toggles:
            session.toggle(port_id)
        for port_id in reset:
            session.toggle_strict_reset(port_id)
        return model.check_save(qd, session)

    def test_whole_card_is_quiet(self):
        self.assertEqual(self.check(qube(), GPU.port_id, GPU_AUDIO.port_id), [])

    def test_half_a_card(self):
        warnings = self.check(qube(), GPU.port_id)
        self.assertEqual(len(warnings), 1)
        self.assertIn("without 00:01.0 -> 00:00.1", warnings[0])

    def test_chipset_functions_are_not_a_card(self):
        # 00:14.0 and 00:14.2 share a slot but are separate devices.
        warnings = self.check(qube(), SRAM.port_id)
        self.assertEqual(warnings, [])

    def test_pvh(self):
        warnings = self.check(qube(virt_mode="pvh"), SRAM.port_id)
        self.assertIn("PVH", warnings[0])
        self.assertEqual(self.check(qube(virt_mode="pvh")), [])

    def test_memory_balancing(self):
        warnings = self.check(qube(maxmem=4000), SRAM.port_id)
        self.assertIn("memory balancing", warnings[0])

    def test_assigned_elsewhere(self):
        warnings = self.check(qube(), NIC.port_id)
        self.assertIn("also assigned to sys-net", warnings[0])

    def test_already_assigned_is_not_warned_again(self):
        self.assertEqual(self.check(qube(NIC)), [])

    def test_attached_elsewhere(self):
        attached = PciDevice("00_02.0", "*", "Display: Intel", attached_to="work")
        warnings = self.check(qube(devices=[attached]), attached.port_id)
        self.assertIn("attached to work now", warnings[0])

    def test_strict_reset_off(self):
        warnings = self.check(qube(SRAM), reset=[SRAM.port_id])
        self.assertIn("Strict reset is off", warnings[0])

    def test_strict_reset_on_again(self):
        qd = qube()
        qd.assignments = (Assignment(SRAM.port_id, "*", no_strict_reset=True),)
        self.assertEqual(self.check(qd, reset=[SRAM.port_id]), [])


class SnapshotLinesTest(unittest.TestCase):
    def test_lines(self):
        text = model.snapshot_lines("games", [
            Assignment("00_01.0-00_00.0", "10de:2803::p030000"),
            Assignment("00_14.0", "*", no_strict_reset=True),
        ])
        self.assertEqual(text.splitlines(), [
            "qvm-pci assign --required games dom0:00_01.0-00_00.0:10de:2803::p030000",
            "qvm-pci assign --required --port -o no-strict-reset=True games dom0:00_14.0",
        ])


if __name__ == "__main__":
    unittest.main()
