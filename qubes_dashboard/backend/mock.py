"""In-memory backend with demo data, for development outside dom0.

The qubes are qubes-manager's demo set (``backend/mock.py``), reduced to the
fields this window shows. Start and stop take simulated time, so the
Starting…/Stopping… states can be seen and tested.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time

from ..model import HALTED, HALTING, PAUSED, RUNNING, TRANSIENT, App, Qube
from .base import (
    BACKUP_COMMAND, CONSOLE_COMMAND, DEVICES_COMMAND, FIREWALL_COMMAND, INFORMATION_COMMAND,
    LOGS_COMMAND,
    Backend, BackendError, settings_command, start_app_argv,
)

logger = logging.getLogger("qubes-dashboard")


@dataclasses.dataclass
class _Qube:
    name: str
    klass: str
    label: str
    state: str = HALTED
    template: str | None = None
    netvm: str | None = None


def _demo_qubes() -> list[_Qube]:
    q = _Qube
    tpl = "TemplateVM"
    fw = "sys-firewall"
    vpn = "sys-vpn"
    return [
        q("dom0", "AdminVM", "black", RUNNING),
        q("ai-llm", "AppVM", "red", RUNNING, "debian-13-gnome-personal", fw),
        q("browser-personal", "AppVM", "yellow", RUNNING, "debian-13-gnome-browser", fw),
        q("browser-social", "AppVM", "orange", RUNNING, "debian-13-gnome-browser", vpn),
        q("browser-streaming", "AppVM", "orange", PAUSED, "debian-13-gnome-browser", vpn),
        q("browser-untrusted", "AppVM", "red", HALTED, "debian-13-gnome-browser", vpn),
        q("disp1234", "DispVM", "red", RUNNING, "browser-untrusted", "sys-vpn-secure"),
        q("debian-13", tpl, "black"),
        q("debian-13-gnome-browser", tpl, "black"),
        q("debian-13-gnome-devel", tpl, "black"),
        q("debian-13-gnome-office", tpl, "black"),
        q("debian-13-gnome-personal", tpl, "black"),
        q("debian-13-gnome-sys", tpl, "black"),
        q("default-dvm", "AppVM", "red", HALTED, "debian-13-xfce", fw),
        q("development", "AppVM", "blue", HALTED, "debian-13-gnome-devel", fw),
        q("games", "StandaloneVM", "green", HALTED, None, fw),
        q("media", "AppVM", "blue", HALTED, "debian-13-gnome-office", fw),
        q("office", "AppVM", "blue", HALTED, "debian-13-gnome-office", fw),
        q("personal", "AppVM", "yellow", RUNNING, "debian-13-gnome-personal", fw),
        q("sys-firewall", "DispVM", "green", RUNNING, "default-dvm", "sys-net"),
        q("sys-net", "AppVM", "red", RUNNING, "debian-13-gnome-sys"),
        q("sys-usb", "DispVM", "red", RUNNING, "default-dvm"),
        q("sys-vpn", "AppVM", "yellow", RUNNING, "debian-13-gnome-sys", fw),
        q("sys-vpn-secure", "AppVM", "orange", RUNNING, "debian-13-gnome-sys", fw),
        q("vault", "AppVM", "black", HALTED, "debian-13-gnome-personal"),
        q("work", "AppVM", "blue", RUNNING, "debian-13-gnome-office", fw),
    ]


_TERMINAL = App("org.gnome.Terminal", "Terminal", "org.gnome.Terminal")
_FILES = App("org.gnome.Nautilus", "Files", "org.gnome.Nautilus")
_FIREFOX = App("firefox-esr", "Firefox ESR", "firefox-esr")

#: Demo menus, by a word in the qube's template (or its own name).
_DEMO_APPS = {
    "browser": [_FIREFOX, App("chromium", "Chromium", "chromium"), _TERMINAL],
    "office": [App("libreoffice-writer", "LibreOffice Writer", "libreoffice-writer"),
               App("libreoffice-calc", "LibreOffice Calc", "libreoffice-calc"),
               App("org.gnome.Evince", "Document Viewer", "org.gnome.Evince"),
               _FILES, _TERMINAL],
    "devel": [App("code", "Visual Studio Code", "com.visualstudio.code"),
              App("org.gnome.gitg", "gitg", "org.gnome.gitg"), _FILES, _TERMINAL],
    "personal": [_FIREFOX, App("thunderbird", "Thunderbird", "thunderbird"),
                 App("keepassxc", "KeePassXC", "keepassxc"), _FILES, _TERMINAL],
    "games": [App("steam", "Steam", "steam"), _TERMINAL],
}


def _apps(q: _Qube) -> list[App]:
    where = f"{q.name} {q.template or ''}"
    for word, apps in _DEMO_APPS.items():
        if word in where:
            return list(apps)
    return [] if q.klass == "AdminVM" else [_TERMINAL]


def _icon(q: _Qube) -> str:
    if q.klass == "AdminVM":
        return "adminvm-black"
    return {
        "TemplateVM": "templatevm",
        "StandaloneVM": "standalonevm",
        "DispVM": "dispvm",
    }.get(q.klass, "appvm") + f"-{q.label}"


class MockBackend(Backend):
    """Simulates a Qubes system. ``delay`` scales simulated start/stop time."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self._lock = threading.RLock()
        self._qubes = {q.name: q for q in _demo_qubes()}
        #: Names passed to open_settings, newest last (for tests).
        self.settings_opened: list[str] = []
        #: Names passed to open_firewall, newest last (for tests).
        self.firewall_opened: list[str] = []
        #: Names passed to open_devices, newest last (for tests).
        self.devices_opened: list[str] = []
        #: Names passed to open_information, newest last (for tests).
        self.information_opened: list[str] = []
        #: Names passed to open_logs, newest last (for tests).
        self.logs_opened: list[str] = []
        #: Names passed to open_backup, newest last (for tests).
        self.backup_opened: list[str] = []
        #: Names passed to open_console, newest last (for tests).
        self.console_opened: list[str] = []
        #: (name, ident) passed to start_app, newest last (for tests).
        self.apps_started: list[tuple[str, str]] = []

    def _get(self, name: str) -> _Qube:
        try:
            return self._qubes[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def _sleep(self, seconds: float) -> None:
        if self.delay:
            time.sleep(seconds * self.delay)

    def _set_state(self, name: str, state: str) -> None:
        with self._lock:
            if name in self._qubes:
                self._qubes[name].state = state

    def _connected(self, name: str) -> list[str]:
        """Running qubes that use ``name`` as netvm."""
        return sorted(
            q.name for q in self._qubes.values() if q.state != HALTED and q.netvm == name
        )

    def list_qubes(self) -> list[Qube]:
        with self._lock:
            return [
                Qube(q.name, q.klass, q.label, q.state, q.template, _icon(q))
                for q in self._qubes.values()
            ]

    def power_states(self) -> dict[str, str]:
        with self._lock:
            return {q.name: q.state for q in self._qubes.values()}

    def start(self, name: str) -> None:
        with self._lock:
            q = self._get(name)
            if q.klass == "AdminVM":
                raise BackendError("dom0 cannot be started.")
            if q.state == PAUSED:
                q.state = RUNNING
                return
            if q.state != HALTED:
                raise BackendError(f"Qube '{name}' is already running.")
            netvm = q.netvm
            q.state = TRANSIENT
        # Like qubesd, bring up the network chain first.
        try:
            if netvm and self._get(netvm).state == HALTED:
                self.start(netvm)
        except Exception:
            self._set_state(name, HALTED)
            raise
        self._sleep(1.5)
        self._set_state(name, RUNNING)

    def shutdown(self, name: str) -> None:
        with self._lock:
            q = self._get(name)
            if q.klass == "AdminVM":
                raise BackendError("dom0 cannot be shut down from here.")
            if q.state == HALTED:
                raise BackendError(f"Qube '{name}' is not running.")
            connected = self._connected(name)
            if connected:
                raise BackendError(
                    f"There are other qubes connected to '{name}': "
                    + ", ".join(connected)
                )
            q.state = HALTING
        self._sleep(1.0)
        self._set_state(name, HALTED)

    def restart(self, name: str) -> None:
        self.shutdown(name)
        self.start(name)

    def open_settings(self, name: str) -> None:
        self._get(name)
        self.settings_opened.append(name)
        logger.info("demo data: would run %s %s", settings_command(), name)

    def open_firewall(self, name: str) -> None:
        self._get(name)
        self.firewall_opened.append(name)
        logger.info("demo data: would run %s %s", FIREWALL_COMMAND, name)

    def open_devices(self, name: str) -> None:
        self._get(name)
        self.devices_opened.append(name)
        logger.info("demo data: would run %s %s", DEVICES_COMMAND, name)

    def open_information(self, name: str) -> None:
        self._get(name)
        self.information_opened.append(name)
        logger.info("demo data: would run %s %s", INFORMATION_COMMAND, name)

    def open_logs(self, name: str) -> None:
        self._get(name)
        self.logs_opened.append(name)
        logger.info("demo data: would run %s %s", LOGS_COMMAND, name)

    def open_backup(self, name: str) -> None:
        self._get(name)
        self.backup_opened.append(name)
        logger.info("demo data: would run %s %s", BACKUP_COMMAND, name)

    def open_console(self, name: str) -> None:
        self._get(name)
        self.console_opened.append(name)
        logger.info("demo data: would run %s %s", CONSOLE_COMMAND, name)

    def list_apps(self, name: str) -> list[App]:
        with self._lock:
            return _apps(self._get(name))

    def start_app(self, name: str, ident: str) -> None:
        with self._lock:
            halted = self._get(name).state == HALTED
            self.apps_started.append((name, ident))
        logger.info("demo data: would run %s", " ".join(start_app_argv(name, ident)))
        if halted:
            # qvm-run -a starts the qube first; that must not block either.
            threading.Thread(target=self.start, args=(name,), daemon=True).start()
