"""What the devices window shows and edits, without GTK or qubesadmin.

A qube's PCI devices as qube Settings' Devices tab has them: every PCI device
dom0 exposes (bridges left out), and which of them are assigned to the qube --
``required``, so they are attached whenever it starts. Each assignment can turn
off strict reset (``no-strict-reset``) for a device that cannot be reset.

This module has:

* :class:`PciDevice`, :class:`Assignment` and :class:`QubeDevices`, the plain
  data a backend hands the window;
* the cells of a device's row (:func:`device_cells`), the filter, and the
  window's subtitle;
* :class:`EditSession`, the working copy the window changes until Save;
* :func:`check_save`, the warnings shown before anything is written.

Assignments can only change while the qube is shut down, as in qube Settings:
qubesd attaches PCI devices when a qube starts.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable

ADMIN_VM = "AdminVM"

# Power states as reported by qubesd (``vm.get_power_state()``).
HALTED = "Halted"
RUNNING = "Running"
NA_STATE = "NA"

#: The assignment option that lets a device without a working reset be used.
NO_STRICT_RESET = "no-strict-reset"

#: Column titles of the device list, matching :func:`device_cells` (after the
#: check box).
COLUMNS = ("Address", "Class", "Device", "Reset", "Used by")

RUNNING_NOTE = ("{name} is {state}. Shut it down to change its devices; "
                "PCI devices are attached when a qube starts.")
ADMIN_NOTE = ("dom0 has every PCI device no qube has; devices cannot be "
              "assigned to it.")
UNKNOWN_DEVICE = "not found in dom0"


@dataclasses.dataclass(frozen=True)
class Assignment:
    """One ``required`` PCI assignment of a qube."""

    port_id: str
    #: The device's identity at that port, or ``*`` for any device there.
    device_id: str = "*"
    no_strict_reset: bool = False


@dataclasses.dataclass(frozen=True)
class PciDevice:
    """One PCI device dom0 exposes (or one assigned that it no longer has)."""

    #: qubesd's port, e.g. ``00_01.0-00_00.0``: behind root port 00:01.0,
    #: function 00.0 on the bus behind it.
    port_id: str
    device_id: str = "*"
    #: qubesadmin's ``DeviceInfo.description``: ``<class>: <vendor> <product>``.
    description: str = ""
    #: Another qube it is attached to now, if any.
    attached_to: str | None = None
    #: Other qubes it is assigned to.
    assigned_to: tuple[str, ...] = ()
    #: False for an assignment whose device dom0 does not have (any more).
    present: bool = True

    @property
    def address(self) -> str:
        return address_text(self.port_id)

    @property
    def class_text(self) -> str:
        return split_description(self.description)[0]

    @property
    def name_text(self) -> str:
        return split_description(self.description)[1]

    @property
    def slot(self) -> str:
        """The port without its function: all functions of one card share it."""
        return self.port_id.rsplit(".", 1)[0]

    @property
    def behind_bridge(self) -> bool:
        """On a card behind a root port, not built into the chipset."""
        return "-" in self.port_id


@dataclasses.dataclass
class QubeDevices:
    """One qube's PCI devices, as read from the backend."""

    name: str
    klass: str
    state: str
    #: ``hvm``, ``pvh`` or ``pv``.
    virt_mode: str
    #: 0 when the qube is not in memory balancing.
    maxmem: int
    #: Every device the window lists, in address order.
    devices: list[PciDevice]
    assignments: tuple[Assignment, ...]

    @property
    def is_admin(self) -> bool:
        return self.klass == ADMIN_VM

    @property
    def editable(self) -> bool:
        return not self.is_admin and self.state == HALTED

    def device(self, port_id: str) -> PciDevice | None:
        for device in self.devices:
            if device.port_id == port_id:
                return device
        return None


def address_text(port_id: str) -> str:
    """``00_01.0-00_00.0`` as qube Settings shows it: ``00:01.0 -> 00:00.0``."""
    return port_id.replace("_", ":").replace("-", " -> ")


def split_description(description: str) -> tuple[str, str]:
    """``("Display", "NVIDIA Corporation AD106 ...")`` from a description."""
    head, sep, tail = description.partition(": ")
    if not sep:
        return "", description
    return head.replace("_", " "), tail


