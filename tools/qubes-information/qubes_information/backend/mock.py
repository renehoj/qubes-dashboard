"""In-memory backend with demo data, for development outside dom0.

The qubes are qubes-dashboard's demo set, with the states that list starts
in, so every qube it shows in demo mode has information here. Properties,
features, volumes and firewall rules are made up the way qubesd would report
them for a qube of that class on Qubes 4.2.
"""

from __future__ import annotations

import time

from ..model import FIREWALL, LiveStats, Prop, QubeInfo, Volume
from .base import Backend, BackendError

GIB = 1024 ** 3
MIB = 1024 ** 2

#: name: (class, label, state, template, net qube)
_QUBES = {
    "dom0": ("AdminVM", "black", "Running", None, None),
    "ai-llm": ("AppVM", "red", "Running", "debian-13-gnome-personal", "sys-firewall"),
    "browser-personal": ("AppVM", "yellow", "Running", "debian-13-gnome-browser", "sys-firewall"),
    "browser-social": ("AppVM", "orange", "Running", "debian-13-gnome-browser", "sys-vpn"),
    "browser-streaming": ("AppVM", "orange", "Paused", "debian-13-gnome-browser", "sys-vpn"),
    "browser-untrusted": ("AppVM", "red", "Halted", "debian-13-gnome-browser", "sys-vpn"),
    "disp1234": ("DispVM", "red", "Running", "browser-untrusted", "sys-vpn-secure"),
    "debian-13": ("TemplateVM", "black", "Halted", None, None),
    "debian-13-gnome-browser": ("TemplateVM", "black", "Halted", None, None),
    "debian-13-gnome-devel": ("TemplateVM", "black", "Halted", None, None),
    "debian-13-gnome-office": ("TemplateVM", "black", "Halted", None, None),
    "debian-13-gnome-personal": ("TemplateVM", "black", "Halted", None, None),
    "debian-13-gnome-sys": ("TemplateVM", "black", "Halted", None, None),
    "debian-13-xfce": ("TemplateVM", "black", "Halted", None, None),
    "default-dvm": ("AppVM", "red", "Halted", "debian-13-xfce", "sys-firewall"),
    "development": ("AppVM", "blue", "Halted", "debian-13-gnome-devel", "sys-firewall"),
    "games": ("StandaloneVM", "green", "Halted", None, "sys-firewall"),
    "media": ("AppVM", "blue", "Halted", "debian-13-gnome-office", "sys-firewall"),
    "office": ("AppVM", "blue", "Halted", "debian-13-gnome-office", "sys-firewall"),
    "personal": ("AppVM", "yellow", "Running", "debian-13-gnome-personal", "sys-firewall"),
    "sys-firewall": ("DispVM", "green", "Running", "default-dvm", "sys-net"),
    "sys-net": ("AppVM", "red", "Running", "debian-13-gnome-sys", None),
    "sys-usb": ("DispVM", "red", "Running", "default-dvm", None),
    "sys-vpn": ("AppVM", "yellow", "Running", "debian-13-gnome-sys", "sys-firewall"),
    "sys-vpn-secure": ("AppVM", "orange", "Running", "debian-13-gnome-sys", "sys-firewall"),
    "vault": ("AppVM", "black", "Halted", "debian-13-gnome-personal", None),
    "work": ("AppVM", "blue", "Running", "debian-13-gnome-office", "sys-firewall"),
}

_PROVIDES_NETWORK = {"sys-net", "sys-firewall", "sys-vpn", "sys-vpn-secure"}
_DISP_TEMPLATES = {"default-dvm", "browser-untrusted"}

#: Extra features and tags, by qube.
_FEATURES = {
    "sys-net": {"service.network-manager": "1", "service.clocksync": "1"},
    "sys-firewall": {"service.qubes-firewall": "1"},
    "sys-usb": {"service.qubes-u2f-proxy": "1", "service.cups": ""},
    "sys-vpn": {"service.network-manager": "1", "vpn-type": "wireguard"},
    "work": {"service.cups": "1", "gui-default-secure-copy-sequence": "Ctrl-Shift-c"},
    "vault": {"service.crond": ""},
    "ai-llm": {"gui-allow-fullscreen": "1"},
    "debian-13-xfce": {"updates-available": "1"},
}
_TAGS = {
    "sys-net": ("audiovm-dom0", "created-by-dom0"),
    "sys-usb": ("created-by-dom0", "usbvm"),
    "work": ("created-by-dom0", "work-data"),
    "vault": ("created-by-dom0", "offline"),
}
_RULES = {
    "work": (
        "action=accept specialtarget=dns",
        "action=accept proto=icmp",
        "action=accept proto=tcp dsthost=mail.example.com dstports=993-993 comment=IMAP",
        "action=accept proto=tcp dst4=10.20.0.0/16 dstports=443-443 comment=Intranet",
        "action=drop",
    ),
    "office": ("action=drop proto=tcp dstports=25-25 comment=No direct mail",
               "action=accept"),
    "vault": (),
    "browser-untrusted": ("action=drop dst4=192.168.0.0/16 comment=No LAN",
                          "action=drop dst4=10.0.0.0/8 comment=No LAN",
                          "action=accept"),
}


