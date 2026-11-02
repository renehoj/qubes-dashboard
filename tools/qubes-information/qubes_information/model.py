"""What the window shows about a qube, as plain Python.

The backend reads a :class:`QubeInfo` -- every property, feature, tag and
volume of one qube, its firewall rules, and its live state when it runs -- and
:func:`sections` turns it into the page: titled sections of key/value rows,
formatted for reading. The window and the CLI only draw those sections, so
everything worth testing is here, without ``gi`` or ``qubesadmin``.
"""

from __future__ import annotations

import dataclasses
import datetime
import time
from typing import Iterable

ADMIN_VM = "AdminVM"
RUNNING = "Running"

#: Features named ``service.<name>`` are qvm-service's services.
SERVICE_PREFIX = "service."

#: Properties that hold a Unix time; shown as a date, not a number.
TIMESTAMP_PROPS = frozenset({"backup_timestamp", "start_time"})
#: Properties in MiB.
MIB_PROPS = frozenset({"memory", "maxmem", "stubdom_mem"})
#: Properties in seconds.
SECOND_PROPS = frozenset({"qrexec_timeout", "shutdown_timeout"})

#: Shown in place of an empty value.
EMPTY = "(empty)"
#: Shown for a property that has neither a value nor a default.
UNSET = "(not set)"

#: What happens to traffic no rule matched (qubes-firewall's IMPLICIT_TEXT).
IMPLICIT_FIREWALL = "Everything else is blocked."

# Section titles, in page order.
OVERVIEW = "Overview"
LIVE = "Live"
PROPERTIES = "Properties"
FEATURES = "Features"
SERVICES = "Services"
TAGS = "Tags"
STORAGE = "Storage"
FIREWALL = "Firewall"
SECTION_ORDER = (OVERVIEW, LIVE, PROPERTIES, FEATURES, SERVICES, TAGS, STORAGE, FIREWALL)


@dataclasses.dataclass(frozen=True)
class Prop:
    """One ``qvm-prefs`` line: the value as text, and whether it is the default."""

    name: str
    value: str | None
    is_default: bool = False


@dataclasses.dataclass(frozen=True)
class Volume:
    """One ``qvm-volume info`` (sizes in bytes; None when unknown)."""

    name: str
    pool: str
    vid: str = ""
    size: int | None = None
    usage: int | None = None
    rw: bool = True
    snap_on_start: bool = False
    save_on_stop: bool = False
    source: str | None = None
    ephemeral: bool | None = None
    revisions_to_keep: int | None = None
    #: Revision names, oldest first (``qvm-volume revisions``).
    revisions: tuple[str, ...] = ()
    #: True when the template's volume changed since this one was started.
    outdated: bool = False


@dataclasses.dataclass(frozen=True)
class LiveStats:
    """``admin.vm.CurrentState`` of a running qube."""

    #: Memory assigned right now, in KiB.
    memory_kb: int | None = None
    #: The most it can get without a restart, in KiB.
    max_memory_kb: int | None = None
    #: CPU time used since start, in seconds.
    cpu_seconds: float | None = None
    #: Unix time the qube started.
    start_time: float | None = None


@dataclasses.dataclass(frozen=True)
class QubeInfo:
    """Everything the backend could read about one qube."""

    name: str
    klass: str
    state: str
    props: tuple[Prop, ...] = ()
    #: ``qvm-features``, services included, sorted by name.
    features: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()
    volumes: tuple[Volume, ...] = ()
    #: Rules in Admin API text (``qvm-firewall --raw``); None when the qube
    #: has no firewall (dom0) or it could not be read.
    firewall: tuple[str, ...] | None = None
    stats: LiveStats | None = None
    #: Section title -> why that part could not be read.
    errors: tuple[tuple[str, str], ...] = ()

    @property
    def is_admin(self) -> bool:
        return self.klass == ADMIN_VM

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING

    def prop(self, name: str) -> str | None:
        for prop in self.props:
            if prop.name == name:
                return prop.value
        return None

    def feature(self, name: str) -> str | None:
        for key, value in self.features:
            if key == name:
                return value
        return None

    def error(self, section: str) -> str | None:
        for title, text in self.errors:
            if title == section:
                return text
        return None


