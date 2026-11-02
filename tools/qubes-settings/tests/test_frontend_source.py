"""Source-level checks for the GTK window, which cannot all be tested off dom0.

Same reasoning as qubes-dashboard's test of the same name: dom0 ships GTK4, so
the Gtk and Gdk versions must be pinned and Gtk imported first, and the core
modules must not import gi or qubesadmin at all.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "qubes_settings"
GTK_MODULES = [PACKAGE / "window.py", PACKAGE / "app_picker.py", PACKAGE / "frontend.py"]
CORE = [
    PACKAGE / "model.py",
    PACKAGE / "cli.py",
    PACKAGE / "backend" / "__init__.py",
    PACKAGE / "backend" / "base.py",
    PACKAGE / "backend" / "appmenus.py",
    PACKAGE / "backend" / "mock.py",
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
        for path in GTK_MODULES:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
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
        for name in ("settings.css", "fallback-theme.css"):
            self.assertTrue((PACKAGE / name).is_file())
            self.assertIn(name, setup)

    def test_fallback_defines_every_colour_used(self):
        used = set(_names((PACKAGE / "settings.css").read_text(encoding="utf-8")))
        defined = set(_defined((PACKAGE / "fallback-theme.css").read_text(encoding="utf-8")))
        defined |= set(_defined((PACKAGE / "settings.css").read_text(encoding="utf-8")))
        self.assertEqual(used - defined, set())


def _names(css):
    import re  # pylint: disable=import-outside-toplevel
    return re.findall(r"@([a-z][a-z-]*)", re.sub(r"/\*.*?\*/", "", css, flags=re.S)
                      .replace("@define-color", ""))


def _defined(css):
    import re  # pylint: disable=import-outside-toplevel
    return re.findall(r"@define-color\s+([a-z-]+)", css)


if __name__ == "__main__":
    unittest.main()