def is_true(value: object) -> bool:
    """An option value from qubesd: ``True``, ``"True"``, ``"yes"``, ..."""
    return str(value).strip().lower() in ("true", "yes", "1", "on")


def sort_key(port_id: str) -> tuple:
    return tuple(port_id.replace("-", "_").replace(".", "_").split("_"))


def merge_devices(exposed: Iterable[PciDevice],
                  assignments: Iterable[Assignment]) -> list[PciDevice]:
    """The exposed devices plus an entry for each assigned port dom0 lacks."""
    devices = {device.port_id: device for device in exposed}
    for assignment in assignments:
        if assignment.port_id not in devices:
            devices[assignment.port_id] = PciDevice(
                assignment.port_id, assignment.device_id, present=False)
    return sorted(devices.values(), key=lambda d: sort_key(d.port_id))


def summary(qd: QubeDevices, assigned: int | None = None) -> str:
    """The window's subtitle: state, virtualization and the device count."""
    if qd.is_admin:
        return "dom0"
    count = len(qd.assignments) if assigned is None else assigned
    devices = "1 device" if count == 1 else f"{count} devices"
    return f"{qd.state.lower()} · {qd.virt_mode.upper() or 'default'} · {devices} assigned"


# -- rows ----------------------------------------------------------------------
def used_by_text(device: PciDevice) -> str:
    """Which other qubes have the device: assigned, and attached right now."""
    parts = []
    for name in device.assigned_to:
        parts.append(f"{name} (running)" if name == device.attached_to else name)
    if device.attached_to and device.attached_to not in device.assigned_to:
        parts.append(f"{device.attached_to} (attached)")
    return ", ".join(parts)


def reset_text(assignment: Assignment | None) -> str:
    if assignment is None:
        return ""
    return "not strict" if assignment.no_strict_reset else "strict"


def device_cells(device: PciDevice, assignment: Assignment | None) -> tuple[str, ...]:
    """The row for ``device``, one string per entry of COLUMNS."""
    if device.present:
        class_text, name = device.class_text, device.name_text
    else:
        class_text, name = "", UNKNOWN_DEVICE
    return (device.address, class_text, name, reset_text(assignment),
            used_by_text(device))


def matches(device: PciDevice, query: str) -> bool:
    """Every word of ``query`` is in the row's address, class, name or users."""
    haystack = " ".join((device.address, device.port_id, device.description,
                         used_by_text(device))).casefold()
    return all(token in haystack for token in query.casefold().split())


# -- the working copy ------------------------------------------------------------
class EditSession:
    """The qube's assignments as edited in the window, against those read."""

    def __init__(self, qd: QubeDevices):
        self.qd = qd
        self.original = {a.port_id: a for a in qd.assignments}
        self.current = dict(self.original)

    @property
    def dirty(self) -> bool:
        return self.current != self.original

    def assignment(self, port_id: str) -> Assignment | None:
        return self.current.get(port_id)

    def is_assigned(self, port_id: str) -> bool:
        return port_id in self.current

    def toggle(self, port_id: str) -> bool:
        """Assign or unassign a device; whether it is assigned now.

        Unassigning and assigning again gives back the assignment as it was
        read, strict reset included.
        """
        if port_id in self.current:
            del self.current[port_id]
            return False
        if port_id in self.original:
            self.current[port_id] = self.original[port_id]
        else:
            device = self.qd.device(port_id)
            device_id = device.device_id if device is not None else "*"
            self.current[port_id] = Assignment(port_id, device_id)
        return True

    def toggle_strict_reset(self, port_id: str) -> bool | None:
        """Flip no-strict-reset of an assigned device; None when unassigned."""
        assignment = self.current.get(port_id)
        if assignment is None:
            return None
        self.current[port_id] = dataclasses.replace(
            assignment, no_strict_reset=not assignment.no_strict_reset)
        return self.current[port_id].no_strict_reset

    def assignments(self) -> tuple[Assignment, ...]:
        """The working copy, in address order."""
        return tuple(sorted(self.current.values(), key=lambda a: sort_key(a.port_id)))

    def changes(self) -> tuple[list[Assignment], list[Assignment], list[Assignment]]:
        """``(added, removed, changed)``: what a save sends to qubesd.

        A changed assignment (strict reset) is unassigned and assigned again;
        qubesd cannot change the options of an assignment in place.
        """
        added = [a for p, a in self.current.items() if p not in self.original]
        removed = [a for p, a in self.original.items() if p not in self.current]
        changed = [a for p, a in self.current.items()
                   if p in self.original and self.original[p] != a]
        key = lambda a: sort_key(a.port_id)  # noqa: E731
        return sorted(added, key=key), sorted(removed, key=key), sorted(changed, key=key)


