"""Backend talking to qubesd through qubesadmin (dom0 or a GUI qube).

Adapted from qubes-manager's ``backend/qubesadmin_backend.py``, cut down to the
columns and actions this window has.
"""

from __future__ import annotations

import subprocess
import time

import qubesadmin
import qubesadmin.exc

from ..model import HALTED, NA, App, Qube
from .appmenus import desktop_entries
from .base import (
    BACKUP_COMMAND, CONSOLE_COMMAND, DEVICES_COMMAND, FIREWALL_COMMAND, INFORMATION_COMMAND,
    LOGS_COMMAND,
    Backend, BackendError, settings_command, start_app_argv,
)

RESTART_TIMEOUT = 120  # seconds to wait for a qube to halt


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # pylint: disable=broad-except
        return default


def _get(vm, prop: str, default=None):
    return _safe(lambda: getattr(vm, prop), default)


def _name(value) -> str | None:
    """qubesadmin returns VM/label objects for reference properties."""
    return str(value) if value else None


class _Errors:
    """Context manager translating qubesadmin errors into BackendError."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None or issubclass(exc_type, BackendError):
            return False
        if issubclass(exc_type, KeyError):
            raise BackendError(f"No such qube: {exc}") from exc
        if issubclass(exc_type, qubesadmin.exc.QubesException):
            raise BackendError(str(exc) or exc_type.__name__) from exc
        return False


class QubesAdminBackend(Backend):

    name = "qubesadmin"

    @staticmethod
    def _app(cached: bool = False):
        # A fresh Qubes object per call keeps worker threads independent and
        # avoids stale caches. With caching on, qubesadmin fetches all of a
        # qube's properties in one call (admin.vm.property.GetAll).
        app = qubesadmin.Qubes()
        if cached:
            app.cache_enabled = True
        return app

    def list_qubes(self) -> list[Qube]:
        app = self._app(cached=True)
        result = []
        with _Errors():
            for vm in app.domains:
                klass = vm.klass
                template = (
                    _name(_get(vm, "template"))
                    if klass in ("AppVM", "DispVM")
                    else None
                )
                result.append(
                    Qube(
                        name=vm.name,
                        klass=klass,
                        label=_name(_get(vm, "label")) or "black",
                        state=_safe(vm.get_power_state, NA),
                        template=template,
                        icon=_get(vm, "icon", "") or "",
                    )
                )
        return result

    def power_states(self) -> dict[str, str]:
        app = self._app()
        try:
            # One call for all qubes: "name class=X state=Y" per line.
            out = app.qubesd_call("dom0", "admin.vm.List").decode()
            states = {}
            for line in out.splitlines():
                name, *fields = line.split(" ")
                props = dict(f.split("=", 1) for f in fields if "=" in f)
                states[name] = props.get("state", NA)
            return states
        except Exception:  # pylint: disable=broad-except
            with _Errors():
                return {vm.name: _safe(vm.get_power_state, NA) for vm in app.domains}

    def start(self, name: str) -> None:
        with _Errors():
            vm = self._app().domains[name]
            if vm.is_paused():
                vm.unpause()
            else:
                vm.start()

    def shutdown(self, name: str) -> None:
        with _Errors():
            self._app().domains[name].shutdown()

    def restart(self, name: str) -> None:
        with _Errors():
            vm = self._app().domains[name]
            vm.shutdown()
            deadline = time.monotonic() + RESTART_TIMEOUT
            while vm.get_power_state() != HALTED:
                if time.monotonic() > deadline:
                    raise BackendError(
                        f"Qube '{name}' did not shut down within "
                        f"{RESTART_TIMEOUT} seconds."
                    )
                time.sleep(0.5)
            vm.start()

    def open_settings(self, name: str) -> None:
        _run_detached(settings_command(), name)

    def open_firewall(self, name: str) -> None:
        _run_detached(FIREWALL_COMMAND, name)

    def open_devices(self, name: str) -> None:
        _run_detached(DEVICES_COMMAND, name)

    def open_information(self, name: str) -> None:
        _run_detached(INFORMATION_COMMAND, name)

    def open_logs(self, name: str) -> None:
        _run_detached(LOGS_COMMAND, name)

    def open_backup(self, name: str) -> None:
        _run_detached(BACKUP_COMMAND, name)

    def open_console(self, name: str) -> None:
        _run_detached(CONSOLE_COMMAND, name)

    def list_apps(self, name: str) -> list[App]:
        with _Errors():
            items = self._app().domains[name].features.get("menu-items", "") or ""
        entries = desktop_entries(name)
        apps = []
        for item in items.split():
            ident = item.removesuffix(".desktop")
            entry_name, icon = entries.get(ident, (ident, ""))
            apps.append(App(ident, entry_name, icon))
        return apps

    def start_app(self, name: str, ident: str) -> None:
        argv = start_app_argv(name, ident)
        try:
            # pylint: disable=consider-using-with
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise BackendError(f"Could not run {argv[0]}: {exc}") from exc


def _run_detached(command: str, name: str) -> None:
    """Start ``command name`` in its own session, without waiting for it."""
    try:
        # pylint: disable=consider-using-with
        subprocess.Popen(
            [command, name],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise BackendError(f"Could not run {command}: {exc}") from exc
