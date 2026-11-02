"""In-memory backend with demo logs, for development outside dom0.

The qubes are qubes-dashboard's demo set, so every qube it shows in demo mode
has logs here. They are made up the way a Qubes 4.2 dom0 writes them: the
Xen console of a booting Fedora/Debian qube, the GUI daemon's start-up and the
qrexec daemon's connection log. A few qubes have something worth highlighting.
"""

from __future__ import annotations

import time

from ..model import ADMIN_NAME, LogFile, LogText, clean
from .base import Backend, BackendError

#: The qubes with logs. A halted qube still has the logs of its last run;
#: one that never ran (debian-13-gnome-devel here) has none.
_QUBES = (
    "dom0", "ai-llm", "browser-personal", "browser-social", "browser-streaming",
    "browser-untrusted", "disp1234", "debian-13", "debian-13-gnome-browser",
    "debian-13-gnome-office", "debian-13-gnome-personal", "debian-13-gnome-sys",
    "debian-13-xfce", "default-dvm", "development", "games", "media", "office",
    "personal", "sys-firewall", "sys-net", "sys-usb", "sys-vpn", "sys-vpn-secure",
    "vault", "work",
)
#: Templates and qubes without a GUI: the GUI daemon never ran for them.
_NO_GUI = {"sys-firewall", "debian-13-gnome-sys"}
#: The time the demo qubes "started".
_BOOT = time.mktime((2026, 10, 24, 11, 38, 42, 0, 0, -1))

_CONSOLE = """\
Logfile Opened
[    0.000000] Linux version 6.12.48-1.qubes.fc41.x86_64 (mockbuild@build.qubes-os.org) (gcc (GCC) 14.3.1 20251022 (Red Hat 14.3.1-4), GNU ld version 2.43.1-14.fc41) #1 SMP PREEMPT_DYNAMIC Wed Sep  2 06:44:35 UTC 2026
[    0.000000] Command line: root=/dev/mapper/dmroot ro nomodeset console=hvc0 rd_NO_PLYMOUTH rd.plymouth.enable=0 plymouth.enable=0 clocksource=tsc xen_scrub_pages=0 swiotlb=2048
[    0.000000] x86/CPU: Model not found in latest microcode list
[    0.000000] BIOS-provided physical RAM map:
[    0.000000] BIOS-e820: [mem 0x0000000000000000-0x000000000009ffff] System RAM
[    0.000000] BIOS-e820: [mem 0x00000000000a0000-0x00000000000fffff] device reserved
[    0.000000] BIOS-e820: [mem 0x0000000000100000-0x0000000018ffffff] System RAM
[    0.000000] BIOS-e820: [gap 0x0000000019000000-0x00000000fbffffff]
[    0.000000] NX (Execute Disable) protection: active
[    0.000000] Hypervisor detected: Xen PVH
[    0.000000] Xen version 4.19.
[    0.012842] tsc: Detected 2995.200 MHz processor
[    0.103117] Memory: 372784K/409600K available (20480K kernel code, 4371K rwdata)
[    0.215530] smpboot: Allowing 2 CPUs, 0 hotplug CPUs
[    0.432005] xen:balloon: Initialising balloon driver
[    0.611201] blkfront: xvda: flush diskcache: enabled; persistent grants: enabled
[    0.623414] blkfront: xvdb: flush diskcache: enabled; persistent grants: enabled
[    0.902377] EXT4-fs (dm-0): mounted filesystem with ordered data mode. Quota mode: none.
[    1.115032] [\x1b[0;32m  OK  \x1b[0m] Reached target initrd-root-fs.target - Initrd Root File System.
[    1.402219] systemd[1]: systemd 256.11 running in system mode
[    1.508811] systemd[1]: Hostname set to <{name}>.
[    1.731409] [\x1b[0;32m  OK  \x1b[0m] Started systemd-journald.service - Journal Service.
[    1.902934] EXT4-fs (xvdb): mounted filesystem with ordered data mode. Quota mode: none.
[    2.023350] [\x1b[0;32m  OK  \x1b[0m] Mounted rw.mount - /rw.
[    2.211843] [\x1b[0;32m  OK  \x1b[0m] Started qubes-db.service - Qubes DB agent.
[    2.380192] [\x1b[0;32m  OK  \x1b[0m] Finished qubes-sysinit.service - Init Qubes Services settings.
[    2.611027] [\x1b[0;32m  OK  \x1b[0m] Started qubes-qrexec-agent.service - Qubes remote exec agent.
[    2.719980] [\x1b[0;32m  OK  \x1b[0m] Started qubes-gui-agent.service - Qubes GUI Agent.
{extra}[    3.004551] [\x1b[0;32m  OK  \x1b[0m] Reached target multi-user.target - Multi-User System.

Fedora Linux 41 (Forty One)
Kernel 6.12.48-1.qubes.fc41.x86_64 on x86_64 (hvc0)

{name} login: \r
"""

_CONSOLE_EXTRA = {
    "sys-net": (
        "[    2.801220] iwlwifi 0000:00:07.0: Direct firmware load for iwlwifi-so-a0-gf-a0-89.ucode failed with error -2\n"
        "[    2.801301] iwlwifi 0000:00:07.0: WARNING: No ucode found, trying older version\n"
        "[    2.912043] [\x1b[0;32m  OK  \x1b[0m] Started NetworkManager.service - Network Manager.\n"
    ),
    "sys-usb": (
        "[    2.844211] usb 2-1: device descriptor read/64, error -71\n"
        "[    2.911908] [\x1b[0;1;31mFAILED\x1b[0m] Failed to start qubes-input-sender-keyboard@event3.service.\n"
    ),
    "work": (
        "[    2.830114] cups-browsed[612]: Warning: Unable to connect to avahi-daemon\n"
    ),
    "vault": (
        "[    2.790002] EXT4-fs (xvdb): recovery complete; 0 errors found\n"
    ),
}

