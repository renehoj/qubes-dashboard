"""Backend talking to qubesd through qubesadmin (dom0 or a GUI qube).

Qubes 4.3's device API, the calls qube Settings' Devices tab makes:

* every PCI device is ``dom0.devices["pci"].get_exposed_devices()``
  (``admin.vm.device.pci.Available``), bridges left out;
* a qube's are ``vm.devices["pci"].get_assigned_devices(required_only=True)``
  (``admin.vm.device.pci.Assigned``), one call per qube, also for the other
  qubes so the window can say who else has a device;
* a save is ``assign`` / ``unassign`` of a ``DeviceAssignment`` with
  ``mode="required"``, ``no-strict-reset`` as an option. qubesd has no call that
  changes an assignment's options, so a changed one is unassigned and assigned
  again, as qube Settings does.
"""

from __future__ import annotations

import qubesadmin
import qubesadmin.exc
from qubesadmin.device_protocol import DeviceAssignment

from ..model import (
    ADMIN_VM, NA_STATE, NO_STRICT_RESET, Assignment, PciDevice, QubeDevices, is_true,
    merge_devices,
)
from .base import Backend, BackendError

PCI = "pci"
BRIDGE = "PCI_Bridge"


def _category(device) -> str:
    try:
        return device.interfaces[0].category.name
    except (AttributeError, IndexError):
        return ""


def _assignment(raw) -> Assignment:
    """Plain data for a qubesadmin ``DeviceAssignment``."""
    return Assignment(
        str(raw.port_id), str(raw.device_id or "*"),
        is_true(raw.options.get(NO_STRICT_RESET, False)),
    )


class QubesAdminBackend(Backend):
    """The real thing."""

    name = "qubesd"

    def __init__(self, app=None):
        self.app = app if app is not None else qubesadmin.Qubes()

    def _vm(self, name: str):
        try:
            return self.app.domains[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def _raw_assignments(self, vm) -> list:
        collection = vm.devices[PCI]
        clear = getattr(collection, "clear_cache", None)
        if clear is not None:
            clear()
        return list(collection.get_assigned_devices(required_only=True))

    def _owners(self, exclude: str) -> dict[str, list[str]]:
        """port_id: other qubes with a PCI assignment of it."""
        owners: dict[str, list[str]] = {}
        for vm in self.app.domains:
            if vm.name == exclude or vm.klass == ADMIN_VM:
                continue
            try:
                raw = self._raw_assignments(vm)
            except qubesadmin.exc.QubesException:
                continue  # not allowed to read that qube's devices
            for assignment in raw:
                owners.setdefault(str(assignment.port_id), []).append(vm.name)
        return owners

    def get_devices(self, name: str) -> QubeDevices:
        vm = self._vm(name)
        try:
            klass = vm.klass
            if klass == ADMIN_VM:
                own: tuple[Assignment, ...] = ()
                state, virt_mode, maxmem = vm.get_power_state(), "", 0
            else:
                own = tuple(_assignment(a) for a in self._raw_assignments(vm))
                state = vm.get_power_state()
                virt_mode = str(vm.virt_mode)
                maxmem = int(vm.maxmem)
            exposed_raw = list(self.app.domains["dom0"].devices[PCI].get_exposed_devices())
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(f"Could not read the devices of {name}: {exc}") from exc
        owners = self._owners(name)
        exposed = []
        for device in exposed_raw:
            if _category(device) == BRIDGE:
                continue
            attachment = getattr(device, "attachment", None)
            attached = attachment.name if attachment is not None else None
            port_id = str(device.port_id)
            exposed.append(PciDevice(
                port_id, str(device.device_id), str(device.description),
                # Only another qube's: this one has its own devices.
                attached_to=attached if attached != name else None,
                assigned_to=tuple(sorted(owners.get(port_id, ()))),
            ))
        return QubeDevices(name, klass, state or NA_STATE, virt_mode, maxmem,
                           merge_devices(exposed, own), own)

    def _power_state(self, name: str) -> str:
        try:
            return self._vm(name).get_power_state()
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(f"Could not read the state of {name}: {exc}") from exc

    def _read_assignments(self, name: str) -> tuple[Assignment, ...]:
        try:
            return tuple(_assignment(a) for a in self._raw_assignments(self._vm(name)))
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(f"Could not read the devices of {name}: {exc}") from exc

    def _assign(self, name: str, assignment: Assignment) -> None:
        vm = self._vm(name)
        options = {NO_STRICT_RESET: True} if assignment.no_strict_reset else {}
        device_id = None if assignment.device_id == "*" else assignment.device_id
        try:
            vm.devices[PCI].assign(DeviceAssignment.new(
                backend_domain=self.app.domains["dom0"], port_id=assignment.port_id,
                devclass=PCI, device_id=device_id, mode="required", options=options))
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(
                f"qubesd refused to assign {assignment.port_id}: {exc}") from exc

    def _unassign(self, name: str, assignment: Assignment) -> None:
        vm = self._vm(name)
        try:
            for raw in self._raw_assignments(vm):
                if str(raw.port_id) == assignment.port_id:
                    vm.devices[PCI].unassign(raw)
                    return
        except qubesadmin.exc.QubesException as exc:
            raise BackendError(
                f"qubesd refused to unassign {assignment.port_id}: {exc}") from exc
        raise BackendError(f"{assignment.port_id} is not assigned to {name}.")
