"""Backend interface shared by the qubesadmin and demo implementations.

Same split as qubes-manager's ``backend/base.py``: the window only talks to a
:class:`Backend`, and everything crossing that boundary is plain data
(:class:`~qubes_settings.model.QubeSettings`, strings, ints), never qubesadmin
objects.
"""

from __future__ import annotations

import abc
from typing import Any

from ..model import QubeSettings


class BackendError(Exception):
    """An operation failed; the message is meant to be shown to the user."""


class Backend(abc.ABC):
    """Reading and writing one qube's settings.

    Both methods may block (qrexec calls), so the window calls them from worker
    threads. Implementations must be thread-safe.
    """

    #: Shown in the window when it is not the real system.
    name = "backend"
    #: True when nothing this backend does touches a real system.
    is_demo = False

    @abc.abstractmethod
    def get_settings(self, name: str) -> QubeSettings:
        """The settings of ``name``, with the choices for its combo boxes."""

    @abc.abstractmethod
    def apply_settings(self, name: str, changes: dict[str, Any]) -> None:
        """Set properties. A value of DEFAULT resets the property."""
