"""In-memory backend with demo data, for development outside dom0.

The qubes are qubes-dashboard's demo set (itself qubes-manager's, reduced), so
every qube the list shows in demo mode has settings here. Property handling and
defaults are qubes-manager's ``backend/mock.py``.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Any

from ..model import (
    DEFAULT, HALTED, MENU_ITEMS, PAUSED, RUNNING, AppEntry, PropValue, QubeSettings,
)
from .base import Backend, BackendError

#: Property defaults, as qubesd reports them for an ordinary qube.
_DEFAULTS = {
    "memory": 400,
    "maxmem": 4000,
    "vcpus": 2,
    "virt_mode": "pvh",
}
_GLOBAL_NETVM = "sys-firewall"
_GLOBAL_DISPVM = "default-dvm"


def _apps(*pairs: str) -> list[AppEntry]:
    """``"ident:Name"`` strings as entries (the demo has no icons)."""
    out = []
    for pair in pairs:
        ident, name = pair.split(":", 1)
        out.append(AppEntry(f"{ident}.desktop", name, f"Demo application {name}"))
    return out


_BASE_APPS = _apps("xterm:XTerm", "org.gnome.Nautilus:Files",
                   "org.gnome.Terminal:Terminal", "org.gnome.Settings:Settings",
                   "org.gnome.TextEditor:Text Editor", "org.gnome.Calculator:Calculator",
                   "org.gnome.Calendar:Calendar", "org.gnome.Characters:Characters",
                   "org.gnome.clocks:Clocks", "yelp:Help")
#: What each template offers, as qvm-appmenus --get-available would list it.
_TEMPLATE_APPS = {
    "debian-13": _apps("xterm:XTerm", "debian-uxterm:UXTerm"),
    "debian-13-xfce": _apps("xterm:XTerm", "xfce4-terminal:Xfce Terminal",
                            "thunar:Thunar File Manager", "firefox-esr:Firefox ESR",
                            "mousepad:Mousepad"),
    "debian-13-gnome-browser": _BASE_APPS + _apps(
        "firefox-esr:Firefox ESR", "chromium:Chromium", "torbrowser:Tor Browser"),
    "debian-13-gnome-devel": _BASE_APPS + _apps(
        "code:Visual Studio Code", "org.gnome.Builder:Builder", "meld:Meld",
        "gitg:gitg", "firefox-esr:Firefox ESR"),
    "debian-13-gnome-office": _BASE_APPS + _apps(
        "libreoffice-writer:LibreOffice Writer", "libreoffice-calc:LibreOffice Calc",
        "libreoffice-impress:LibreOffice Impress", "org.gnome.Evince:Document Viewer",
        "thunderbird:Thunderbird", "firefox-esr:Firefox ESR", "vlc:VLC media player"),
    "debian-13-gnome-personal": _BASE_APPS + _apps(
        "firefox-esr:Firefox ESR", "thunderbird:Thunderbird",
        "org.keepassxc.KeePassXC:KeePassXC", "signal-desktop:Signal",
        "org.gnome.Evince:Document Viewer"),
    "debian-13-gnome-sys": _BASE_APPS + _apps(
        "nm-connection-editor:Network Connections", "wireguard:WireGuard"),
    "games": _apps("steam:Steam", "xterm:XTerm", "org.gnome.Nautilus:Files"),
}
_DEMO_MENU_ITEMS = {
    "office": "libreoffice-writer libreoffice-calc org.gnome.Nautilus org.gnome.Terminal",
    "work": "thunderbird firefox-esr org.gnome.Nautilus org.gnome.Terminal",
    "personal": "firefox-esr thunderbird org.gnome.Terminal",
    "vault": "org.keepassxc.KeePassXC org.gnome.Terminal old-password-tool",
    "development": "code org.gnome.Terminal",
    "games": "steam",
    "browser-untrusted": "firefox-esr",
    "disp1234": "firefox-esr",
}


@dataclasses.dataclass
class _Qube:
    name: str
    klass: str
    label: str
    state: str = HALTED
    template: str | None = None
    # Property values; a missing key means "default".
    props: dict = dataclasses.field(default_factory=dict)
    provides_network: bool = False
    include_in_backups: bool = True
    template_for_dispvms: bool = False
    menu_items: list[str] = dataclasses.field(default_factory=list)


def _demo_qubes() -> list[_Qube]:
    q = _Qube
    tpl = "TemplateVM"
    vpn = {"netvm": "sys-vpn"}
    return [
        q("dom0", "AdminVM", "black", RUNNING),
        q("ai-llm", "AppVM", "red", RUNNING, "debian-13-gnome-personal"),
        q("browser-personal", "AppVM", "yellow", RUNNING, "debian-13-gnome-browser"),
        q("browser-social", "AppVM", "orange", RUNNING, "debian-13-gnome-browser", vpn),
        q("browser-streaming", "AppVM", "orange", PAUSED, "debian-13-gnome-browser", vpn),
        q("browser-untrusted", "AppVM", "red", HALTED, "debian-13-gnome-browser", vpn,
          template_for_dispvms=True),
        q("disp1234", "DispVM", "red", RUNNING, "browser-untrusted",
          {"netvm": "sys-vpn-secure"}, include_in_backups=False),
        q("debian-13", tpl, "black", props={"netvm": None}),
        q("debian-13-gnome-browser", tpl, "black", props={"netvm": None}),
        q("debian-13-gnome-devel", tpl, "black", props={"netvm": None}),
        q("debian-13-gnome-office", tpl, "black", props={"netvm": None}),
        q("debian-13-gnome-personal", tpl, "black", props={"netvm": None}),
        q("debian-13-gnome-sys", tpl, "black", props={"netvm": None}),
        q("debian-13-xfce", tpl, "black", props={"netvm": None}),
        q("default-dvm", "AppVM", "red", HALTED, "debian-13-xfce",
          template_for_dispvms=True),
        q("development", "AppVM", "blue", HALTED, "debian-13-gnome-devel",
          {"memory": 2000, "maxmem": 8000, "vcpus": 4}),
        q("games", "StandaloneVM", "green", HALTED, None, {"virt_mode": "hvm"}),
        q("media", "AppVM", "blue", HALTED, "debian-13-gnome-office"),
        q("office", "AppVM", "blue", HALTED, "debian-13-gnome-office"),
        q("personal", "AppVM", "yellow", RUNNING, "debian-13-gnome-personal"),
        q("sys-firewall", "DispVM", "green", RUNNING, "default-dvm",
          {"netvm": "sys-net"}, provides_network=True),
        q("sys-net", "AppVM", "red", RUNNING, "debian-13-gnome-sys",
          {"netvm": None, "virt_mode": "hvm"}, provides_network=True),
        q("sys-usb", "DispVM", "red", RUNNING, "default-dvm",
          {"netvm": None, "virt_mode": "hvm"}),
        q("sys-vpn", "AppVM", "yellow", RUNNING, "debian-13-gnome-sys",
          provides_network=True),
        q("sys-vpn-secure", "AppVM", "orange", RUNNING, "debian-13-gnome-sys",
          provides_network=True),
        q("vault", "AppVM", "black", HALTED, "debian-13-gnome-personal",
          {"netvm": None}),
        q("work", "AppVM", "blue", RUNNING, "debian-13-gnome-office"),
    ]


class MockBackend(Backend):
    """Simulates a Qubes system. ``delay`` scales the simulated qrexec time."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self._lock = threading.RLock()
        self._qubes = {q.name: q for q in _demo_qubes()}
        for name, items in _DEMO_MENU_ITEMS.items():
            self._qubes[name].menu_items = [f"{i}.desktop" for i in items.split()]

    # -- helpers ---------------------------------------------------------
    def _get(self, name: str) -> _Qube:
        try:
            return self._qubes[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def _sleep(self, seconds: float) -> None:
        if self.delay:
            time.sleep(seconds * self.delay)

    def _default_of(self, q: _Qube, prop: str) -> Any:
        if prop == "netvm":
            if q.klass in ("AdminVM", "TemplateVM"):
                return None
            return _GLOBAL_NETVM
        if prop == "default_dispvm":
            if q.klass == "DispVM":
                return q.template
            return _GLOBAL_DISPVM
        return _DEFAULTS.get(prop)

    def _prop(self, q: _Qube, prop: str) -> PropValue:
        default = self._default_of(q, prop)
        if prop in q.props:
            return PropValue(q.props[prop], False, default)
        return PropValue(default, True, default)

    def _available_apps(self, q: _Qube) -> list[AppEntry]:
        """The template chain's applications, like qvm-appmenus finds them."""
        current, seen = q, 0
        while current is not None and seen < 3:
            if current.name in _TEMPLATE_APPS:
                return list(_TEMPLATE_APPS[current.name])
            current = self._qubes.get(current.template) if current.template else None
            seen += 1
        return []

    def _names(self, pred) -> list[str]:
        return sorted(q.name for q in self._qubes.values() if pred(q))

    def set_states(self, states: dict[str, str]) -> None:
        """Take power states from elsewhere (the list's own demo backend).

        In demo mode qubes-dashboard and this backend each simulate a system;
        this keeps "can only be changed while shut down" in step with what the
        list shows.
        """
        with self._lock:
            for name, state in states.items():
                if name in self._qubes:
                    self._qubes[name].state = state

    # -- Backend ---------------------------------------------------------
    def get_settings(self, name: str) -> QubeSettings:
        self._sleep(0.2)
        with self._lock:
            q = self._get(name)
            dispvm_templates = self._names(lambda x: x.template_for_dispvms)
            if q.klass == "DispVM":
                template_choices = dispvm_templates
            elif q.klass == "AppVM":
                template_choices = self._names(lambda x: x.klass == "TemplateVM")
            else:
                template_choices = []
            return QubeSettings(
                name=q.name,
                klass=q.klass,
                state=q.state,
                label=q.label,
                template=q.template if template_choices else None,
                netvm=self._prop(q, "netvm"),
                default_dispvm=self._prop(q, "default_dispvm"),
                memory=self._prop(q, "memory"),
                maxmem=self._prop(q, "maxmem"),
                vcpus=self._prop(q, "vcpus"),
                virt_mode=self._prop(q, "virt_mode"),
                include_in_backups=q.include_in_backups,
                template_for_dispvms=q.template_for_dispvms,
                template_choices=template_choices,
                netvm_choices=self._names(lambda x: x.provides_network and x.name != name),
                dispvm_choices=dispvm_templates,
                apps=[] if q.klass == "AdminVM" else self._available_apps(q),
                menu_items=list(q.menu_items),
            )

    def apply_settings(self, name: str, changes: dict[str, Any]) -> None:
        self._sleep(0.3)
        with self._lock:
            q = self._get(name)
            for prop, value in changes.items():
                if prop == "label":
                    if q.klass == "AdminVM":
                        raise BackendError("dom0's label cannot be changed.")
                    q.label = value
                elif prop == "template":
                    if q.state != HALTED:
                        raise BackendError(
                            "The template can only be changed while the "
                            "qube is shut down.")
                    q.template = value
                elif prop == MENU_ITEMS:
                    q.menu_items = list(value)
                elif prop in ("include_in_backups", "template_for_dispvms"):
                    setattr(q, prop, bool(value))
                elif prop == "netvm" and value == name:
                    raise BackendError("A qube cannot be its own net qube.")
                elif value is DEFAULT:
                    q.props.pop(prop, None)
                else:
                    q.props[prop] = value
