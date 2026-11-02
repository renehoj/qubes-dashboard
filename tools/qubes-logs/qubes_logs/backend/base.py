"""Backend interface shared by the file and demo implementations.

Same split as qubes-information: the window only talks to a :class:`Backend`,
and everything crossing that boundary is plain data
(:class:`~qubes_logs.model.LogText`). Nothing here writes to a log.
"""

from __future__ import annotations

import abc

from ..model import LogText, log_files


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class Backend(abc.ABC):
    """Reading one qube's logs.

    Both methods may block (file I/O), so the window calls them from worker
    threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False

    def read_logs(self, name: str) -> list[LogText]:
        """The end of each of ``name``'s logs, in tab order. A file that
        cannot be read comes back with ``error`` set; this never raises for
        one file."""
        return [self.read_log(file) for file in log_files(name)]

    @abc.abstractmethod
    def read_log(self, file) -> LogText:
        """The last ``model.MAX_BYTES`` of ``file`` (a ``model.LogFile``)."""

    @abc.abstractmethod
    def copy_to_global_clipboard(self, text: str) -> None:
        """Put ``text`` on the Qubes global clipboard, as if copied from dom0
        with Ctrl+Shift+C, so Ctrl+Shift+V pastes it into a qube. Raises
        BackendError when it cannot."""
