"""Backend interface shared by the qubesadmin and demo implementations.

Same split as qubes-manager's ``backend/base.py``: the window only talks to a
:class:`Backend`, and everything crossing that boundary is plain data
(:class:`~qubes_dashboard.model.Qube`, strings), never qubesadmin objects.
"""

from __future__ import annotations

import abc
import shutil

from ..model import App, Qube

#: Tried in order when qubes-settings cannot be opened in-process: our own
#: window as a command, then the stock one.
SETTINGS_COMMANDS = ("qubes-settings", "qubes-vm-settings")


def settings_command(which=shutil.which) -> str:
    """The first of SETTINGS_COMMANDS on PATH (the stock one if neither is)."""
    for command in SETTINGS_COMMANDS:
        if which(command):
            return command
    return SETTINGS_COMMANDS[-1]


#: Run when qubes-firewall cannot be opened in-process. There is no stock
#: equivalent: qube Settings' firewall tab gives up on hand-written rules.
FIREWALL_COMMAND = "qubes-firewall"

#: Run when qubes-devices cannot be opened in-process.
DEVICES_COMMAND = "qubes-devices"

#: Run when qubes-information cannot be opened in-process.
INFORMATION_COMMAND = "qubes-information"

#: Run when qubes-logs cannot be opened in-process.
LOGS_COMMAND = "qubes-logs"

#: Run when qubes-backup cannot be opened in-process.
BACKUP_COMMAND = "qubes-backup"

#: Console: the qube's console in a disposable's terminal (stock Qubes tool).
CONSOLE_COMMAND = "qvm-console-dispvm"

#: Starts one of a qube's applications, followed by the qube's name and
#: ``qubes.StartApp+<ident>``: what the Qubes menu's entries run. ``-a``
#: starts a qube that is off first.
START_APP_COMMAND = ("qvm-run", "-q", "-a", "--service", "--")


def start_app_argv(name: str, ident: str) -> list[str]:
    """The command line that starts application ``ident`` in qube ``name``."""
    return [*START_APP_COMMAND, name, f"qubes.StartApp+{ident}"]


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class Backend(abc.ABC):
    """Access to the qubes in the system.

    ``list_qubes``, ``power_states``, ``start``, ``shutdown`` and ``restart``
    may block (qrexec calls, waiting for a qube to halt), so the window calls
    them from worker threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False

    @abc.abstractmethod
    def list_qubes(self) -> list[Qube]:
        """Every qube, with label, template and state (slower)."""

    @abc.abstractmethod
    def power_states(self) -> dict[str, str]:
        """Power state of every qube, by name (one cheap call)."""

    @abc.abstractmethod
    def start(self, name: str) -> None:
        """Start a halted qube, or resume a paused one."""

    @abc.abstractmethod
    def shutdown(self, name: str) -> None:
        """Shut a qube down. Returns once qubesd has accepted the request."""

    @abc.abstractmethod
    def restart(self, name: str) -> None:
        """Shut down, wait until halted, and start again."""

    @abc.abstractmethod
    def open_settings(self, name: str) -> None:
        """Run the settings command for ``name`` (see settings_command).

        Must not block. Only used when qubes-settings cannot be imported; the
        frontend opens its window in-process otherwise.
        """

    @abc.abstractmethod
    def open_firewall(self, name: str) -> None:
        """Run FIREWALL_COMMAND for ``name``.

        Must not block. Only used when qubes-firewall cannot be imported; the
        frontend opens its window in-process otherwise.
        """

    @abc.abstractmethod
    def open_devices(self, name: str) -> None:
        """Run DEVICES_COMMAND for ``name``.

        Must not block. Only used when qubes-devices cannot be imported; the
        frontend opens its window in-process otherwise.
        """

    @abc.abstractmethod
    def open_information(self, name: str) -> None:
        """Run INFORMATION_COMMAND for ``name``.

        Must not block. Only used when qubes-information cannot be imported;
        the frontend opens its window in-process otherwise.
        """

    @abc.abstractmethod
    def open_logs(self, name: str) -> None:
        """Run LOGS_COMMAND for ``name``.

        Must not block. Only used when qubes-logs cannot be imported; the
        frontend opens its window in-process otherwise.
        """

    @abc.abstractmethod
    def open_backup(self, name: str) -> None:
        """Run BACKUP_COMMAND for ``name``.

        Must not block. Only used when qubes-backup cannot be imported; the
        frontend opens its window in-process otherwise.
        """

    @abc.abstractmethod
    def open_console(self, name: str) -> None:
        """Run CONSOLE_COMMAND for ``name``, a running qube. Must not block."""

    @abc.abstractmethod
    def list_apps(self, name: str) -> list[App]:
        """The applications chosen for ``name``'s menu, in their order.

        These are the qube's ``menu-items`` feature, the list its Settings'
        Applications tab edits. May block.
        """

    @abc.abstractmethod
    def start_app(self, name: str, ident: str) -> None:
        """Run start_app_argv for ``name``, which may be off. Must not block."""
