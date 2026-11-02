"""Console: qvm-console-dispvm for the qube, and the list gets out of the way."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from qubes_dashboard import frontend
except (ImportError, ValueError) as exc:  # ValueError: Gtk 3.0 typelib missing
    frontend = None
    SKIP = f"GTK 3 not available: {exc}"
else:
    SKIP = ""

from qubes_dashboard.backend import BackendError  # noqa: E402


@unittest.skipIf(SKIP, SKIP)
class ConsoleTest(unittest.TestCase):
    def app(self, open_console):
        app = frontend.DashboardApp.__new__(frontend.DashboardApp)
        calls = []
        app.backend = type("B", (), {"open_console": lambda self, name: open_console(calls, name)})()
        app.hide_window = lambda: calls.append("hidden")
        app._show_error = lambda text: calls.append(("error", text))  # pylint: disable=protected-access
        return app, calls

    def test_runs_the_command_and_hides_the_list(self):
        app, calls = self.app(lambda calls, name: calls.append(name))
        app._open_console(frontend.model.Qube("work", "AppVM", "blue", "Running", "t"))  # pylint: disable=protected-access
        self.assertEqual(calls, ["work", "hidden"])

    def test_a_failure_stays_in_the_list(self):
        def fail(_calls, _name):
            raise BackendError("Could not run qvm-console-dispvm")
        app, calls = self.app(fail)
        app._open_console(frontend.model.Qube("work", "AppVM", "blue", "Running", "t"))  # pylint: disable=protected-access
        self.assertEqual(calls, [("error", "Could not run qvm-console-dispvm")])


if __name__ == "__main__":
    unittest.main()
