"""Headless check: what the window would show for a qube.

``python3 -m qubes_devices.cli <qube>`` prints every PCI device as the
window's columns, the qube's assigned ones marked, then the warnings a save of
those assignments would show. Nothing is written. For checking the backend in
dom0 without a display.
"""

from __future__ import annotations

import argparse
import sys

from . import model
from .backend import KINDS, BackendError, create_backend


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qubes-devices-cli",
        description="Print a qube's PCI devices as the window shows them.",
    )
    parser.add_argument("qube")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock")
    parser.add_argument("--assigned", action="store_true",
                        help="only the devices assigned to the qube")
    parser.add_argument("--raw", action="store_true",
                        help="print the qvm-pci commands that recreate the assignments")
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
        qd = backend.get_devices(args.qube)
    except BackendError as exc:
        print(f"qubes-devices-cli: {exc}", file=sys.stderr)
        return 1
    if args.raw:
        out.write(model.snapshot_lines(qd.name, qd.assignments))
        return 0
    print(f"{qd.name}: {model.summary(qd)}", file=out)
    session = model.EditSession(qd)
    rows = [("",) + model.COLUMNS]
    for device in qd.devices:
        assignment = session.assignment(device.port_id)
        if args.assigned and assignment is None:
            continue
        rows.append(("*" if assignment else "",) + model.device_cells(device, assignment))
    print(format_table(rows), file=out)
    if not qd.editable and not qd.is_admin:
        print(model.RUNNING_NOTE.format(name=qd.name, state=qd.state.lower()), file=out)
    for text in model.check_save(qd, session):
        print(f"! {text}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
