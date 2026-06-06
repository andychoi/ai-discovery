"""Tests for shared/llm_router.py — DiscoveryConfig-backed config resolution.

The router was vendored from DocHub where overrides lived in an llm_config DB
table. In Discovery it must (a) import without any DB, and (b) resolve config
as: runtime save_config > DiscoveryConfig (via configure()) > env > defaults.
"""

from __future__ import annotations

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.shared import llm_router
from ai_discovery.shared.model_defaults import MODELS, DEFAULT_PROVIDER


@pytest.fixture(autouse=True)
def _clean_router_state(monkeypatch):
    """Each test starts with no DiscoveryConfig registered, no runtime
    overrides, and none of the router's env vars set."""
    llm_router.configure(None)
    llm_router.reset_config()
    for env_var in llm_router._ENV_MAP.values():
        monkeypatch.delenv(env_var, raising=False)
    yield
    llm_router.configure(None)
    llm_router.reset_config()


# ── Importability ─────────────────────────────────────────────────────────────


def test_module_imports_without_db():
    """The module must not require sdlc_db / db.connection at import time."""
    import importlib

    importlib.reload(llm_router)


# ── Defaults layer ────────────────────────────────────────────────────────────


def test_defaults_resolution():
    config = llm_router.get_config()
    assert config["provider"] == DEFAULT_PROVIDER
    assert config["bedrock.region"] == MODELS["bedrock"]["region"]
    assert config["tier.fast.bedrock_model"] == MODELS["bedrock"]["fast"]
    assert config["tier.standard.ollama_model"] == MODELS["ollama"]["standard"]


def test_get_config_returns_copy():
    a = llm_router.get_config()
    a["provider"] = "mutated"
    assert llm_router.get_config()["provider"] != "mutated"


# ── Env layer ────────────────────────────────────────────────────────────────


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("LLM_FAST_BEDROCK_MODEL", "env-fast-model")
    config = llm_router.get_config()
    assert config["provider"] == "bedrock"
    assert config["tier.fast.bedrock_model"] == "env-fast-model"


# ── DiscoveryConfig layer ────────────────────────────────────────────────────


def _discovery_cfg(**kwargs) -> DiscoveryConfig:
    cfg = DiscoveryConfig(**kwargs)
    cfg.provider = kwargs.get("provider", "bedrock")
    return cfg


def test_configure_maps_discovery_tiers_to_router_tiers():
    cfg = _discovery_cfg()
    cfg.bedrock.tier1 = "dc-haiku"
    cfg.bedrock.tier2 = "dc-sonnet"
    cfg.bedrock.tier3d = "dc-sonnet-dev"
    cfg.bedrock.tier3p = "dc-opus-prod"
    llm_router.configure(cfg)

    config = llm_router.get_config()
    assert config["provider"] == "bedrock"
    assert config["tier.fast.bedrock_model"] == "dc-haiku"
    assert config["tier.standard.bedrock_model"] == "dc-sonnet"
    # dev (prod=False): expert resolves to tier3d; heavy always tier3p
    assert config["tier.expert.bedrock_model"] == "dc-sonnet-dev"
    assert config["tier.heavy.bedrock_model"] == "dc-opus-prod"


def test_configure_prod_flag_switches_expert_tier():
    cfg = _discovery_cfg()
    cfg.prod = True
    cfg.bedrock.tier3d = "dc-dev"
    cfg.bedrock.tier3p = "dc-prod"
    llm_router.configure(cfg)
    assert llm_router.get_config()["tier.expert.bedrock_model"] == "dc-prod"


def test_configure_overrides_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    cfg = _discovery_cfg(provider="bedrock")
    llm_router.configure(cfg)
    assert llm_router.get_config()["provider"] == "bedrock"


def test_configure_none_unregisters():
    cfg = _discovery_cfg(provider="bedrock")
    llm_router.configure(cfg)
    llm_router.configure(None)
    assert llm_router.get_config()["provider"] == DEFAULT_PROVIDER


def test_configure_maps_urls_and_embedding():
    cfg = _discovery_cfg(provider="ollama")
    cfg.ollama.base_url = "http://example:11434"
    cfg.mlx_gemma.base_url = "http://example:11435"
    cfg.rag.bedrock_model = "dc-embed"
    llm_router.configure(cfg)
    config = llm_router.get_config()
    assert config["ollama.url"] == "http://example:11434"
    assert config["mlx-gemma.url"] == "http://example:11435"
    assert config["tier.embedding.bedrock_model"] == "dc-embed"


