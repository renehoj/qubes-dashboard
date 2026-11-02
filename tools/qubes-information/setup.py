#!/usr/bin/env python3
"""Packaging for qubes-information."""

import setuptools

setuptools.setup(
    name="qubes_information",
    version="0.1.0",
    description="Information window for one qube, in qubes-dashboard's look",
    packages=["qubes_information", "qubes_information.backend"],
    python_requires=">=3.9",
    # Without these the window raises FileNotFoundError when it opens, only on
    # a real install.
    package_data={"qubes_information": ["information.css", "fallback-theme.css"]},
    entry_points={
        "gui_scripts": [
            "qubes-information = qubes_information.frontend:main",
        ],
        "console_scripts": [
            "qubes-information-cli = qubes_information.cli:main",
        ],
    },
)