def _label(device: PciDevice | None, port_id: str) -> str:
    if device is None or not device.present:
        return address_text(port_id)
    return f"{device.address} ({device.name_text or device.class_text})"


def check_save(qd: QubeDevices, session: EditSession) -> list[str]:
    """Warnings for writing ``session``'s assignments to ``qd``'s qube.

    The window shows them and asks before saving. Nothing here stops a save:
    qubesd itself refuses what cannot be done.
    """
    warnings: list[str] = []
    assigned = session.assignments()
    added, _removed, changed = session.changes()
    if assigned and qd.virt_mode == "pvh":
        warnings.append(
            f"{qd.name} uses PVH, which cannot have PCI devices: it will not "
            "start. Set Virtualization to HVM in its Settings.")
    if assigned and qd.maxmem > 0:
        warnings.append(
            f"{qd.name} is in memory balancing. A qube with PCI devices should "
            "have fixed memory: turn balancing off in its Settings.")

    for assignment in added:
        device = qd.device(assignment.port_id)
        if device is None:
            continue
        others = [n for n in device.assigned_to if n != qd.name]
        if others:
            warnings.append(
                f"{_label(device, assignment.port_id)} is also assigned to "
                f"{', '.join(others)}. Only one of them can run at a time.")
        elif device.attached_to and device.attached_to != qd.name:
            warnings.append(
                f"{_label(device, assignment.port_id)} is attached to "
                f"{device.attached_to} now. {qd.name} cannot start while it is.")

    for assignment in added + changed:
        if assignment.no_strict_reset and not (
                session.original.get(assignment.port_id)
                and session.original[assignment.port_id].no_strict_reset):
            device = qd.device(assignment.port_id)
            warnings.append(
                f"Strict reset is off for {_label(device, assignment.port_id)}. "
                "What this qube leaves in the device may reach the next qube "
                "that gets it.")

    warnings.extend(_split_cards(qd, session))
    return warnings


def _split_cards(qd: QubeDevices, session: EditSession) -> list[str]:
    """A card behind a root port whose functions are only partly assigned.

    A GPU and its HDMI audio are two functions of one card, and the card
    usually only works with both in the same qube. Devices built into the
    chipset share slots without belonging together, so they are left out.
    """
    slots: dict[str, list[PciDevice]] = {}
    for device in qd.devices:
        if device.present and device.behind_bridge:
            slots.setdefault(device.slot, []).append(device)
    warnings = []
    for functions in slots.values():
        assigned = [d for d in functions if session.is_assigned(d.port_id)]
        if not assigned or len(assigned) == len(functions):
            continue
        missing = [d for d in functions if not session.is_assigned(d.port_id)]
        warnings.append(
            f"{', '.join(_label(d, d.port_id) for d in assigned)} is assigned "
            f"without {', '.join(_label(d, d.port_id) for d in missing)}, on the "
            "same card. Most cards need every function in the same qube.")
    return warnings


def snapshot_lines(name: str, assignments: Iterable[Assignment]) -> str:
    """Commands that put ``assignments`` back (``qvm-pci assign``)."""
    lines = []
    for a in assignments:
        option = f" -o {NO_STRICT_RESET}=True" if a.no_strict_reset else ""
        device = f"dom0:{a.port_id}" + (f":{a.device_id}" if a.device_id != "*" else "")
        port_only = " --port" if a.device_id == "*" else ""
        lines.append(f"qvm-pci assign --required{port_only}{option} {name} {device}\n")
    return "".join(lines)
