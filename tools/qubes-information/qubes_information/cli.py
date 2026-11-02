"""Headless check: what the window would show for a qube.

``python3 -m qubes_information.cli <qube> [filter words]`` prints the page as
text, the same text the window's Copy puts on the clipboard. For checking the
backend in dom0 without a display.
"""

from __future__ import annotations

import argparse
import sys

from . import model
from .backend import KINDS, BackendError, create_backend


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qubes-information-cli",
        description="Print everything about a qube, as the window shows it.",
    )
    parser.add_argument("qube")
    parser.add_argument("filter", nargs="*", help="words to filter rows by, as in the window")
    parser.add_argument("--backend", choices=KINDS, default="auto")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, out=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    backend = create_backend(args.backend)
    if args.backend == "auto" and backend.is_demo:
        print(f"# {backend.name}: qubesadmin is not installed", file=out)
    try:
        info = backend.get_info(args.qube)
    except BackendError as exc:
        print(f"qubes-information-cli: {exc}", file=sys.stderr)
        return 1
    shown = model.filter_sections(model.sections(info), " ".join(args.filter))
    if not shown:
        print("Nothing matches.", file=sys.stderr)
        return 1
    out.write(model.as_text(shown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