@dataclasses.dataclass(frozen=True)
class Row:
    """One line of a section. ``dim`` rows are defaults or placeholders."""

    key: str
    value: str
    detail: str = ""
    dim: bool = False
    error: bool = False

    def text(self) -> str:
        return " ".join((self.key, self.value, self.detail))


@dataclasses.dataclass(frozen=True)
class Section:
    title: str
    rows: tuple[Row, ...]
    #: A short count or status next to the title.
    summary: str = ""


# -- formatting ----------------------------------------------------------------
def format_bytes(value: int | None) -> str:
    """Binary units, like qvm-volume's ``--human-readable``."""
    if value is None:
        return "?"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"  # pragma: no cover


def format_time(timestamp: float | None) -> str:
    """Local date and time, to the minute; empty for 0 or None."""
    if not timestamp:
        return ""
    moment = datetime.datetime.fromtimestamp(timestamp)
    return moment.strftime("%Y-%m-%d %H:%M")


def format_duration(seconds: float | None) -> str:
    """``3d 4h``, ``2h 05m``, ``12m 30s``, ``45s``."""
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def is_true(value: str | None) -> bool:
    """How qubesd reads a feature or a bool property given as text."""
    return value not in (None, "", "0", "False", "false")


def format_prop(prop: Prop) -> str:
    """A property value as the page shows it: dates, units, empty marked."""
    if prop.value is None:
        return UNSET
    if prop.value == "":
        return EMPTY
    if prop.name in TIMESTAMP_PROPS:
        number = _number(prop.value)
        if number is not None:
            return format_time(number) or "never"
    if prop.name in MIB_PROPS and prop.value.isdigit():
        return f"{prop.value} MiB"
    if prop.name in SECOND_PROPS and prop.value.isdigit():
        return f"{prop.value} s"
    return prop.value


def services(info: QubeInfo) -> list[tuple[str, bool]]:
    """``qvm-service``: (service, enabled) from the ``service.*`` features."""
    return [(key[len(SERVICE_PREFIX):], is_true(value))
            for key, value in info.features if key.startswith(SERVICE_PREFIX)]


def plain_features(info: QubeInfo) -> list[tuple[str, str]]:
    """The features that are not services."""
    return [(key, value) for key, value in info.features
            if not key.startswith(SERVICE_PREFIX)]


def volume_text(volume: Volume) -> str:
    """``1.2 GiB of 2.0 GiB (60%)``, or just the size when usage is unknown."""
    if volume.usage is not None and volume.size:
        percent = round(100 * volume.usage / volume.size)
        return f"{format_bytes(volume.usage)} of {format_bytes(volume.size)} ({percent}%)"
    return format_bytes(volume.size)


def volume_detail(volume: Volume) -> str:
    """Pool, mode and lifecycle of a volume, in one line."""
    parts = [f"pool {volume.pool}", "rw" if volume.rw else "read-only"]
    if volume.snap_on_start:
        parts.append(f"snapshot of {volume.source}" if volume.source else "snap on start")
    if volume.save_on_stop:
        parts.append("saved on stop")
    elif volume.rw and not volume.snap_on_start:
        parts.append("discarded on stop")
    if volume.ephemeral:
        parts.append("ephemeral")
    if volume.revisions_to_keep is not None:
        parts.append(f"keeps {volume.revisions_to_keep} revision"
                     + ("" if volume.revisions_to_keep == 1 else "s"))
    if volume.revisions:
        parts.append(f"{len(volume.revisions)} saved, latest {volume.revisions[-1]}")
    if volume.outdated:
        parts.append("outdated: restart to get the template's changes")
    if volume.vid:
        parts.append(volume.vid)
    return " · ".join(parts)


def disk_totals(info: QubeInfo) -> tuple[int | None, int | None]:
    """Used and allocated bytes over every volume that reports them."""
    usage = [v.usage for v in info.volumes if v.usage is not None]
    size = [v.size for v in info.volumes if v.size is not None]
    return (sum(usage) if usage else None, sum(size) if size else None)


