"""Shared test isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path_factory):
    """Keep the developer's ~/.config and the repo's own discovery.yaml out of
    tests (M1.3 auto-loads both). Tests that exercise auto-discovery pass an
    explicit ``cwd=`` to the loader, which bypasses this guard."""
    from ai_discovery.config import loader

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    original = loader.project_config_path
    monkeypatch.setattr(
        loader, "project_config_path",
        lambda cwd=None: original(cwd) if cwd is not None else None,
    )