_DOM0_CONSOLE = """\
(XEN) Xen version 4.19.3 (mockbuild@[unknown]) (gcc (GCC) 14.3.1) debug=n Wed Sep  2 06:44:35 UTC 2026
(XEN) Bootloader: GRUB 2.12
(XEN) Command line: placeholder console=none dom0_mem=min:1024M dom0_mem=max:4096M ucode=scan smt=off gnttab_max_frames=2048 gnttab_max_maptrack_frames=4096
(XEN) Xen image load base address: 0x5b400000
(XEN) Video information:
(XEN)  VGA is graphics mode 3840x2160, 32 bpp
(XEN) Disc information:
(XEN)  Found 1 MBR signatures
(XEN) CPU Vendor: Intel, Family 6 (0x6), Model 186 (0xba), Stepping 3
(XEN) Speculative mitigation facilities:
(XEN)   Hardware features: IBPB IBRS STIBP SSBD L1D_FLUSH MD_CLEAR
(XEN) Using APIC driver x2apic_mixed
(XEN) Intel VT-d iommu 0 supported page sizes: 4kB, 2MB, 1GB
(XEN) Intel VT-d Snoop Control enabled.
(XEN) HVM: VMX enabled
(XEN) HVM: Hardware Assisted Paging (HAP) detected
(XEN) Brought up 8 CPUs
(XEN) Dom0 has maximum 8 VCPUs
(XEN) Initial low memory virq threshold set at 0x4000 pages.
(XEN) Scrubbing Free RAM in background
(XEN) Std. Loglevel: Errors and warnings
(XEN) Guest Loglevel: Nothing (Rate-limited: Errors and warnings)
(XEN) *** Serial input to DOM0 (type 'CTRL-a' three times to switch input)
(XEN) Freed 676kB init memory
(XEN) d3v0 VIRIDIAN CRASH: 1e c0000005 fffff8030c4b2a57 0 0
(XEN) WARNING: unsupported MSR 0x10f read by d7v1
"""

_GUID = """\
Icon size: 128x128
libvchan_is_eof
Waiting for daemon...
Connected to the GUI agent, protocol version 1.8
Using override_redirect protection: yes
Clipboard: secure copy sequence Ctrl-Shift-c, secure paste sequence Ctrl-Shift-v
{extra}"""

_GUID_EXTRA = {
    "ai-llm": "ErrorHandler: BadWindow (invalid Window parameter)\n"
              "                 Major opcode: 20 (X_GetProperty)\n",
    "browser-streaming": "Warning: XGetWindowAttributes for 0x2a00003 failed\n",
}

_QREXEC = """\
{date} 11:38:43.104 qrexec-daemon[{pid}]: Waiting for VM's qrexec agent.
{date} 11:38:45.517 qrexec-daemon[{pid}]: connected
{extra}"""

_QREXEC_EXTRA = {
    "work": "{date} 12:02:10.882 qrexec-policy-daemon[1302]: qrexec: qubes.Filecopy: "
            "work -> @default: denied: no matching policy\n",
    "browser-untrusted": "{date} 12:40:02.003 qrexec-daemon[{pid}]: "
                         "Domain browser-untrusted died; closing qrexec connection\n",
}


def _timestamped(text: str, stamp: float) -> str:
    """Prefix each line with xenconsoled's timestamp, as guest-*.log has."""
    prefix = time.strftime("[%Y-%m-%d %H:%M:%S] ", time.localtime(stamp))
    return "".join(prefix + line + "\n" for line in text.splitlines())


def _demo_text(name: str, path: str) -> str | None:
    date = time.strftime("%Y-%m-%d", time.localtime(_BOOT))
    pid = 4000 + sum(map(ord, name)) % 1000
    if name == ADMIN_NAME:
        return _DOM0_CONSOLE if path.endswith("hypervisor.log") else None
    if "/console/" in path:
        text = _CONSOLE.format(name=name, extra=_CONSOLE_EXTRA.get(name, ""))
        return _timestamped(text, _BOOT)
    if "/guid." in path:
        if name in _NO_GUI:
            return None
        return _GUID.format(extra=_GUID_EXTRA.get(name, ""))
    extra = _QREXEC_EXTRA.get(name, "").format(date=date, pid=pid)
    return _QREXEC.format(date=date, pid=pid, extra=extra)


class MockBackend(Backend):
    """Demo logs; ``delay`` seconds per read, like a slow disk."""

    name = "demo data"
    is_demo = True

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        #: What copy_to_global_clipboard was given, newest last (for tests).
        self.clipboard: list[str] = []

    def read_log(self, file: LogFile) -> LogText:
        if self.delay:
            time.sleep(self.delay)
        name = _owner(file.path)
        text = _demo_text(name, file.path) if name in _QUBES else None
        if text is None:
            return LogText(file, error="no such file (the qube has not run since "
                                       "the logs were rotated, or never)")
        raw = text.encode("utf-8")
        return LogText(file, clean(text), size=len(raw), mtime=_BOOT + 600)

    def copy_to_global_clipboard(self, text: str) -> None:
        if not text:
            raise BackendError("Nothing to copy")
        self.clipboard.append(text)


def _owner(path: str) -> str:
    """The qube a log path belongs to."""
    base = path.rsplit("/", 1)[-1]
    if base == "hypervisor.log":
        return ADMIN_NAME
    if base.startswith("guest-"):
        return base[len("guest-"):-len(".log")]
    # guid.<name>.log, qrexec.<name>.log
    return base.split(".", 1)[1][:-len(".log")]