def _qid(name: str) -> int:
    return sorted(_QUBES).index(name)


def _props(name: str, klass: str, label: str, state: str, template, netvm) -> list[Prop]:
    """``qvm-prefs`` of a qube of this class, as qubesd would answer."""
    running = state in ("Running", "Paused")
    qid = _qid(name)
    ip = f"10.137.0.{qid + 2}" if netvm else ""
    started = time.time() - 3600 * (qid % 7 + 1) - 60 * qid if running else 0
    no_kernel = klass in ("StandaloneVM", "AdminVM")

    def p(prop, value, default=False):
        return Prop(prop, "" if value is None else str(value), default)

    if klass == "AdminVM":
        return [p("default_dispvm", "default-dvm", True), p("icon", "adminvm-black"),
                p("keyboard_layout", "dk++", True), p("klass", klass), p("label", label),
                p("name", name), p("qid", 0), p("uuid", "00000000-0000-0000-0000-000000000000")]

    props = [
        p("audiovm", "dom0", True),
        p("autostart", name.startswith("sys-"), not name.startswith("sys-")),
        p("backup_timestamp", 1761300000 - qid * 86400 if not name.startswith("sys") else "",
          name.startswith("sys")),
        p("debug", False, True),
        p("default_dispvm", "default-dvm", True),
        p("default_user", "user", True),
        p("dns", "10.139.1.1 10.139.1.2" if netvm else "", True),
        p("gateway", f"10.138.0.{qid + 1}" if netvm else "", True),
        p("guivm", "dom0", True),
        p("icon", f"{klass.lower()}-{label}"),
        p("include_in_backups", klass != "DispVM" and not name.startswith("sys"),
          klass != "DispVM"),
        p("installed_by_rpm", klass == "TemplateVM", True),
        p("ip", ip, True),
        p("ip6", "", True),
        p("kernel", "" if no_kernel else "6.6.63-1.fc37", not no_kernel),
        p("kernelopts", "", True),
        p("keyboard_layout", "dk++", True),
        p("klass", klass),
        p("label", label),
        p("mac", f"00:16:3e:5e:6c:{qid:02x}", True),
        p("management_dispvm", "default-mgmt-dvm", True),
        p("maxmem", 0 if name in _PROVIDES_NETWORK or name == "sys-usb" else 4000,
          name not in _PROVIDES_NETWORK),
        p("memory", 400 if name.startswith("sys") else 1000 if klass != "TemplateVM" else 400,
          not name.startswith("sys")),
        p("name", name),
        p("netvm", netvm, netvm == "sys-firewall"),
        p("provides_network", name in _PROVIDES_NETWORK, name not in _PROVIDES_NETWORK),
        p("qid", qid + 1),
        p("qrexec_timeout", 60, True),
        p("shutdown_timeout", 60, True),
        p("start_time", f"{started:.2f}" if started else "", not started),
        p("stubdom_mem", "", True),
        p("stubdom_xid", -1, True),
        p("template_for_dispvms", name in _DISP_TEMPLATES, name not in _DISP_TEMPLATES),
        p("updateable", klass in ("TemplateVM", "StandaloneVM"), True),
        p("uuid", f"8f0c{qid:04x}-6a1e-4b7a-9d3c-1c2b3a4d5e{qid:02x}"),
        p("vcpus", 4 if name == "ai-llm" else 2, name != "ai-llm"),
        p("virt_mode", "hvm" if name in ("sys-net", "sys-usb") else "pvh",
          name not in ("sys-net", "sys-usb")),
        p("visible_ip", ip, True),
        p("xid", 10 + qid if running else -1, True),
    ]
    if template:
        props.append(p("template", template, False))
        props.sort(key=lambda prop: prop.name)
    return props


def _features(name: str, klass: str, template) -> dict[str, str]:
    features = {
        "qrexec": "1", "gui": "1", "gui-emulated": "", "supported-service.cups": "1",
        "os": "Linux", "os-distribution": "debian", "os-version": "13",
        "os-eol": "2030-06-30", "last-updates-check": "2026-10-24 21:14:03",
    }
    if klass == "AdminVM":
        features = {"gui-default-trayicon-mode": "border1", "config.default.qubes-update-check": "",
                    "os": "Linux", "os-distribution": "fedora", "os-version": "41",
                    "last-updates-check": "2026-10-25 08:02:40"}
    if klass == "TemplateVM":
        features["template-name"] = name
    if klass == "DispVM" or name in _DISP_TEMPLATES:
        features["appmenus-dispvm"] = "1"
    if name.startswith("sys-"):
        features["internal"] = "" if name == "sys-net" else "1"
    if template:
        features["menu-items"] = "org.gnome.Nautilus.desktop xterm.desktop"
    features.update(_FEATURES.get(name, {}))
    return features


