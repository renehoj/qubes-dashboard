"""In-memory backend with demo data, for development outside dom0.

The qubes are qubes-dashboard's demo set, so every qube the list shows in
demo mode has a firewall here. Most allow everything (a fresh qube's single
``action=accept``); a few have the shapes worth trying the window on.
"""

from __future__ import annotations

import tempfile
import threading
import time

from ..model import ADMIN_VM, QubeFirewall
from ..rules import RuleList
from .base import Backend, BackendError

_DNS = ("10.139.1.1", "10.139.1.2")

#: name: (class, net qube)
_QUBES = {
    "dom0": (ADMIN_VM, None),
    "ai-llm": ("AppVM", "sys-firewall"),
    "browser-personal": ("AppVM", "sys-firewall"),
    "browser-social": ("AppVM", "sys-vpn"),
    "browser-streaming": ("AppVM", "sys-vpn"),
    "browser-untrusted": ("AppVM", "sys-vpn"),
    "disp1234": ("DispVM", "sys-vpn-secure"),
    "debian-13": ("TemplateVM", None),
    "debian-13-gnome-browser": ("TemplateVM", None),
    "debian-13-gnome-devel": ("TemplateVM", None),
    "debian-13-gnome-office": ("TemplateVM", None),
    "debian-13-gnome-personal": ("TemplateVM", None),
    "debian-13-gnome-sys": ("TemplateVM", None),
    "debian-13-xfce": ("TemplateVM", None),
    "default-dvm": ("AppVM", "sys-firewall"),
    "development": ("AppVM", "sys-firewall"),
    "games": ("StandaloneVM", "sys-firewall"),
    "media": ("AppVM", "sys-firewall"),
    "office": ("AppVM", "sys-firewall"),
    "personal": ("AppVM", "sys-firewall"),
    "sys-firewall": ("DispVM", "sys-net"),
    "sys-net": ("AppVM", None),
    "sys-usb": ("DispVM", None),
    "sys-vpn": ("AppVM", "sys-firewall"),
    "sys-vpn-secure": ("AppVM", "sys-firewall"),
    "vault": ("AppVM", None),
    "work": ("AppVM", "sys-firewall"),
}

_RULES = {
    "work": [
        "action=accept specialtarget=dns",
        "action=accept proto=icmp",
        "action=accept proto=tcp dsthost=mail.example.com dstports=993-993 comment=IMAP",
        "action=accept proto=tcp dsthost=mail.example.com dstports=587-587 comment=SMTP",
        "action=accept proto=tcp dst4=10.20.0.0/16 dstports=443-443 comment=Intranet",
        "action=drop",
    ],
    "office": [
        "action=drop proto=tcp dstports=25-25 comment=No direct mail",
        "action=accept",
    ],
    "vault": [],
    "browser-untrusted": [
        "action=drop dst4=192.168.0.0/16 comment=No LAN",
        "action=drop dst4=10.0.0.0/8 comment=No LAN",
        "action=accept",
    ],
}


class MockBackend(Backend):
    """Simulates a Qubes system. ``delay`` scales the simulated qrexec time."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self._lock = threading.RLock()
        self._rules = {name: RuleList(_RULES.get(name, ["action=accept"]))
                       for name, (klass, _netvm) in _QUBES.items() if klass != ADMIN_VM}
        # Demo snapshots stay out of the real state directory.
        self.snapshot_directory = tempfile.mkdtemp(prefix="qubes-firewall-demo-")

    def _sleep(self, seconds: float) -> None:
        if self.delay:
            time.sleep(seconds * self.delay)

    def _get(self, name: str) -> tuple[str, str | None]:
        try:
            return _QUBES[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def get_firewall(self, name: str) -> QubeFirewall:
        self._sleep(0.2)
        with self._lock:
            klass, netvm = self._get(name)
            if klass == ADMIN_VM:
                return QubeFirewall(name, klass, None, RuleList())
            return QubeFirewall(name, klass, netvm, RuleList(self._rules[name].rules),
                                _DNS if netvm else ())

    def _read_rules(self, name: str) -> RuleList:
        with self._lock:
            if self._get(name)[0] == ADMIN_VM:
                raise BackendError("dom0 has no firewall.")
            return RuleList(self._rules[name].rules)

    def _write_rules(self, name: str, rules: RuleList) -> None:
        self._sleep(0.3)
        with self._lock:
            self._rules[name] = RuleList(rules.rules)

    def change_elsewhere(self, name: str, rules: list[str]) -> None:
        """Simulate another tool writing the rules (tests)."""
        with self._lock:
            self._rules[name] = RuleList(rules)
