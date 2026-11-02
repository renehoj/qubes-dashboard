"""The real thing: dom0's log files and its global clipboard.

The log paths and the clipboard protocol are qubes-manager's (``log_dialog.py``
and ``clipboard.py``, the Qubes OS Project, GPL-2.0+): the tail of each file is
read directly, and a copy writes the clipboard file under the GUI daemon's
lock, with dom0 as its source.
"""

from __future__ import annotations

import fcntl
import os

from ..model import MAX_BYTES, LogFile, LogText, decode_tail
from .base import Backend, BackendError

APPVIEWER_LOCK = "/run/qubes/appviewer.lock"
CLIPBOARD_CONTENTS = "/run/qubes/qubes-clipboard.bin"
CLIPBOARD_SOURCE = CLIPBOARD_CONTENTS + ".source"


class FileBackend(Backend):
    """Reads the files under ``root`` (``/``; tests pass a directory)."""

    name = "files"

    def __init__(self, root: str = "/", max_bytes: int = MAX_BYTES):
        self.root = root
        self.max_bytes = max_bytes

    def _path(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    def read_log(self, file: LogFile) -> LogText:
        try:
            with open(self._path(file.path), "rb") as stream:
                stat = os.fstat(stream.fileno())
                truncated = stat.st_size > self.max_bytes
                if truncated:
                    stream.seek(stat.st_size - self.max_bytes)
                data = stream.read(self.max_bytes)
        except FileNotFoundError:
            return LogText(file, error="no such file (the qube has not run since "
                                       "the logs were rotated, or never)")
        except PermissionError:
            return LogText(file, error="permission denied")
        except OSError as exc:
            return LogText(file, error=exc.strerror or str(exc))
        return LogText(file, decode_tail(data, truncated), size=stat.st_size,
                       mtime=stat.st_mtime, truncated=truncated)

    def copy_to_global_clipboard(self, text: str) -> None:
        try:
            lock = os.open(self._path(APPVIEWER_LOCK), os.O_RDWR | os.O_CREAT, 0o666)
        except OSError as exc:
            raise BackendError(f"Cannot open the global clipboard: {exc}") from exc
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with open(self._path(CLIPBOARD_CONTENTS), "w", encoding="utf-8") as contents:
                contents.write(text)
            with open(self._path(CLIPBOARD_SOURCE), "w", encoding="utf-8") as source:
                source.write("dom0")
        except OSError as exc:
            raise BackendError(f"Cannot write the global clipboard: {exc}") from exc
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
