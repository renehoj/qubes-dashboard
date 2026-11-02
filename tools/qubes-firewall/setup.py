#!/usr/bin/env python3
"""Packaging for qubes-firewall."""

import setuptools

setuptools.setup(
    name="qubes_firewall",
    version="0.1.0",
    description="Firewall window for one qube, in qubes-dashboard's look",
    packages=["qubes_firewall", "qubes_firewall.backend"],
    python_requires=">=3.9",
    # Without these the window raises FileNotFoundError when it opens, only on
    # a real install.
    package_data={"qubes_firewall": ["firewall.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-firewall = qubes_firewall.frontend:main",
        ],
        "console_scripts": [
            "qubes-firewall-cli = qubes_firewall.cli:main",
        ],
    },
)
