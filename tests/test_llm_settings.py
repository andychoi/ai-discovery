"""LLM settings resolution, roles, provider registry and the invoke path (M1.10.a).

Resolution order (unchanged from the old router): explicit overrides >
DiscoveryConfig > env > catalog defaults — but built explicitly per client,
with no module-global state (``llm_router.configure()`` is gone).
"""

from __future__ import annotations

import pytest

from ai_discovery import llm
from ai_discovery.config import DiscoveryConfig
from ai_discovery.llm import invoke as llm_invoke
from ai_discovery.llm.catalog import DEFAULT_PROVIDER, MODELS
from ai_discovery.llm.settings import _ENV_MAP, settings_from_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env_var in _ENV_MAP.values():
        monkeypatch.delenv(env_var, raising=False)


def _cfg(**kwargs) -> DiscoveryConfig:
    cfg = DiscoveryConfig(**kwargs)
    cfg.provider = kwargs.get("provider", "bedrock")
    return cfg


# ── no module-global config ───────────────────────────────────────────────

def test_router_globals_are_gone():
    from ai_discovery.shared import llm_router

    for name in ("configure", "get_config", "save_config", "reset_config", "set_usage_logger"):
        assert not hasattr(llm_router, name), name


def test_two_clients_with_different_configs_do_not_interfere():
    from ai_discovery.ai.llm_client import LLMClient

    a = LLMClient(_cfg(provider="bedrock"))
    b = LLMClient(_cfg(provider="ollama"))
    assert a.settings().provider == "bedrock" and b.settings().provider == "ollama"
    assert a.settings().provider == "bedrock"          # b did not re-route a


# ── layers ────────────────────────────────────────────────────────────────

def test_defaults_resolution():
    s = settings_from_config(None)
    assert s.provider == DEFAULT_PROVIDER
    assert s.region() == MODELS["bedrock"]["region"]
    assert s.get("tier.fast.bedrock_model") == MODELS["bedrock"]["fast"]
    assert s.get("tier.standard.ollama_model") == MODELS["ollama"]["standard"]


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("LLM_FAST_BEDROCK_MODEL", "env-fast-model")
    s = settings_from_config(None)
    assert s.provider == "bedrock" and s.model("fast") == "env-fast-model"


def test_config_maps_roles_to_router_tiers():
    cfg = _cfg()
    cfg.bedrock.fast, cfg.bedrock.standard, cfg.bedrock.deep = "dc-haiku", "dc-sonnet", "dc-opus"
    s = settings_from_config(cfg)
    assert (s.model("fast"), s.model("standard")) == ("dc-haiku", "dc-sonnet")
    assert s.model("expert") == "dc-sonnet"         # draft: documents use the standard model
    assert s.model("heavy") == "dc-opus"            # heavy is always the deep role


def test_final_quality_switches_documents_to_deep_and_models_override_active_provider():
    cfg = _cfg()
    cfg.quality = "final"
    cfg.bedrock.deep = "dc-opus"
    assert settings_from_config(cfg).model("expert") == "dc-opus"
    cfg.models.deep = "override-opus"
    s = settings_from_config(cfg)
    assert s.model("expert") == "override-opus" and s.model("heavy") == "override-opus"
    assert s.model("expert", "openai") != "override-opus"      # other providers keep their own


def test_config_overrides_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert settings_from_config(_cfg(provider="bedrock")).provider == "bedrock"


def test_config_maps_urls_and_embedding():
    cfg = _cfg(provider="ollama")
    cfg.ollama.base_url = "http://example:11434"
    cfg.mlx_gemma.base_url = "http://example:11435"
    cfg.rag.bedrock_model = "dc-embed"
    s = settings_from_config(cfg)
    assert s.url("ollama") == "http://example:11434" and s.url("mlx-gemma") == "http://example:11435"
    assert s.get("tier.embedding.bedrock_model") == "dc-embed"


def test_explicit_overrides_beat_config_and_are_validated():
    assert settings_from_config(_cfg(provider="bedrock"), {"provider": "ollama"}).provider == "ollama"
    with pytest.raises(ValueError, match="Unknown LLM config key"):
        settings_from_config(None, {"nonsense.key": "x"})


