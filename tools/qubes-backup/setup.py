#!/usr/bin/env python3
"""Packaging for qubes-backup."""

import setuptools

setuptools.setup(
    name="qubes_backup",
    version="0.1.0",
    description="Backup, restore and revert for one qube, in qubes-dashboard's look",
    packages=["qubes_backup", "qubes_backup.backend"],
    python_requires=">=3.9",
    # Without these the window raises FileNotFoundError when it opens, only on
    # a real install.
    package_data={"qubes_backup": ["backup.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-backup = qubes_backup.frontend:main",
        ],
        "console_scripts": [
            "qubes-backup-cli = qubes_backup.cli:main",
        ],
    },
)
