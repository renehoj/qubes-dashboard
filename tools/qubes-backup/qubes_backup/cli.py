"""Headless check: what the window would show and run for a qube.

``python3 -m qubes_backup.cli <qube>`` prints the qube's state, which pages
can run, and the private volume's revisions as the Revert drop-down lists
them. ``--backups QUBE:DIR`` lists the backup files Restore would offer, and
``--commands`` prints the command lines Backup, Restore and Revert would run.
Nothing is backed up, restored or reverted. For checking the backend in dom0
without a display.
"""

from __future__ import annotations

import argparse
import sys

from . import model
from .backend import KINDS, BackendError, create_backend


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qubes-backup-cli",
        description="Show what the backup window would offer for a qube.",
    )
    parser.add_argument("qube")
    parser.add_argument("--backups", metavar="QUBE:DIR",
                        help="list the backup files in DIR of QUBE (dom0 for dom0)")
    parser.add_argument("--commands", action="store_true",
                        help="print the commands the window would run, with example paths")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, out=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    backend = create_backend(args.backend)
    if args.backend == "auto" and backend.is_demo:
        print(f"# {backend.name}: qubesadmin is not here", file=out)
    try:
        if args.backups:
            qube, _, directory = args.backups.partition(":")
            for name in backend.list_backups(qube, directory, start=True):
                print(name, file=out)
            return 0
        info = backend.qube_info(args.qube)
        revisions = [] if info.is_admin else backend.revisions(args.qube)
    except BackendError as exc:
        print(f"qubes-backup-cli: {exc}", file=sys.stderr)
        return 1

    pages = model.available_pages(info)
    print(f"{info.name}: {info.klass}, {info.state}", file=out)
    for page in model.PAGES:
        mark = "+" if page in pages else "-"
        print(f"  {mark} {model.PAGE_TITLES[page]:<8} {model.page_note(page, info)}", file=out)
    print(f"Revisions of {info.name}:{model.VOLUME}, newest first:", file=out)
    for revision in revisions:
        print(f"  {revision.id:<20} {model.revision_label(revision)}", file=out)
    if not revisions:
        print("  none", file=out)

    if args.commands:
        backup = model.BackupRequest(args.qube, "sys-usb", "/media/usb/backups", "-")
        restore = model.RestoreRequest(args.qube, "sys-usb",
                                       "/media/usb/backups/qubes-backup-…", "-")
        print("Commands (the passphrase goes on stdin):", file=out)
        print("  " + model.command_text(model.backup_command(backup)), file=out)
        print("  " + model.command_text(model.restore_command(restore)), file=out)
        if revisions:
            print("  " + model.command_text(model.revert_command(args.qube, revisions[0].id)),
                  file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
