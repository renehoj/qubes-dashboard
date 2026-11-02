"""Backup, restore and revert for one qube, as plain data and command lines.

The window does three things with the qube it was opened for:

* **Backup** runs ``qvm-backup`` for that qube alone, to a directory in dom0
  or in another qube;
* **Restore** runs ``qvm-backup-restore`` for that qube alone, from a backup
  file in dom0 or in another qube. The qube is there already (it was picked
  from the list), so the backup copy comes back under a new name
  (``--rename-conflicting``: ``work`` becomes ``work1``) and the existing qube
  is not touched;
* **Revert** runs ``qvm-volume revert <qube>:private <revision>``, to one of
  the private volume's older revisions, picked from a list. Only a qube that is
  shut down can be reverted.

The passphrase goes to the tools on stdin (``--passphrase-file -``), never on a
command line or to disk. What is remembered between runs (where backups go)
is in :data:`SETTINGS_PATH`.

No GTK and no qubesadmin here: the window, the CLI and the tests share it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import time

ADMIN_NAME = "dom0"
#: The only volume Revert offers: the qube's own data (/home, /rw).
VOLUME = "private"

BACKUP_COMMAND = "qvm-backup"
RESTORE_COMMAND = "qvm-backup-restore"
VOLUME_COMMAND = "qvm-volume"

#: The pages, in tab order.
BACKUP = "backup"
RESTORE = "restore"
REVERT = "revert"
PAGES = (BACKUP, RESTORE, REVERT)
PAGE_TITLES = {BACKUP: "Backup", RESTORE: "Restore", REVERT: "Revert"}

# Power states as qubesd reports them; qubes-dashboard's names.
RUNNING = "Running"
HALTED = "Halted"
CRASHED = "Crashed"
NA = "NA"
#: States in which the private volume is not in use and can be reverted.
SHUT_DOWN_STATES = frozenset({HALTED, CRASHED, NA})


@dataclasses.dataclass(frozen=True)
class QubeInfo:
    """What the window needs to know about the qube."""

    name: str
    klass: str
    state: str

    @property
    def is_admin(self) -> bool:
        return self.klass == "AdminVM"

    @property
    def is_shut_down(self) -> bool:
        return self.state in SHUT_DOWN_STATES


def available_pages(qube: QubeInfo) -> set[str]:
    """Pages whose action can run now. dom0 gets none: its backup is the home
    directory, which this window does not handle. Revert needs the private
    volume unused, so a qube that is on cannot be reverted."""
    if qube.is_admin:
        return set()
    if qube.is_shut_down:
        return {BACKUP, RESTORE, REVERT}
    return {BACKUP, RESTORE}


def page_note(page: str, qube: QubeInfo) -> str:
    """The line under a page's fields: what will happen, or why it cannot."""
    if qube.is_admin:
        return "dom0 is not backed up, restored or reverted from here."
    if page == BACKUP:
        if not qube.is_shut_down:
            return (f"{qube.name} is {qube.state.lower()}: the backup has its data "
                    "as of its last shutdown.")
        return f"Only {qube.name} is backed up. The passphrase encrypts the backup."
    if page == RESTORE:
        return (f"Only {qube.name} is restored, as a new qube (such as "
                f"{qube.name}1). The existing {qube.name} is not touched.")
    if not qube.is_shut_down:
        return f"Shut {qube.name} down first: only a qube that is off can be reverted."
    return (f"{qube.name}'s {VOLUME} volume goes back to the revision picked. "
            "What was written since then is lost.")


# -- revisions -----------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Revision:
    """One older state of the private volume, as ``qvm-volume`` names it.

    The LVM pool names a revision after the time it was taken
    (``1730000000-back``); other pools may use a plain counter, and then there
    is no time to show.
    """

    id: str
    #: Seconds since the epoch, or None when the id carries no time.
    taken: float | None = None


#: An LVM revision id: the time it was taken, then ``-back``.
_EPOCH_ID = re.compile(r"^(\d{9,11})(?:\D.*)?$")
#: Revision ids qvm-volume accepts; anything else in its output is ignored.
_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]*$")


