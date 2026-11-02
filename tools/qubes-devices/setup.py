#!/usr/bin/env python3
"""Packaging for qubes-devices."""

import setuptools

setuptools.setup(
    name="qubes_devices",
    version="0.1.0",
    description="PCI devices window for one qube, in qubes-dashboard's look",
    packages=["qubes_devices", "qubes_devices.backend"],
    python_requires=">=3.9",
    # Without these the window raises FileNotFoundError when it opens, only on
    # a real install.
    package_data={"qubes_devices": ["devices.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-devices = qubes_devices.frontend:main",
        ],
        "console_scripts": [
            "qubes-devices-cli = qubes_devices.cli:main",
        ],
    },
)