def summary(info: QubeInfo) -> str:
    """The window's subtitle: ``AppVM · Running · debian-13 · sys-firewall``."""
    parts = [info.klass, info.state]
    template = info.prop("template")
    if template:
        parts.append(f"template {template}")
    if not info.is_admin:
        netvm = info.prop("netvm")
        parts.append(f"net {netvm}" if netvm else "no network")
    return " · ".join(parts)


# -- the sections --------------------------------------------------------------
def _overview(info: QubeInfo) -> list[Row]:
    rows = [Row("Class", info.klass), Row("State", info.state)]

    def add(key: str, value: str | None, detail: str = "", dim: bool = False):
        if value:
            rows.append(Row(key, value, detail, dim))

    add("Label", info.prop("label"))
    add("Template", info.prop("template"))
    if not info.is_admin:
        netvm = info.prop("netvm")
        add("Net qube", netvm or "none", dim=not netvm)
        if is_true(info.prop("provides_network")):
            add("Provides network", "yes", "other qubes can use it as their net qube")
        addresses = [a for a in (info.prop("ip"), info.prop("ip6")) if a]
        add("IP address", "  ".join(addresses), info.prop("mac") or "")
        add("Default disposable", info.prop("default_dispvm"))

    distribution = info.feature("os-distribution")
    os_text = " ".join(p for p in (distribution or info.feature("os"),
                                   info.feature("os-version")) if p)
    eol = info.feature("os-eol")
    add("Operating system", os_text, f"end of life {eol}" if eol else "")

    if is_true(info.feature("updates-available")):
        add("Updates", "available", _last_check(info))
    elif info.feature("last-updates-check"):
        add("Updates", "none known", _last_check(info), dim=True)

    if not info.is_admin:
        memory, maxmem = info.prop("memory"), info.prop("maxmem")
        if memory:
            balanced = maxmem not in (None, "", "0")
            add("Memory", f"{memory} MiB",
                f"up to {maxmem} MiB" if balanced else "fixed (no memory balancing)")
        add("VCPUs", info.prop("vcpus"))
        add("Virtualization", info.prop("virt_mode"), _kernel(info))

    used, size = disk_totals(info)
    if size is not None:
        text = (f"{format_bytes(used)} of {format_bytes(size)}" if used is not None
                else format_bytes(size))
        add("Disk", text, f"{len(info.volumes)} volumes")

    if not info.is_admin:
        last = format_time(_number(info.prop("backup_timestamp")))
        if is_true(info.prop("include_in_backups")):
            add("Backups", "included", f"last backup {last}" if last else "never backed up")
        else:
            add("Backups", "not included", f"last backup {last}" if last else "", dim=True)

    flags = []
    if is_true(info.prop("template_for_dispvms")):
        flags.append("disposable template")
    if is_true(info.prop("autostart")):
        flags.append("starts at boot")
    if is_true(info.feature("internal")):
        flags.append("internal (hidden from menus)")
    if is_true(info.prop("installed_by_rpm")):
        flags.append("installed by package")
    if is_true(info.prop("debug")):
        flags.append("debug mode")
    add("Flags", ", ".join(flags))
    add("UUID", info.prop("uuid"))
    return rows


def _last_check(info: QubeInfo) -> str:
    when = info.feature("last-updates-check")
    return f"last checked {when}" if when else ""


def _kernel(info: QubeInfo) -> str:
    kernel = info.prop("kernel")
    if info.klass == "StandaloneVM" or kernel == "":
        return "kernel from the qube"
    return f"kernel {kernel}" if kernel else ""


def _live(info: QubeInfo, now: float) -> list[Row]:
    stats = info.stats
    rows = []
    start = stats.start_time or _number(info.prop("start_time"))
    if start:
        rows.append(Row("Uptime", format_duration(now - start), f"started {format_time(start)}"))
    if stats.memory_kb is not None:
        detail = (f"of at most {format_bytes(stats.max_memory_kb * 1024)}"
                  if stats.max_memory_kb else "")
        rows.append(Row("Memory now", format_bytes(stats.memory_kb * 1024), detail))
    if stats.cpu_seconds is not None:
        rows.append(Row("CPU time", format_duration(stats.cpu_seconds)))
    xid = info.prop("xid")
    if xid not in (None, "", "-1"):
        rows.append(Row("Xen domain ID", xid))
    return rows


