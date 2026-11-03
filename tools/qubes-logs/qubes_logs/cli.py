"""Headless check: what the window would show for a qube.

``python3 -m qubes_logs.cli <qube> [filter words]`` prints the end of the
qube's console log, the same text the window's Copy puts on the clipboard;
``--log guid`` or ``--log qrexec`` picks another tab, and ``--list`` lists
the files. For checking the backend in dom0 without a display.
"""

from __future__ import annotations

import argparse
import sys

from . import model
from .backend import KINDS, create_backend


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qubes-logs-cli",
        description="Print a qube's log, as the window shows it.",
    )
    parser.add_argument("qube", type=model.qube_name)
    parser.add_argument("filter", nargs="*", help="words to filter lines by, as in the window")
    parser.add_argument("--log", default=None,
                        help="which log, by tab title: console (default), gui or qrexec; "
                             "hypervisor for dom0")
    parser.add_argument("--list", action="store_true", help="list the files and their sizes")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, out=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    backend = create_backend(args.backend)
    if args.backend == "auto" and backend.is_demo:
        print(f"# {backend.name}: {model.log_files('x')[0].path} is not here", file=out)
    files = model.log_files(args.qube)

    if args.list:
        for log in backend.read_logs(args.qube):
            print(f"{log.file.title:<10} {model.summary(log)}", file=out)
        return 0

    wanted = (args.log or files[0].title).casefold()
    file = next((f for f in files if f.title.casefold() == wanted), None)
    if file is None:
        titles = ", ".join(f.title.casefold() for f in files)
        print(f"qubes-logs-cli: no {args.log} log for {args.qube}; there is {titles}",
              file=sys.stderr)
        return 2
    log = backend.read_log(file)
    if log.error:
        print(f"qubes-logs-cli: {model.summary(log)}", file=sys.stderr)
        return 1
    shown = model.filter_lines(log.lines, " ".join(args.filter))
    if not shown:
        print("Nothing matches.", file=sys.stderr)
        return 1
    out.write(model.as_text(shown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
