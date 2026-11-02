#!/usr/bin/env python3
"""Packaging for qubes-dashboard."""

import setuptools

setuptools.setup(
    name="qubes_dashboard",
    version="0.1.0",
    description="Keyboard-first list of qubes for Qubes OS",
    packages=["qubes_dashboard", "qubes_dashboard.backend"],
    python_requires=">=3.9",
    # Without these the frontend raises FileNotFoundError at startup, only on a
    # real install.
    package_data={"qubes_dashboard": ["dashboard.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-dashboard = qubes_dashboard.frontend:main",
        ],
        "console_scripts": [
            "qubes-dashboard-cli = qubes_dashboard.cli:main",
        ],
    },
)
