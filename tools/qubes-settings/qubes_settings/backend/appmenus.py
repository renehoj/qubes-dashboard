"""Talking to ``qvm-appmenus`` (qubes-desktop-linux-common, in dom0).

Kept apart from the qubesadmin backend, and free of qubesadmin imports, so the
output parsing is tested on any box. ``--get-available --file-field Comment``
is the same call Qubes' Create Qube makes for its Select Applications popup.
"""

from __future__ import annotations

import os
import subprocess

from ..model import AppEntry
from .base import BackendError

APPMENUS_COMMAND = "qvm-appmenus"
APPMENUS_TIMEOUT = 60  # seconds; --update writes a .desktop file per entry
#: Arguments for the available-applications listing. Released qvm-appmenus
#: (Qubes 4.2, 4.3) refuses --get-available without the "unstable format"
#: pledge; newer ones accept it as a no-op and only warn on stderr.
GET_AVAILABLE_ARGS = ("--get-available", "--i-understand-format-is-unstable",
                      "--file-field", "Comment")
#: Where qvm-appmenus keeps each qube's templates and uncoloured icons.
APPMENUS_DIR = os.path.expanduser("~/.local/share/qubes-appmenus")


def parse_available(output: str, icon_dirs: list[str]) -> list[AppEntry]:
    """``qvm-appmenus --get-available --file-field Comment`` output.

    One ``ident|name|comment`` line per application. The icon is the first
    ``apps.tempicons/<ident without .desktop>.png`` found in ``icon_dirs``.
    """
    entries = []
    for line in output.splitlines():
        ident, sep, rest = line.partition("|")
        if not sep or not ident.endswith(".desktop"):
            continue
        name, _sep, comment = rest.partition("|")
        root = ident[: -len(".desktop")]
        icon = next((path for path in (os.path.join(d, f"{root}.png") for d in icon_dirs)
                     if os.path.isfile(path)), "")
        entries.append(AppEntry(ident, name or ident, comment, icon))
    return entries


def run_appmenus(*args: str, stdin: str | None = None) -> str:
    """Run qvm-appmenus; its stdout, or BackendError with its stderr."""
    try:
        result = subprocess.run(
            [APPMENUS_COMMAND, *args], input=stdin, capture_output=True,
            text=True, timeout=APPMENUS_TIMEOUT, check=False,
        )
    except FileNotFoundError as exc:
        raise BackendError(f"{APPMENUS_COMMAND} is not installed here (it is in dom0).") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"{APPMENUS_COMMAND} failed: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip().splitlines()
        raise BackendError(message[-1] if message else
                           f"{APPMENUS_COMMAND} exited with {result.returncode}")
    return result.stdout
