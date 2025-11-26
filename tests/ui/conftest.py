"""
Pytest configuration for UI tests.
"""
import pytest
import os
import sys


def pytest_collection_modifyitems(config, items):
    """Mark UI tests that need a display."""
    for item in items:
        # Skip Qt tests on headless systems without xvfb
        if "ui" in str(item.fspath):
            if sys.platform != "win32" and not os.environ.get("DISPLAY"):
                item.add_marker(pytest.mark.skip(reason="No DISPLAY for Qt tests"))
