"""Backends reading a qube's log files."""

import os

from .base import Backend, BackendError  # noqa: F401

KINDS = ("auto", "files", "mock")

#: Only dom0 has the qubes' logs; a qube has /var/log/qubes of its own.
XEN_CONSOLE_DIR = "/var/log/xen/console"


def files_available() -> bool:
    return os.path.isdir(XEN_CONSOLE_DIR)


def create_backend(kind: str = "auto") -> Backend:
    """Create a backend: ``files``, ``mock`` or ``auto``.

    ``auto`` reads the real files when they are there (dom0) and demo logs
    otherwise, so the window can be developed on any Linux box.
    """
    if kind == "auto":
        kind = "files" if files_available() else "mock"
    if kind == "files":
        # pylint: disable=import-outside-toplevel
        from .files import FileBackend
        return FileBackend()
    if kind == "mock":
        from .mock import MockBackend  # pylint: disable=import-outside-toplevel
        return MockBackend()
    raise ValueError(f"Unknown backend: {kind}")
