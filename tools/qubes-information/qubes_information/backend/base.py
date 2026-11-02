"""Backend interface shared by the qubesadmin and demo implementations.

Same split as qubes-settings and qubes-firewall: the window only talks to a
:class:`Backend`, and everything crossing that boundary is plain data
(:class:`~qubes_information.model.QubeInfo`), never qubesadmin objects.
Nothing here writes to a qube.
"""

from __future__ import annotations

import abc

from ..model import LiveStats, QubeInfo


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class Backend(abc.ABC):
    """Reading one qube's information.

    Both methods may block (qrexec calls), so the window calls them from
    worker threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False

    @abc.abstractmethod
    def get_info(self, name: str) -> QubeInfo:
        """Everything about ``name``. A part that cannot be read is left
        empty and named in ``QubeInfo.errors``; only an unknown qube or an
        unreachable qubesd raises BackendError."""

    @abc.abstractmethod
    def get_stats(self, name: str) -> LiveStats | None:
        """The live state of ``name`` (one cheap call); None when it is not
        running or the state cannot be read."""
