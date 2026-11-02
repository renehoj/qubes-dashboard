"""In-memory backend with demo data, for development outside dom0.

The qubes are qubes-dashboard's demo set, so every qube the list shows in
demo mode has a Devices window here. The PCI devices are a Raptor Lake desktop
with a discrete NVIDIA card: ``games`` has the card (both functions) and a
Fresco Logic USB controller, ``sys-net`` the network cards and ``sys-usb`` the
chipset's USB controller.
"""

from __future__ import annotations

import tempfile
import threading
import time

from ..model import (
    ADMIN_VM, HALTED, RUNNING, Assignment, PciDevice, QubeDevices, merge_devices,
)
from .base import Backend, BackendError

#: name: (class, state, virt_mode, maxmem). qubes-dashboard's demo states.
_QUBES = {
    "dom0": (ADMIN_VM, RUNNING, "", 0),
    "ai-llm": ("AppVM", RUNNING, "pvh", 4000),
    "browser-personal": ("AppVM", RUNNING, "pvh", 4000),
    "browser-social": ("AppVM", RUNNING, "pvh", 4000),
    "browser-streaming": ("AppVM", "Paused", "pvh", 4000),
    "browser-untrusted": ("AppVM", HALTED, "pvh", 4000),
    "disp1234": ("DispVM", RUNNING, "pvh", 4000),
    "debian-13": ("TemplateVM", HALTED, "pvh", 4000),
    "debian-13-gnome-browser": ("TemplateVM", HALTED, "pvh", 4000),
    "debian-13-gnome-devel": ("TemplateVM", HALTED, "pvh", 4000),
    "debian-13-gnome-office": ("TemplateVM", HALTED, "pvh", 4000),
    "debian-13-gnome-personal": ("TemplateVM", HALTED, "pvh", 4000),
    "debian-13-gnome-sys": ("TemplateVM", HALTED, "pvh", 4000),
    "debian-13-xfce": ("TemplateVM", HALTED, "pvh", 4000),
    "default-dvm": ("AppVM", HALTED, "pvh", 4000),
    "development": ("AppVM", HALTED, "pvh", 4000),
    "games": ("StandaloneVM", HALTED, "hvm", 0),
    "media": ("AppVM", HALTED, "pvh", 4000),
    "office": ("AppVM", HALTED, "pvh", 4000),
    "personal": ("AppVM", RUNNING, "pvh", 4000),
    "sys-firewall": ("DispVM", RUNNING, "pvh", 4000),
    "sys-net": ("AppVM", RUNNING, "hvm", 0),
    "sys-usb": ("DispVM", RUNNING, "hvm", 0),
    "sys-vpn": ("AppVM", RUNNING, "pvh", 4000),
    "sys-vpn-secure": ("AppVM", RUNNING, "pvh", 4000),
    "vault": ("AppVM", HALTED, "pvh", 4000),
    "work": ("AppVM", RUNNING, "pvh", 4000),
}

#: port_id: (device_id, description)
_DEVICES = {
    "00_01.0-00_00.0": ("10de:2803::p030000",
                        "Display: NVIDIA Corporation AD106 [GeForce RTX 4060 Ti]"),
    "00_01.0-00_00.1": ("10de:22bd::p040300",
                        "Audio: NVIDIA Corporation AD106M High Definition Audio Controller"),
    "00_02.0": ("8086:a780::p030000",
                "Display: Intel Corporation Raptor Lake-S GT1 [UHD Graphics 770]"),
    "00_06.0-00_00.0": ("c0a9:5426::p010802",
                        "Storage: Micron/Crucial Technology T705 NVMe PCIe SSD"),
    "00_08.0": ("8086:a74f::p088000",
                "System peripheral: Intel Corporation GNA Scoring Accelerator module"),
    "00_0a.0": ("8086:a77d::p118000",
                "Signal processing controller: Intel Corporation Raptor Lake Crashlog "
                "and Telemetry"),
    "00_14.0": ("8086:7a60::p0c0330",
                "PCI_USB: Intel Corporation Raptor Lake USB 3.2 Gen 2x2 (20 Gb/s) "
                "XHCI Host Controller"),
    "00_14.2": ("8086:7a27::p050000",
                "Memory: Intel Corporation Raptor Lake PCH Shared SRAM"),
    "00_14.3": ("8086:7a70::p028000",
                "Network: Intel Corporation 700 Series Chipset CNVi WiFi"),
    "00_16.0": ("8086:7a68::p078000",
                "Communication controller: Intel Corporation Raptor Lake CSME HECI #1"),
    "00_17.0": ("8086:7a62::p010601",
                "Storage: Intel Corporation Raptor Lake SATA AHCI Controller"),
    "00_1c.1-00_00.0": ("1b73:1100::p0c0330",
                        "PCI_USB: Fresco Logic FL1100 USB 3.0 Host Controller"),
    "00_1c.2-00_00.0": ("8086:125c::p020000",
                        "Network: Intel Corporation Ethernet Controller I225-V"),
    "00_1f.4": ("8086:7a23::p0c0500",
                "PCI_Serial_Bus: Intel Corporation 700 Series Chipset SMBus Controller"),
    "00_1f.5": ("8086:7a24::p0c8000",
                "PCI_Serial_Bus: Intel Corporation Raptor Lake SPI (flash) Controller"),
}

