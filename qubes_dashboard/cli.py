"""Headless frontend: print what the window would list.

Useful in dom0 to check the qubesadmin backend without a display, and anywhere
else to check filtering against the demo data::

    python3 -m qubes_dashboard.cli                 # running qubes
    python3 -m qubes_dashboard.cli --all sys       # every qube with "sys" in its name
    python3 -m qubes_dashboard.cli --mock --actions personal
"""

from __future__ import annotations

import argparse
import sys

from . import model
from .backend import KINDS, create_backend


def format_table(qubes: list[model.Qube], with_actions: bool = False) -> str:
    rows = [("NAME", "STATUS", "TEMPLATE") + (("ACTIONS",) if with_actions else ())]
    for qube in qubes:
        template, placeholder = model.template_text(qube)
        row = (qube.name, qube.state, f"({template})" if placeholder and template else template)
        if with_actions:
            actions = model.available_actions(qube)
            row += (" ".join(a for a in model.ACTIONS if a in actions),)
        rows.append(row)
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in rows
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qubes-dashboard-cli",
        description="List qubes the way the qubes-dashboard window does.",
    )
    parser.add_argument("query", nargs="*", help="search words (matched in the name)")
    parser.add_argument("-a", "--all", action="store_true", help="include halted qubes")
    parser.add_argument("--actions", action="store_true", help="show what Enter would offer")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock",
                        help="same as --backend mock")
    args = parser.parse_args(argv)

    backend = create_backend(args.backend)
    qubes = model.visible_qubes(
        backend.list_qubes(), " ".join(args.query), show_all=args.all
    )
    if backend.is_demo:
        print(f"# {backend.name}", file=sys.stderr)
    print(format_table(qubes, with_actions=args.actions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
