"""Backend interface shared by the qubesadmin and demo implementations.

Same split as qubes-firewall: the window only talks to a :class:`Backend`, and
everything crossing that boundary is plain data (:class:`QubeDevices`,
:class:`Assignment`), never qubesadmin objects.

Every save goes through :meth:`Backend.save_assignments`, qubes-firewall's
guarded write path for PCI assignments: refuse unless the qube is still shut
down and its assignments are still the ones the window read, snapshot them,
then unassign and assign what changed.
"""

from __future__ import annotations

import abc
import os
import time

from ..model import HALTED, Assignment, QubeDevices, snapshot_lines

SNAPSHOT_SUBDIR = "qubes-devices"
SNAPSHOT_TS_FORMAT = "%Y%m%dT%H%M%SZ"


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class StaleDevicesError(BackendError):
    """The assignments changed elsewhere after the window read them."""


def snapshot_dir() -> str:
    """``$XDG_STATE_HOME/qubes-devices``, else ``~/.local/state/qubes-devices``."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, SNAPSHOT_SUBDIR)


def take_snapshot(name: str, assignments: tuple[Assignment, ...],
                  directory: str | None = None, timestamp: float | None = None) -> str:
    """Write ``assignments`` to ``<dir>/<qube>.<UTC time>.pci`` and return the path.

    The file holds the ``qvm-pci assign`` commands that put them back, one per
    line; an empty file means the qube had none. An existing snapshot is never
    overwritten; a same-second one gets a numeric suffix.
    """
    directory = directory or snapshot_dir()
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime(SNAPSHOT_TS_FORMAT, time.gmtime(timestamp))
    path = os.path.join(directory, f"{name}.{stamp}.pci")
    candidate, counter = path, 1
    while True:
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            candidate = f"{path}-{counter}"
            counter += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(snapshot_lines(name, assignments))
        return candidate


def _sorted(assignments) -> tuple[Assignment, ...]:
    return tuple(sorted(assignments, key=lambda a: a.port_id))


class Backend(abc.ABC):
    """Reading and writing one qube's PCI assignments.

    Every method may block (qrexec calls), so the window calls them from
    worker threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False
    #: Where save_assignments puts its snapshots; None means snapshot_dir().
    snapshot_directory: str | None = None

    @abc.abstractmethod
    def get_devices(self, name: str) -> QubeDevices:
        """Every PCI device, and the qube's state and assignments."""

    @abc.abstractmethod
    def _power_state(self, name: str) -> str:
        """The qube's power state right now."""

    @abc.abstractmethod
    def _read_assignments(self, name: str) -> tuple[Assignment, ...]:
        """The qube's required PCI assignments right now (no cache)."""

    @abc.abstractmethod
    def _assign(self, name: str, assignment: Assignment) -> None:
        """Add one required assignment."""

    @abc.abstractmethod
    def _unassign(self, name: str, assignment: Assignment) -> None:
        """Remove the assignment of ``assignment.port_id``."""

    def save_assignments(self, name: str, expected: tuple[Assignment, ...],
                         assignments: tuple[Assignment, ...]) -> str:
        """Make ``assignments`` the qube's; the snapshot path.

        Raises BackendError, having written nothing, when the qube is no longer
        shut down, and StaleDevicesError when its assignments changed since
        they were read. Otherwise the current ones are snapshotted, then
        removed ones are unassigned, changed ones (strict reset) unassigned and
        assigned again, and new ones assigned.
        """
        state = self._power_state(name)
        if state != HALTED:
            raise BackendError(
                f"{name} is {state.lower()} now. Nothing was saved; shut it down "
                "and save again.")
        current = self._read_assignments(name)
        if _sorted(current) != _sorted(expected):
            raise StaleDevicesError(
                f"The devices of {name} were changed elsewhere since this window "
                "read them. Nothing was saved; close and open it again.")
        try:
            path = take_snapshot(name, _sorted(current), self.snapshot_directory)
        except OSError as exc:
            raise BackendError(f"Could not save a copy of the old assignments: {exc}") from exc

        old = {a.port_id: a for a in current}
        new = {a.port_id: a for a in assignments}
        steps = [(self._unassign, a) for p, a in sorted(old.items()) if new.get(p) != a]
        steps += [(self._assign, a) for p, a in sorted(new.items()) if old.get(p) != a]
        for call, assignment in steps:
            try:
                call(name, assignment)
            except BackendError as exc:
                raise BackendError(
                    f"{exc} Some changes may have been made; the old assignments "
                    f"are in {path}.") from exc
        return path