def _volumes(name: str, klass: str, template) -> list[Volume]:
    qid = _qid(name)
    used = 0.2 + (qid % 9) / 10
    if klass in ("TemplateVM", "StandaloneVM"):
        root = Volume("root", "vm-pool", f"qubes_dom0/vm-{name}-root", 20 * GIB,
                      int((4 + qid % 5) * GIB), rw=True, save_on_stop=True,
                      revisions_to_keep=1, revisions=("1761302400-back",))
    else:
        source = f"qubes_dom0/vm-{template}-root" if klass == "AppVM" else \
            f"qubes_dom0/vm-{template}-private"
        root = Volume("root", "vm-pool", f"qubes_dom0/vm-{name}-root-snap", 20 * GIB,
                      int(5.1 * GIB), rw=True, snap_on_start=True, source=source,
                      outdated=name == "work")
    private = Volume("private", "vm-pool", f"qubes_dom0/vm-{name}-private",
                     2 * GIB if klass != "TemplateVM" else 2 * GIB, int(used * GIB),
                     rw=True, save_on_stop=klass != "DispVM",
                     snap_on_start=klass == "DispVM",
                     source=f"qubes_dom0/vm-{template}-private" if klass == "DispVM" else None,
                     revisions_to_keep=0 if klass == "DispVM" else 2,
                     revisions=() if klass == "DispVM" else ("1761216000-back",))
    volatile = Volume("volatile", "vm-pool", f"qubes_dom0/vm-{name}-volatile",
                      10 * GIB, 34 * MIB, rw=True, ephemeral=False)
    kernel = Volume("kernel", "linux-kernel", "6.6.63-1.fc37", 312 * MIB, None, rw=False)
    volumes = [kernel, private, root, volatile]
    if klass == "StandaloneVM":
        volumes.remove(kernel)
    return volumes


class MockBackend(Backend):
    """Simulates a Qubes system. ``delay`` scales the simulated qrexec time."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay
        #: Simulated memory drift, so the Live section visibly updates.
        self._ticks = 0

    def _sleep(self, seconds: float) -> None:
        if self.delay:
            time.sleep(seconds * self.delay)

    @staticmethod
    def _get(name: str):
        try:
            return _QUBES[name]
        except KeyError:
            raise BackendError(f"No such qube: {name}") from None

    def get_info(self, name: str) -> QubeInfo:
        self._sleep(0.3)
        klass, label, state, template, netvm = self._get(name)
        props = tuple(_props(name, klass, label, state, template, netvm))
        features = tuple(sorted(_features(name, klass, template).items()))
        tags = tuple(sorted(_TAGS.get(name, ("created-by-dom0",))))
        admin = klass == "AdminVM"
        errors = ()
        firewall = None
        if not admin:
            firewall = _RULES.get(name, ("action=accept",))
            if name == "disp1234":
                # Show what a refused call looks like.
                firewall = None
                errors = ((FIREWALL, "Got empty response from qubesd. "
                                     "See journalctl in dom0 for details."),)
        return QubeInfo(
            name=name, klass=klass, state=state, props=props, features=features,
            tags=tags, volumes=() if admin else tuple(_volumes(name, klass, template)),
            firewall=firewall, stats=self._stats(name, props), errors=errors)

    def _stats(self, name: str, props) -> LiveStats | None:
        klass, _label, state, _template, _netvm = _QUBES[name]
        if state != "Running":
            return None
        self._ticks += 1
        qid = _qid(name)
        memory = dict((p.name, p.value) for p in props).get("memory") or "4096"
        start = next((float(p.value) for p in props if p.name == "start_time" and p.value),
                     time.time() - 86400 * 2)
        maxmem = 4000 if klass != "AdminVM" else 16384
        drift = (self._ticks * 37 + qid * 101) % 400
        return LiveStats(
            memory_kb=(int(memory) + 600 + drift) * 1024,
            max_memory_kb=maxmem * 1024,
            cpu_seconds=(time.time() - start) * 0.02 * (qid % 4 + 1),
            start_time=start,
        )

    def get_stats(self, name: str) -> LiveStats | None:
        self._sleep(0.05)
        self._get(name)
        info_props = _props(name, *_QUBES[name])
        return self._stats(name, info_props)