_ASSIGNED = {
    "games": ("00_01.0-00_00.0", "00_01.0-00_00.1", "00_1c.1-00_00.0"),
    "sys-net": ("00_14.3", "00_1c.2-00_00.0"),
    "sys-usb": ("00_14.0",),
}


class MockBackend(Backend):
    """Simulates a Qubes system. ``delay`` scales the simulated qrexec time."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self._lock = threading.RLock()
        self._states = {name: q[1] for name, q in _QUBES.items()}
        self._assignments: dict[str, dict[str, Assignment]] = {
            name: {p: Assignment(p, _DEVICES[p][0]) for p in ports}
            for name, ports in _ASSIGNED.items()
        }
        # Demo snapshots stay out of the real state directory.
        self.snapshot_directory = tempfile.mkdtemp(prefix="qubes-devices-demo-")

    def _sleep(self, seconds: float) -> None:
        if self.delay:
            time.sleep(seconds * self.delay)

    def _get(self, name: str) -> tuple:
        try:
            return _QUBES[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def set_states(self, states: dict[str, str]) -> None:
        """Take power states from elsewhere (the list's own demo backend).

        In demo mode qubes-dashboard and this backend each simulate a system;
        this keeps "only while shut down" in step with what the list shows.
        """
        with self._lock:
            for name, state in states.items():
                if name in self._states:
                    self._states[name] = state

    def get_devices(self, name: str) -> QubeDevices:
        self._sleep(0.3)
        with self._lock:
            klass, _state, virt_mode, maxmem = self._get(name)
            own = self._assignments.get(name, {}) if klass != ADMIN_VM else {}
            exposed = []
            for port_id, (device_id, description) in _DEVICES.items():
                owners = tuple(sorted(
                    other for other, ports in self._assignments.items()
                    if other != name and port_id in ports))
                attached = next((o for o in owners if self._states[o] == RUNNING), None)
                exposed.append(PciDevice(port_id, device_id, description,
                                         attached_to=attached, assigned_to=owners))
            assignments = tuple(own.values())
            return QubeDevices(name, klass, self._states[name], virt_mode, maxmem,
                               merge_devices(exposed, assignments), assignments)

    def _power_state(self, name: str) -> str:
        with self._lock:
            self._get(name)
            return self._states[name]

    def _read_assignments(self, name: str) -> tuple[Assignment, ...]:
        with self._lock:
            if self._get(name)[0] == ADMIN_VM:
                raise BackendError("Devices cannot be assigned to dom0.")
            return tuple(self._assignments.get(name, {}).values())

    def _assign(self, name: str, assignment: Assignment) -> None:
        self._sleep(0.2)
        with self._lock:
            self._assignments.setdefault(name, {})[assignment.port_id] = assignment

    def _unassign(self, name: str, assignment: Assignment) -> None:
        self._sleep(0.2)
        with self._lock:
            ports = self._assignments.get(name, {})
            if assignment.port_id not in ports:
                raise BackendError(f"{assignment.port_id} is not assigned to {name}.")
            del ports[assignment.port_id]

    def change_elsewhere(self, name: str, assignments: list[Assignment]) -> None:
        """Simulate another tool changing the assignments (tests)."""
        with self._lock:
            self._assignments[name] = {a.port_id: a for a in assignments}
