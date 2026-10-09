"""Shared test isolation."""

from __future__ import annotations

import pytest

# Developer credentials/selection (shell or .env via load_dotenv) must never
# reach tests: they change resolution and can surface in assertion output.
_LLM_ENV = (
    "DISCOVERY_LLM_PROVIDER", "DISCOVERY_LLM_API_KEY", "DISCOVERY_LLM_MODEL",
    "AIGW_API_KEY", "AIGW_BASE_URL", "OMLX_API_KEY", "OMLX_BASE_URL",
    "OPENROUTER_API_KEY", "OPENROUTER_API", "OPENROUTER_BASE_URL", "OPENROUTER_EMBEDDING_KEY",
    "DISCOVERY_EMBEDDING_PROVIDER", "DISCOVERY_EMBEDDING_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path_factory):
    """Keep the developer's ~/.config and the repo's own discovery.yaml out of
    tests (M1.3 auto-loads both). Tests that exercise auto-discovery pass an
    explicit ``cwd=`` to the loader, which bypasses this guard."""
    from ai_discovery.config import loader

    for var in _LLM_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    original = loader.project_config_path
    monkeypatch.setattr(
        loader, "project_config_path",
        lambda cwd=None: original(cwd) if cwd is not None else None,
    )
