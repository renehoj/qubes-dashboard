#!/usr/bin/env python3
"""Packaging for qubes-settings."""

import setuptools

setuptools.setup(
    name="qubes_settings",
    version="0.1.0",
    description="Settings window for one qube, in qubes-dashboard's look",
    packages=["qubes_settings", "qubes_settings.backend"],
    python_requires=">=3.9",
    # Without these the window raises FileNotFoundError when it opens, only on
    # a real install.
    package_data={"qubes_settings": ["settings.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-settings = qubes_settings.frontend:main",
        ],
        "console_scripts": [
            "qubes-settings-cli = qubes_settings.cli:main",
        ],
    },
)