def _properties(info: QubeInfo) -> list[Row]:
    return [Row(p.name, format_prop(p), "default" if p.is_default else "",
                dim=p.is_default or p.value in (None, ""))
            for p in info.props]


def _firewall(info: QubeInfo) -> tuple[list[Row], str]:
    rows = [Row(str(index), rule) for index, rule in enumerate(info.firewall or ())]
    if info.firewall is not None:
        rows.append(Row("", IMPLICIT_FIREWALL, dim=True))
        if not info.prop("netvm") and not info.is_admin:
            rows.append(Row("", "The qube has no net qube, so the rules have no effect.",
                            dim=True))
    count = len(info.firewall or ())
    return rows, f"{count} rule" + ("" if count == 1 else "s")


def sections(info: QubeInfo, now: float | None = None) -> list[Section]:
    """The whole page, in order. Sections that cannot apply are left out:
    Live for a qube that is not running, Storage and Firewall for dom0."""
    now = time.time() if now is None else now
    out: list[Section] = [Section(OVERVIEW, tuple(_overview(info)), summary(info))]

    if info.stats is not None and info.is_running:
        out.append(Section(LIVE, tuple(_live(info, now))))

    out.append(Section(PROPERTIES, tuple(_properties(info)), f"{len(info.props)}"))

    features = plain_features(info)
    out.append(Section(FEATURES, tuple(
        Row(key, value if value != "" else EMPTY, dim=value == "") for key, value in features),
        f"{len(features)}"))

    service_list = services(info)
    out.append(Section(SERVICES, tuple(
        Row(name, "enabled" if on else "disabled", dim=not on)
        for name, on in service_list), f"{len(service_list)}"))

    out.append(Section(TAGS, tuple(Row(tag, "") for tag in info.tags), f"{len(info.tags)}"))

    if not info.is_admin:
        used, size = disk_totals(info)
        out.append(Section(STORAGE, tuple(
            Row(v.name, volume_text(v), volume_detail(v)) for v in info.volumes),
            f"{format_bytes(used)} used" if used is not None else ""))
        rows, count = _firewall(info)
        out.append(Section(FIREWALL, tuple(rows), count if info.firewall is not None else ""))

    return [_with_error(section, info.error(section.title)) for section in out]


def _with_error(section: Section, error: str | None) -> Section:
    if error:
        return dataclasses.replace(
            section, rows=(Row("", f"Could not read: {error}", error=True),) + section.rows)
    return section


#: Shown in a section that has no rows.
EMPTY_TEXT = {
    FEATURES: "No features",
    SERVICES: "No services set; each service has its default",
    TAGS: "No tags",
    STORAGE: "No volumes",
    PROPERTIES: "No properties",
}


# -- search --------------------------------------------------------------------
def tokens_of(query: str) -> list[str]:
    return query.casefold().split()


def filter_sections(all_sections: Iterable[Section], query: str) -> list[Section]:
    """The sections and rows that match ``query``.

    Every word must occur in the row's key, value or detail, or in the
    section's title -- so ``features`` shows the whole Features section and
    ``feat gui`` the GUI features in it. Sections left with no rows are
    dropped. An empty query keeps everything.
    """
    tokens = tokens_of(query)
    if not tokens:
        return list(all_sections)
    result = []
    for section in all_sections:
        title = section.title.casefold()
        rows = tuple(row for row in section.rows
                     if all(t in title or t in row.text().casefold() for t in tokens))
        if rows:
            result.append(dataclasses.replace(section, rows=rows))
    return result


def as_text(all_sections: Iterable[Section]) -> str:
    """The page as plain text: what the CLI prints and Copy puts on the clipboard."""
    lines: list[str] = []
    for section in all_sections:
        if lines:
            lines.append("")
        lines.append(f"{section.title}" + (f"  ({section.summary})" if section.summary else ""))
        rows = section.rows or (Row("", EMPTY_TEXT.get(section.title, "")),)
        width = max((len(row.key) for row in rows), default=0)
        for row in rows:
            value = row.value + (f"  [{row.detail}]" if row.detail else "")
            lines.append(f"  {row.key.ljust(width)}  {value}".rstrip())
    return "\n".join(lines) + "\n" if lines else ""
