"""Source-level checks that hold without GTK: pinned versions, a headless core,
the stylesheets shipped."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "qubes_logs"
CORE = [
    PACKAGE / "model.py",
    PACKAGE / "cli.py",
    PACKAGE / "backend" / "__init__.py",
    PACKAGE / "backend" / "base.py",
    PACKAGE / "backend" / "mock.py",
    PACKAGE / "backend" / "files.py",
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


class SourceTest(unittest.TestCase):
    def test_versions_pinned_before_import(self):
        for name in ("window.py", "frontend.py"):
            with self.subTest(name=name):
                source = (PACKAGE / name).read_text(encoding="utf-8")
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
        for name in ("logs.css", "fallback-theme.css"):
            self.assertTrue((PACKAGE / name).is_file())
            self.assertIn(name, setup)


if __name__ == "__main__":
    unittest.main()