def parse_revisions(text: str) -> list[Revision]:
    """``qvm-volume info <qube>:private revisions`` output, newest first.

    qvm-volume lists them oldest first, one per line (the last is what a
    revert without a revision would pick).
    """
    revisions = []
    for line in text.splitlines():
        rev = line.strip()
        if not rev or not _REVISION_ID.match(rev):
            continue
        match = _EPOCH_ID.match(rev)
        revisions.append(Revision(rev, float(match.group(1)) if match else None))
    revisions.reverse()
    return revisions


def revision_label(revision: Revision, now: float | None = None) -> str:
    """The revision's line in the drop-down: when it was taken, and how long ago."""
    if revision.taken is None:
        return f"Revision {revision.id}"
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(revision.taken))
    return f"{stamp}  ({ago(revision.taken, now)})"


def ago(when: float, now: float | None = None) -> str:
    seconds = max(0, (time.time() if now is None else now) - when)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


# -- requests and their command lines -----------------------------------------
class InvalidRequest(ValueError):
    """A field is missing or wrong; the message is meant for the footer."""


@dataclasses.dataclass(frozen=True)
class BackupRequest:
    qube: str
    #: Where the backup goes: dom0 or a qube's name.
    dest_qube: str
    #: An absolute directory in ``dest_qube``; qvm-backup names the file.
    directory: str
    passphrase: str
    compress: bool = True


@dataclasses.dataclass(frozen=True)
class RestoreRequest:
    qube: str
    #: Where the backup is: dom0 or a qube's name.
    source_qube: str
    #: The backup file, an absolute path in ``source_qube``.
    path: str
    passphrase: str
    #: Only check the backup (``--verify-only``); nothing is created.
    verify_only: bool = False


def _check_path(path: str, what: str) -> None:
    if not path:
        raise InvalidRequest(f"Enter the {what}.")
    if not path.startswith("/"):
        raise InvalidRequest(f"The {what} must be an absolute path, starting with /.")
    if "\n" in path or "\0" in path:
        raise InvalidRequest(f"The {what} cannot contain a line break.")


def is_qube_name(name: str) -> bool:
    """Could ``name`` be a qube? (qubesd's rule: a letter, then at most 30
    letters, digits, ``_``, ``.`` or ``-``.)"""
    return re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,30}", name) is not None


def _check_qube(name: str, what: str) -> None:
    if not name:
        raise InvalidRequest(f"Pick the {what}.")
    if not is_qube_name(name):
        raise InvalidRequest(f"“{name}” is not a qube name.")


def _check_passphrase(passphrase: str) -> None:
    if not passphrase:
        raise InvalidRequest("Enter the passphrase.")
    if "\n" in passphrase or "\r" in passphrase:
        raise InvalidRequest("The passphrase cannot contain a line break.")


def check_backup(request: BackupRequest, repeated: str) -> None:
    """Raise InvalidRequest when the backup cannot be started as asked."""
    _check_qube(request.dest_qube, "qube the backup goes to")
    _check_path(request.directory, "directory")
    _check_passphrase(request.passphrase)
    if repeated != request.passphrase:
        raise InvalidRequest("The two passphrases are not the same.")


def check_restore(request: RestoreRequest) -> None:
    """Raise InvalidRequest when the restore cannot be started as asked."""
    _check_qube(request.source_qube, "qube the backup is in")
    _check_path(request.path, "backup file")
    _check_passphrase(request.passphrase)


def _dest_args(qube: str) -> list[str]:
    return [] if qube == ADMIN_NAME else ["--dest-vm", qube]


def backup_command(request: BackupRequest) -> list[str]:
    """``qvm-backup`` for the one qube. ``--yes`` skips its y/N question;
    the passphrase is read from stdin (see :func:`stdin_for`)."""
    argv = [BACKUP_COMMAND, "--yes", "--passphrase-file", "-"]
    if not request.compress:
        argv.append("--no-compress")
    argv += _dest_args(request.dest_qube)
    # No "--": the path starts with / and the qube name with a letter (see
    # the checks), and dom0's argparse mishandles "--" before nargs="?".
    return argv + [request.directory, request.qube]


def restore_command(request: RestoreRequest) -> list[str]:
    """``qvm-backup-restore`` for the one qube, under a new name if it exists.
    With ``--passphrase-file`` it does not ask y/N either."""
    argv = [RESTORE_COMMAND, "--passphrase-file", "-", "--rename-conflicting"]
    if request.verify_only:
        argv.append("--verify-only")
    argv += _dest_args(request.source_qube)
    return argv + [request.path, request.qube]


