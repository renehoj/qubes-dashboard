"""The menu entries qvm-appmenus makes in dom0, read for names and icons.

Plain files, no qubesadmin: kept apart from the qubesadmin backend so it can
be tested anywhere.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

#: Where qvm-appmenus writes the menu entries it makes for each qube; their
#: names and icons are read from there.
DESKTOP_DIRS = (
    Path.home() / ".local/share/applications",
    Path.home() / ".local/share/qubes-appmenus",
)
_START_APP = re.compile(r"qubes\.StartApp\+(\S+)")


def desktop_entries(name: str, dirs=DESKTOP_DIRS) -> dict[str, tuple[str, str]]:
    """Name and icon of qube ``name``'s menu entries, by application ident.

    The entries qvm-appmenus makes say which qube they belong to
    (``X-Qubes-VmName``) and run ``qubes.StartApp+<ident>``; their Name is
    ``<qube>: <application>``. Unreadable files are skipped: a missing entry
    only costs the application its pretty name.
    """
    found = {}
    for directory in dirs:
        for path in sorted(directory.rglob("*.desktop")) if directory.is_dir() else ():
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, OSError, UnicodeDecodeError):
                continue
            match = _START_APP.search(entry.get("exec", ""))
            if entry.get("x-qubes-vmname") != name or match is None:
                continue
            title = entry.get("name", "").removeprefix(f"{name}: ") or match.group(1)
            found.setdefault(match.group(1), (title, entry.get("icon", "")))
    return found
