#!/usr/bin/env python3
"""Packaging for qubes-logs."""

import setuptools

setuptools.setup(
    name="qubes_logs",
    version="0.1.0",
    description="Log viewer for one qube, in qubes-dashboard's look",
    packages=["qubes_logs", "qubes_logs.backend"],
    python_requires=">=3.9",
    # Without these the window raises FileNotFoundError when it opens, only on
    # a real install.
    package_data={"qubes_logs": ["logs.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-logs = qubes_logs.frontend:main",
        ],
        "console_scripts": [
            "qubes-logs-cli = qubes_logs.cli:main",
        ],
    },
)
