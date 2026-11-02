"""Source-level checks for the GTK frontend, which cannot be unit-tested off dom0.

Same reasoning as qubes-launcher's test of the same name: dom0 ships GTK4, so the
Gtk and Gdk versions must be pinned and Gtk imported first, and the core modules
must not import gi or qubesadmin at all.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "qubes_dashboard"
FRONTEND = PACKAGE / "frontend.py"
CORE = [
    PACKAGE / "model.py",
    PACKAGE / "cli.py",
    PACKAGE / "backend" / "__init__.py",
    PACKAGE / "backend" / "base.py",
    PACKAGE / "backend" / "mock.py",
    PACKAGE / "backend" / "appmenus.py",
]


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        # Top-level imports only: create_backend imports qubesadmin lazily.
        if isinstance(node, ast.Import) and node.col_offset == 0:
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.col_offset == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


class FrontendSourceTest(unittest.TestCase):
    def test_versions_pinned_before_import(self):
        source = FRONTEND.read_text(encoding="utf-8")
        gtk = source.index('gi.require_version("Gtk", "3.0")')
        gdk = source.index('gi.require_version("Gdk", "3.0")')
        imp = source.index("from gi.repository import")
        self.assertLess(gtk, gdk)
        self.assertLess(gdk, imp)
        self.assertIn("from gi.repository import Gtk, Gdk", source)

    def test_core_is_headless(self):
        for path in CORE:
            with self.subTest(path=path.name):
                self.assertFalse(imported_modules(path) & {"gi", "qubesadmin"})

    def test_css_shipped(self):
        setup = (ROOT / "setup.py").read_text(encoding="utf-8")
        for name in ("dashboard.css", "fallback-theme.css"):
            self.assertTrue((PACKAGE / name).is_file())
            self.assertIn(name, setup)

    def test_app_id_matches_packaging(self):
        source = FRONTEND.read_text(encoding="utf-8")
        self.assertIn('APP_ID = "org.qubesos.dashboard"', source)
        for name in ("qubes-dashboard.service", "dbus-qubes-dashboard.service",
                     "open-qubes-dashboard.desktop"):
            self.assertIn("org.qubesos.dashboard",
                          (ROOT / "packaging" / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
