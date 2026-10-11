"""Slow end-to-end smoke tests (roadmap item M4.5).

Skipped unless ``RUN_SLOW=1`` or ``-m slow`` is given, so ``pytest -q`` stays fast.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items) -> None:
    if os.environ.get("RUN_SLOW") == "1" or "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow smoke test: set RUN_SLOW=1 or pass -m slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
