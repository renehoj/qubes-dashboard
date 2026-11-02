"""Backend interface shared by the qubesadmin and demo implementations.

Same split as qubes-logs: the window only talks to a :class:`Backend`, and
everything crossing that boundary is plain data
(:class:`~qubes_backup.model.QubeInfo`, :class:`~qubes_backup.model.Revision`,
strings).
"""

from __future__ import annotations

import abc
import threading
from typing import Callable

from ..model import BackupRequest, Output, QubeInfo, RestoreRequest, Revision

#: Called from the worker thread with each piece of a tool's output.
Report = Callable[[Output], None]


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class Cancelled(BackendError):
    """A backup or restore stopped because Cancel was pressed."""


class Job:
    """A backup or restore in progress. ``cancel()`` may be called from any
    thread; the backend stops the tool and the call raises Cancelled."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._on_cancel: Callable[[], None] | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def on_cancel(self, callback: Callable[[], None] | None) -> None:
        """Set what cancel() does (the backend, once the tool runs). Called
        at once when cancel() came first."""
        with self._lock:
            self._on_cancel = callback
            run = callback is not None and self._cancelled
        if run:
            callback()

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callback = self._on_cancel
        if callback is not None:
            callback()


class Backend(abc.ABC):
    """Backup, restore and revert for qubes.

    Every method may block (qubesd calls, the tools), so the window calls them
    from worker threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False

    @abc.abstractmethod
    def qube_info(self, name: str) -> QubeInfo:
        """The qube's class and power state. Raises BackendError."""

    @abc.abstractmethod
    def storage_qubes(self) -> list[str]:
        """Where a backup can go or come from: dom0 first, then every qube
        that is not a template, by name."""

    @abc.abstractmethod
    def revisions(self, name: str) -> list[Revision]:
        """The private volume's revisions, newest first."""

    @abc.abstractmethod
    def list_backups(self, qube: str, directory: str, start: bool = False) -> list[str]:
        """The names of qvm-backup's files in ``directory`` of ``qube``, newest
        first (see ``model.backup_files``). A qube that is off is started only
        with ``start``; otherwise BackendError says it is off."""

    @abc.abstractmethod
    def backup(self, request: BackupRequest, job: Job, report: Report) -> None:
        """Run the backup; returns when it is done. Raises BackendError when
        it fails, Cancelled when ``job`` was cancelled."""

    @abc.abstractmethod
    def restore(self, request: RestoreRequest, job: Job, report: Report) -> None:
        """Run the restore (or the check, with ``verify_only``), like backup."""

    @abc.abstractmethod
    def revert(self, name: str, revision: str) -> None:
        """Revert the private volume of ``name``, which is off, to ``revision``."""