def test_config_api_key_beats_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    cfg = _cfg(provider="openai")
    assert settings_from_config(cfg).api_key("openai") == "env-key"
    cfg.openai.api_key = "cfg-key"
    assert settings_from_config(cfg).api_key("openai") == "cfg-key"


# ── model / target resolution ─────────────────────────────────────────────

def test_unknown_tier_falls_back_to_standard_and_doc_types_map_to_tiers():
    s = settings_from_config(None, {"provider": "bedrock"})
    assert s.model("no-such-tier") == MODELS["bedrock"]["standard"]
    assert s.model(s.tier_for_doc_type("runbook")) == MODELS["bedrock"]["fast"]


def test_resolve_tier_target():
    s = settings_from_config(None, {"provider": "bedrock"})
    assert llm.resolve_tier_target(s, "standard") == ("bedrock", MODELS["bedrock"]["standard"],
                                                      MODELS["bedrock"]["region"])
    s = settings_from_config(None, {"provider": "mlx-gemma"})
    assert llm.resolve_tier_target(s, "fast") == ("mlx-gemma", MODELS["mlx-gemma"]["fast"],
                                                  MODELS["mlx-gemma"]["url"])


# ── roles + registry ──────────────────────────────────────────────────────

def test_roles_map_discovery_tiers():
    assert [llm.role_for_tier(t) for t in ("tier1", "tier2", "screen", "tier3")] == \
        ["fast", "standard", "standard", "deep"]
    assert llm.ROLE_ROUTER_TIER["deep"] == "expert"
    with pytest.raises(ValueError):
        llm.role_for_tier("tier9")


def test_registry_covers_every_catalog_provider_with_capabilities():
    assert set(llm.PROVIDERS) == set(MODELS)
    for spec in llm.PROVIDERS.values():
        assert spec.protocol in {"bedrock", "anthropic", "openai_compat", "ollama"}
        assert spec.structured in {"tool", "json_schema", "prompt"}
        assert spec.timeout_class in {"cloud", "local"}
    assert llm.max_tokens_field_for("openai") == "max_completion_tokens"
    assert llm.max_tokens_field_for("gemini") == "max_tokens"
    assert not llm.get_provider("anthropic").embeddings
    assert not llm.get_provider("ollama").metered and llm.get_provider("bedrock").metered
    with pytest.raises(ValueError, match="unknown LLM provider"):
        llm.get_provider("nope")


# ── usage logging (per call, not a global hook) ───────────────────────────

def test_usage_logger_is_per_call(monkeypatch):
    monkeypatch.setattr(llm.transport, "invoke_bedrock", lambda *a, **k: ("ok", 10, 20))
    calls = []
    s = settings_from_config(None, {"provider": "bedrock"})
    meta = llm.invoke_text(s, "fast", "p", 100, usage_logger=lambda **kw: calls.append(kw))
    assert meta["text"] == "ok" and meta["truncated"] is False
    assert calls == [{"project_slug": "", "model_id": MODELS["bedrock"]["fast"], "tier": "fast",
                      "tokens_in": 10, "tokens_out": 20, "provider": "bedrock"}]


def test_usage_logger_errors_are_swallowed():
    def boom(**kw):
        raise RuntimeError("logger broke")

    llm_invoke._log_usage(boom, "p", "m", "fast", 1, 1, provider="bedrock")   # must not raise
    llm_invoke._log_usage(None, "", "m", "fast", None, None, provider="bedrock")


# ── strip_thinking (behaviour locked before the move) ─────────────────────

def test_strip_thinking_removes_think_tags():
    assert llm.strip_thinking("<think>internal</think>Answer.") == "Answer."


def test_strip_thinking_truncated_tag():
    assert llm.strip_thinking("Answer.<think>cut off mid") == "Answer."


def test_strip_thinking_plain_text_passthrough():
    assert llm.strip_thinking("Just an answer.") == "Just an answer."
