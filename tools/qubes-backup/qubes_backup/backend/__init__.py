"""Backends running qvm-backup, qvm-backup-restore and qvm-volume."""

from .base import Backend, BackendError, Cancelled, Job  # noqa: F401

KINDS = ("auto", "qubesadmin", "mock")


def qubesadmin_available() -> bool:
    try:
        import qubesadmin  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


def create_backend(kind: str = "auto") -> Backend:
    """Create a backend: ``qubesadmin``, ``mock`` or ``auto``.

    ``auto`` uses the real system when qubesadmin is importable (dom0) and
    demo data otherwise, so the window can be developed on any Linux box.
    """
    if kind == "auto":
        kind = "qubesadmin" if qubesadmin_available() else "mock"
    if kind == "qubesadmin":
        # pylint: disable=import-outside-toplevel
        from .qubesadmin_backend import QubesAdminBackend
        return QubesAdminBackend()
    if kind == "mock":
        from .mock import MockBackend  # pylint: disable=import-outside-toplevel
        return MockBackend()
    raise ValueError(f"Unknown backend: {kind}")
