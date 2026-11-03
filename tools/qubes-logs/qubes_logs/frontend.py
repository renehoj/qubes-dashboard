"""``qubes-logs <qube>``: the log window on its own.

qubes-dashboard opens :class:`~qubes_logs.window.LogsWindow` in
its own process when this package is importable, and runs this command when it
is only installed elsewhere.
"""

from __future__ import annotations

import argparse
import logging
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402,F401  pylint: disable=unused-import

from . import model  # noqa: E402
from .backend import KINDS, create_backend  # noqa: E402
from .window import WINDOW_ALPHA, LogsWindow  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="qubes-logs",
        description="Show a qube's logs: its console, GUI daemon and qrexec.",
    )
    parser.add_argument("qube", type=model.qube_name)
    parser.add_argument("--backend", choices=KINDS, default="auto",
                        help="where the logs come from (default auto: the files "
                             "when this is dom0, demo logs otherwise)")
    parser.add_argument("--mock", dest="backend", action="store_const", const="mock",
                        help="same as --backend mock")
    parser.add_argument("-a", "--alpha", type=float, default=WINDOW_ALPHA,
                        help=f"window background opacity, 0.0-1.0 (default {WINDOW_ALPHA})")
    args = parser.parse_args(argv)
    args.alpha = max(0.0, min(1.0, args.alpha))
    return args


def main(argv: list[str] | None = None) -> int:
    """Entry point. Exit code 6 means there is no display."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO)
    if not Gtk.init_check()[0]:
        return 6
    backend = create_backend(args.backend)
    logging.getLogger("qubes-logs").info("Backend: %s", backend.name)
    LogsWindow(backend, args.qube, alpha=args.alpha, on_close=Gtk.main_quit)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
