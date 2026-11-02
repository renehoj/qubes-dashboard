"""A qube's log files, as plain data: which files, their tails, filtering.

The files are the three qubes-manager's log dialog offers for a qube -- the
Xen console (the kernel and systemd as the qube boots), the GUI daemon and
qrexec -- or, for dom0, Xen's own console. Only the last
:data:`MAX_BYTES` of each is read, so a console log that has grown for months
still opens at once.

No GTK and no qubesadmin here: the window, the CLI and the tests share it.
"""

from __future__ import annotations

import dataclasses
import re
import time

#: How much of the end of each file is read: qubes-manager's limit.
MAX_BYTES = 1024 * 1024

ADMIN_NAME = "dom0"


@dataclasses.dataclass(frozen=True)
class LogFile:
    """One tab: what it is called and which file it shows."""

    title: str
    path: str


def log_files(name: str) -> tuple[LogFile, ...]:
    """The logs for qube ``name``, in tab order."""
    if name == ADMIN_NAME:
        # dom0 has no console, GUI daemon or qrexec log of its own.
        return (LogFile("Hypervisor", "/var/log/xen/console/hypervisor.log"),)
    return (
        LogFile("Console", f"/var/log/xen/console/guest-{name}.log"),
        LogFile("GUI", f"/var/log/qubes/guid.{name}.log"),
        LogFile("qrexec", f"/var/log/qubes/qrexec.{name}.log"),
    )


@dataclasses.dataclass(frozen=True)
class LogText:
    """The end of one log file, as read.

    ``error`` is set, and ``text`` empty, when the file could not be read
    (it does not exist yet, or permission is denied).
    """

    file: LogFile
    text: str = ""
    #: Size of the whole file, in bytes.
    size: int = 0
    #: Modification time (seconds since the epoch), or None.
    mtime: float | None = None
    #: True when only the last MAX_BYTES were read.
    truncated: bool = False
    error: str | None = None

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()


# -- reading ---------------------------------------------------------------
# Console logs are what the qube wrote to its serial console: systemd's
# colours, cursor movement and carriage returns come along.
_ESCAPES = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
#: xenconsoled stamps every console line, empty ones too.
_TIMESTAMP = re.compile(r"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\]")


def _is_blank(line: str) -> bool:
    return not _TIMESTAMP.sub("", line, count=1).strip()


def clean(text: str) -> str:
    """Drop terminal escape sequences, control characters (not tabs) and
    lines with nothing on them but xenconsoled's timestamp."""
    text = _ESCAPES.sub("", text)
    text = text.replace("\r\n", "\n")
    # A bare carriage return redraws the line: keep what was drawn last.
    lines = (_CONTROLS.sub("", line.rsplit("\r", 1)[-1]) for line in text.split("\n"))
    return "".join(line + "\n" for line in lines if not _is_blank(line))


def decode_tail(data: bytes, truncated: bool) -> str:
    """Text of the last bytes of a file. When the read started mid-file, the
    first, partial line is dropped."""
    if truncated:
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline >= 0 else b""
    return clean(data.decode("utf-8", errors="replace"))


# -- filtering and highlighting -------------------------------------------------
def filter_lines(lines: list[str], query: str) -> list[str]:
    """The lines that contain every word of ``query``, ignoring case."""
    words = query.casefold().split()
    if not words:
        return list(lines)
    return [line for line in lines if all(word in line.casefold() for word in words)]


ERROR = "error"
WARNING = "warning"

_ERROR_WORDS = re.compile(
    r"\b(?:errors?|err|failed|failures?|fails?|fatal|panic|critical|crit|denied|"
    r"refused|segfault|oops|traceback|exception|timed out|timeout|killed)\b",
    re.IGNORECASE,
)
_WARNING_WORDS = re.compile(r"\b(?:warn|warning|warnings|deprecated)\b", re.IGNORECASE)
#: Things that look like errors and are not: "0 errors", "errors=remount-ro".
_NOT_ERROR = re.compile(r"\b(?:no|0) (?:errors?|failures?)\b|\berrors=|\bignore_errors\b",
                        re.IGNORECASE)


def severity(line: str) -> str | None:
    """ERROR, WARNING or None: how the line is highlighted."""
    if _ERROR_WORDS.search(_NOT_ERROR.sub("", line)):
        return ERROR
    if _WARNING_WORDS.search(line):
        return WARNING
    return None


# -- describing a log ----------------------------------------------------------
def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")


def summary(log: LogText) -> str:
    """The subtitle under the window's title: path, size, last change."""
    if log.error:
        return f"{log.file.path} · {log.error}"
    parts = [log.file.path, format_bytes(log.size)]
    if log.mtime is not None:
        parts.append("changed " + time.strftime("%Y-%m-%d %H:%M", time.localtime(log.mtime)))
    if log.truncated:
        parts.append(f"last {format_bytes(MAX_BYTES)} shown")
    return " · ".join(parts)


def as_text(lines: list[str]) -> str:
    """What Copy puts on the clipboard."""
    return "".join(line + "\n" for line in lines)