def revisions_command(qube: str) -> list[str]:
    return [VOLUME_COMMAND, "info", f"{qube}:{VOLUME}", "revisions"]


def revert_command(qube: str, revision: str) -> list[str]:
    return [VOLUME_COMMAND, "revert", f"{qube}:{VOLUME}", revision]


def stdin_for(request: BackupRequest | RestoreRequest) -> bytes:
    """What the tool reads with ``--passphrase-file -``: one line."""
    return (request.passphrase + "\n").encode("utf-8")


def command_text(argv: list[str]) -> str:
    """A command line for the log, as it would be typed."""
    return " ".join(shlex.quote(arg) for arg in argv)


# -- the tools' output ---------------------------------------------------------
#: qvm-backup's progress, rewritten in place with carriage returns.
_PROGRESS = re.compile(r"(?:Making a backup|Backup progress)\.*\s*(\d+(?:\.\d+)?)%")


@dataclasses.dataclass(frozen=True)
class Output:
    """One piece of a tool's output: a line for the log, or a progress figure."""

    line: str | None = None
    percent: float | None = None


class OutputParser:
    """Turns a tool's raw output into :class:`Output` as it arrives.

    Lines end with ``\\n``; qvm-backup also rewrites its progress line with
    ``\\r``. A progress figure is reported, not logged, so the log is not one
    line per percent.
    """

    def __init__(self):
        self._pending = ""

    def feed(self, text: str) -> list[Output]:
        self._pending += text
        parts = re.split(r"(\r\n|\r|\n)", self._pending)
        # The last part has no line end yet; keep it for the next feed.
        self._pending = parts.pop()
        return [out for part in parts[::2] for out in self._parse(part)]

    def close(self) -> list[Output]:
        rest, self._pending = self._pending, ""
        return self._parse(rest)

    @staticmethod
    def _parse(piece: str) -> list[Output]:
        piece = clean(piece)
        match = _PROGRESS.search(piece)
        if match:
            return [Output(percent=min(100.0, float(match.group(1))))]
        return [Output(line=piece)] if piece.strip() else []


_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text: str) -> str:
    """Drop terminal colours (qvm-backup-restore's red) and control characters."""
    return _CONTROLS.sub("", _ESCAPES.sub("", text)).rstrip()


# -- backup files --------------------------------------------------------------
#: What qvm-backup names its files: ``qubes-backup-2026-10-26T143012``.
_BACKUP_FILE = re.compile(r"^qubes-backup-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}$")


def backup_files(names) -> list[str]:
    """The names that are qvm-backup's files, newest first. Names listed by a
    qube are untrusted: anything else is dropped, not shown."""
    return sorted((n for n in names if _BACKUP_FILE.match(n)), reverse=True)


def list_backups_script(directory: str) -> str:
    """The shell command a source qube runs to list ``directory``."""
    return f"ls -1 -- {shlex.quote(directory)}"


def split_path(path: str) -> tuple[str, str]:
    """``/a/b/file`` -> (``/a/b``, ``file``)."""
    directory, name = os.path.split(path)
    return directory or "/", name


# -- remembered between runs ---------------------------------------------------
SETTINGS_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "qubes-backup", "settings.json")


@dataclasses.dataclass
class Settings:
    """Where backups went and came from last time. Never a passphrase."""

    backup_qube: str = ""
    backup_directory: str = ""
    restore_qube: str = ""
    restore_directory: str = ""
    compress: bool = True


def load_settings(path: str = SETTINGS_PATH) -> Settings:
    """The settings saved last, or the defaults when there are none or they
    cannot be read."""
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    defaults = Settings()
    values = {}
    for field in dataclasses.fields(Settings):
        value = data.get(field.name)
        if isinstance(value, type(getattr(defaults, field.name))):
            values[field.name] = value
    return Settings(**values)


def save_settings(settings: Settings, path: str = SETTINGS_PATH) -> None:
    """Write the settings; raises OSError when it cannot."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(dataclasses.asdict(settings), stream, indent=2)
        stream.write("\n")
    os.replace(temporary, path)
