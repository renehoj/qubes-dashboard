"""Headless check: what the window would show for a qube.

``python3 -m qubes_firewall.cli <qube>`` prints the rule list as the window's
columns, then the warnings a save of that list would show. Nothing is written.
For checking the backend in dom0 without a display.
"""

from __future__ import annotations

import argparse
import sys

from . import model
from .backend import KINDS, BackendError, create_backend


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qubes-firewall-cli",
        description="Print a qube's firewall rules as the window shows them.",
    )
    parser.add_argument("qube")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock")
    parser.add_argument("--raw", action="store_true",
                        help="print the Admin API lines instead (qvm-firewall --raw)")
    return parser.parse_args(argv)


def format_table(rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip()
                     for row in rows)


def main(argv: list[str] | None = None, out=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    backend = create_backend(args.backend)
    if args.backend == "auto" and backend.is_demo:
        print(f"# {backend.name}: qubesadmin is not installed", file=out)
    try:
        firewall = backend.get_firewall(args.qube)
    except BackendError as exc:
        print(f"qubes-firewall-cli: {exc}", file=sys.stderr)
        return 1
    if args.raw:
        out.write(firewall.rules.to_api_text())
        return 0
    print(f"{firewall.name}: {model.summary(firewall)}", file=out)
    if firewall.is_admin:
        return 0
    rows = [model.COLUMNS] + [model.rule_cells(i, r) for i, r in enumerate(firewall.rules)]
    print(format_table(rows), file=out)
    print(model.IMPLICIT_TEXT, file=out)
    problems, warnings = model.check_save(firewall, firewall.rules)
    for text in problems + warnings:
        print(f"! {text}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
