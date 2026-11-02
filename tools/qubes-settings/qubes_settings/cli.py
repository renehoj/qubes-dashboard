"""Headless frontend: print what the window would show, and optionally save.

Useful in dom0 to check the qubesadmin backend without a display, and anywhere
else to check the field rules against the demo data::

    python3 -m qubes_settings.cli work
    python3 -m qubes_settings.cli --mock office --set netvm=sys-vpn --set vcpus=4
    python3 -m qubes_settings.cli --mock office --set netvm=default
    python3 -m qubes_settings.cli --mock office --set menu_items=xterm.desktop,yelp.desktop

``--set`` goes through the same rules as the window: a field that is not shown,
or cannot be changed in the qube's current state, is refused.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from . import model
from .backend import KINDS, BackendError, create_backend

_COMBO_FIELDS = (model.NETVM, model.DEFAULT_DISPVM, model.VIRT_MODE)
_INT_FIELDS = (model.MEMORY, model.MAXMEM, model.VCPUS)
_BOOL_FIELDS = (model.INCLUDE_IN_BACKUPS, model.TEMPLATE_FOR_DISPVMS)


def _value_text(s: model.QubeSettings, field: str) -> str:
    if field == model.MENU_ITEMS:
        names = {a.ident: a.name for a in model.app_entries(s)}
        return ", ".join(names.get(i, i) for i in s.menu_items) or "(none)"
    value = getattr(s, field)
    if isinstance(value, model.PropValue):
        text = "(none)" if value.value is None else str(value.value)
        return f"default ({text})" if value.is_default else text
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def format_settings(s: model.QubeSettings) -> str:
    rows = []
    for field in model.shown_fields(s):
        reason = model.disabled_reason(s, field)
        rows.append((model.FIELD_TITLES[field], _value_text(s, field),
                     f"(read-only: {reason})" if reason else ""))
    width = max(len(title) for title, _v, _n in rows)
    lines = [s.name, model.summary(s), ""]
    lines += [f"{title.ljust(width)}  {value}  {note}".rstrip() for title, value, note in rows]
    return "\n".join(lines)


def parse_assignment(s: model.QubeSettings, text: str) -> tuple[str, Any]:
    """``prop=value`` as a form value, in the shape :func:`model.changes` reads."""
    field, sep, raw = text.partition("=")
    if not sep or field not in model.FIELD_TITLES:
        raise ValueError(f"expected PROP=VALUE with PROP one of {', '.join(model.FIELD_TITLES)}")
    if field in _COMBO_FIELDS:
        return field, {"default": model.DEFAULT_ID, "none": model.NONE_ID}.get(raw, raw)
    if field in _INT_FIELDS:
        return field, int(raw)
    if field == model.MENU_ITEMS:
        return field, [i for i in raw.split(",") if i]
    if field in _BOOL_FIELDS:
        if raw.lower() not in ("yes", "no", "true", "false", "1", "0"):
            raise ValueError(f"{field} takes yes or no")
        return field, raw.lower() in ("yes", "true", "1")
    if field == model.LABEL and raw not in model.LABELS:
        raise ValueError(f"label must be one of {', '.join(model.LABELS)}")
    return field, raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qubes-settings-cli",
        description="Show (and change) a qube's settings the way the qubes-settings window does.",
    )
    parser.add_argument("qube")
    parser.add_argument("--set", action="append", default=[], metavar="PROP=VALUE",
                        help="change a setting; 'default' resets, 'none' clears")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock",
                        help="same as --backend mock")
    args = parser.parse_args(argv)

    backend = create_backend(args.backend)
    if backend.is_demo:
        backend.delay = 0
        print(f"# {backend.name}", file=sys.stderr)
    try:
        settings = backend.get_settings(args.qube)
        if args.set:
            values = model.initial_values(settings)
            for text in args.set:
                field, value = parse_assignment(settings, text)
                reason = model.disabled_reason(settings, field)
                if field not in model.shown_fields(settings) or reason:
                    parser.error(f"{field} cannot be changed: {reason or 'not a setting of this qube'}")
                values[field] = value
            changed = model.changes(settings, values)
            if changed:
                backend.apply_settings(args.qube, changed)
                settings = backend.get_settings(args.qube)
    except ValueError as exc:
        parser.error(str(exc))
    except BackendError as exc:
        print(f"qubes-settings-cli: {exc}", file=sys.stderr)
        return 1
    print(format_settings(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
