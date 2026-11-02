"""Backend interface shared by the qubesadmin and demo implementations.

Same split as qubes-settings: the window only talks to a :class:`Backend`, and
everything crossing that boundary is plain data (:class:`QubeFirewall`, a
:class:`~qubes_firewall.rules.RuleList`), never qubesadmin objects.

Every save goes through :meth:`Backend.save_rules`, which is qvm-firewall-ui's
guarded write path in small: re-read the qube's rules and refuse if they changed
since the window read them, snapshot them, then write the whole list.
"""

from __future__ import annotations

import abc
import os
import time

from ..model import QubeFirewall
from ..rules import RuleList

SNAPSHOT_SUBDIR = "qubes-firewall"
SNAPSHOT_TS_FORMAT = "%Y%m%dT%H%M%SZ"


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class StaleRulesError(BackendError):
    """The rules changed elsewhere after the window read them."""


def snapshot_dir() -> str:
    """``$XDG_STATE_HOME/qubes-firewall``, else ``~/.local/state/qubes-firewall``."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, SNAPSHOT_SUBDIR)


def take_snapshot(name: str, rules: RuleList, directory: str | None = None,
                  timestamp: float | None = None) -> str:
    """Write ``rules`` to ``<dir>/<qube>.<UTC time>.rules`` and return the path.

    The file is the Admin API text (``qvm-firewall --raw <qube> list``), so it
    can be put back line by line with ``qvm-firewall <qube> add``. An existing
    snapshot is never overwritten; a same-second one gets a numeric suffix.
    (qvm-firewall-ui's ``adapter.take_snapshot``.)
    """
    directory = directory or snapshot_dir()
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime(SNAPSHOT_TS_FORMAT, time.gmtime(timestamp))
    path = os.path.join(directory, f"{name}.{stamp}.rules")
    candidate, counter = path, 1
    while True:
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            candidate = f"{path}-{counter}"
            counter += 1
            continue
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(rules.to_api_text())
        return candidate


class Backend(abc.ABC):
    """Reading and writing one qube's firewall rules.

    Both methods may block (qrexec calls), so the window calls them from worker
    threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False
    #: Where save_rules puts its snapshots; None means snapshot_dir().
    snapshot_directory: str | None = None

    @abc.abstractmethod
    def get_firewall(self, name: str) -> QubeFirewall:
        """The firewall of ``name``: its rules, in order, and its net qube."""

    @abc.abstractmethod
    def _read_rules(self, name: str) -> RuleList:
        """The rules qubesd holds for ``name`` right now (no cache)."""

    @abc.abstractmethod
    def _write_rules(self, name: str, rules: RuleList) -> None:
        """Replace the whole rule list of ``name``."""

    def save_rules(self, name: str, expected: RuleList, rules: RuleList) -> str:
        """Write ``rules``, if the qube still has ``expected``; the snapshot path.

        Raises StaleRulesError, having written nothing, when the rules changed
        since they were read. Otherwise the current list is snapshotted first,
        so a failed write leaves the old rules on disk.
        """
        current = self._read_rules(name)
        if current != expected:
            raise StaleRulesError(
                f"The firewall of {name} was changed elsewhere since this window "
                "read it. Nothing was saved; close and open it again.")
        try:
            path = take_snapshot(name, current, self.snapshot_directory)
        except OSError as exc:
            raise BackendError(f"Could not save a copy of the old rules: {exc}") from exc
        try:
            self._write_rules(name, rules)
        except BackendError as exc:
            raise BackendError(f"{exc} The old rules are in {path}.") from exc
        return path