# ── Runtime override layer (save_config / reset_config) ─────────────────────


def test_save_config_beats_discovery_config():
    cfg = _discovery_cfg(provider="bedrock")
    llm_router.configure(cfg)
    llm_router.save_config({"provider": "ollama"})
    assert llm_router.get_config()["provider"] == "ollama"


def test_save_config_rejects_unknown_keys():
    with pytest.raises(ValueError, match="Unknown LLM config key"):
        llm_router.save_config({"nonsense.key": "x"})


def test_reset_config_single_key():
    llm_router.save_config({"provider": "bedrock", "bedrock.region": "eu-west-1"})
    removed = llm_router.reset_config("provider")
    assert removed == 1
    config = llm_router.get_config()
    assert config["provider"] == DEFAULT_PROVIDER
    assert config["bedrock.region"] == "eu-west-1"


def test_reset_config_all():
    llm_router.save_config({"provider": "bedrock", "bedrock.region": "eu-west-1"})
    removed = llm_router.reset_config()
    assert removed == 2
    assert llm_router.get_config()["bedrock.region"] == MODELS["bedrock"]["region"]


def test_reset_config_unknown_key_is_noop():
    assert llm_router.reset_config("not-a-key") == 0


# ── Tier / model resolution ──────────────────────────────────────────────────


def test_resolve_model_id_bedrock():
    llm_router.save_config({"provider": "bedrock"})
    assert llm_router.resolve_model_id("fast") == MODELS["bedrock"]["fast"]
    # Unknown tier falls back to standard
    assert llm_router.resolve_model_id("no-such-tier") == MODELS["bedrock"]["standard"]


def test_resolve_model_id_doc_type_derives_tier():
    llm_router.save_config({"provider": "bedrock"})
    # 'runbook' maps to fast per DOC_TYPE_TIER
    assert (
        llm_router.resolve_model_id("ignored", doc_type="runbook")
        == MODELS["bedrock"]["fast"]
    )


def test_resolve_tier_target_bedrock():
    llm_router.save_config({"provider": "bedrock"})
    provider, model, region = llm_router.resolve_tier_target("standard")
    assert provider == "bedrock"
    assert model == MODELS["bedrock"]["standard"]
    assert region == MODELS["bedrock"]["region"]


def test_resolve_tier_target_ollama_like():
    llm_router.save_config({"provider": "mlx-gemma"})
    provider, model, base_url = llm_router.resolve_tier_target("fast")
    assert provider == "mlx-gemma"
    assert model == MODELS["mlx-gemma"]["fast"]
    assert base_url == MODELS["mlx-gemma"]["url"]


# ── Usage logging hook ───────────────────────────────────────────────────────


def test_usage_logger_hook():
    calls = []
    llm_router.set_usage_logger(
        lambda **kw: calls.append(kw)
    )
    try:
        llm_router._log_usage("proj", "model-x", "fast", 10, 20, provider="bedrock")
    finally:
        llm_router.set_usage_logger(None)
    assert calls == [{
        "project_slug": "proj", "model_id": "model-x", "tier": "fast",
        "tokens_in": 10, "tokens_out": 20, "provider": "bedrock",
    }]


def test_usage_logger_default_is_silent_noop():
    llm_router.set_usage_logger(None)
    # Must not raise even with odd inputs (None tokens)
    llm_router._log_usage("", "m", "fast", None, None)


def test_usage_logger_exceptions_are_swallowed():
    def boom(**kw):
        raise RuntimeError("logger broke")

    llm_router.set_usage_logger(boom)
    try:
        llm_router._log_usage("p", "m", "fast", 1, 1)  # must not raise
    finally:
        llm_router.set_usage_logger(None)


# ── strip_thinking (behavior locked before refactor) ─────────────────────────


def test_strip_thinking_removes_think_tags():
    assert llm_router.strip_thinking("<think>internal</think>Answer.") == "Answer."


def test_strip_thinking_truncated_tag():
    assert llm_router.strip_thinking("Answer.<think>cut off mid") == "Answer."


def test_strip_thinking_plain_text_passthrough():
    assert llm_router.strip_thinking("Just an answer.") == "Just an answer."
