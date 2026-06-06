"""Tests for the openai / gemini / anthropic provider additions.

Covers: model_defaults entries, llm_invoke's OpenAI-compatible + Anthropic
invoke functions (HTTP mocked), router tier resolution + invoke dispatch,
and DiscoveryConfig provider sections.
"""

from __future__ import annotations

import sys
import types

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.shared import llm_invoke, llm_router
from ai_discovery.shared.model_defaults import MODELS


@pytest.fixture(autouse=True)
def _clean_router_state(monkeypatch):
    llm_router.configure(None)
    llm_router.reset_config()
    for env_var in llm_router._ENV_MAP.values():
        monkeypatch.delenv(env_var, raising=False)
    yield
    llm_router.configure(None)
    llm_router.reset_config()


# ── model_defaults ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_models_entry_exists_with_tiers(provider):
    entry = MODELS[provider]
    assert entry["url"].startswith("https://")
    for tier in ("fast", "standard", "expert", "heavy", "deep"):
        assert entry[tier], f"{provider} missing tier {tier}"


def test_openai_gemini_have_embedding_anthropic_does_not():
    assert "embedding" in MODELS["openai"]
    assert "embedding" in MODELS["gemini"]
    # Anthropic has no embeddings API
    assert "embedding" not in MODELS["anthropic"]


def test_anthropic_model_ids_are_bare_aliases():
    # Per claude-api guidance: bare aliases, no date suffixes
    assert MODELS["anthropic"]["fast"] == "claude-haiku-4-5"
    assert MODELS["anthropic"]["standard"] == "claude-sonnet-4-6"
    assert MODELS["anthropic"]["expert"] == "claude-opus-4-8"


# ── llm_invoke.invoke_openai_compat ──────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _chat_payload(text="hello", tok_in=11, tok_out=7):
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": tok_in, "completion_tokens": tok_out},
    }


def test_invoke_openai_compat_url_auth_and_parsing(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResponse(_chat_payload("Answer."))

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    text, tok_in, tok_out = llm_invoke.invoke_openai_compat(
        "gpt-5-mini", "Hi", max_tokens=128,
        base_url="https://api.openai.com/v1", api_key="sk-test",
    )
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["model"] == "gpt-5-mini"
    # OpenAI default: max_completion_tokens (gpt-5 family rejects max_tokens)
    assert captured["json"]["max_completion_tokens"] == 128
    assert "max_tokens" not in captured["json"]
    assert (text, tok_in, tok_out) == ("Answer.", 11, 7)


def test_invoke_openai_compat_gemini_field_and_trailing_slash(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(url=url, json=json)
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    llm_invoke.invoke_openai_compat(
        "gemini-3.5-flash", "Hi", max_tokens=64,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key="g-key", max_tokens_field="max_tokens",
    )
    # Trailing slash on base_url must not produce a double slash
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert captured["json"]["max_tokens"] == 64


def test_invoke_openai_compat_wraps_errors(monkeypatch):
    def fake_post(url, json=None, timeout=None, headers=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    with pytest.raises(RuntimeError, match="OpenAI-compatible invocation failed"):
        llm_invoke.invoke_openai_compat("gpt-5-mini", "Hi", base_url="https://x/v1")


def test_embed_openai_compat(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(url=url, json=json)
        return _FakeResponse({"data": [{"embedding": [0.1, 0.2]}]})

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    vec = llm_invoke.embed_openai_compat(
        "text-embedding-3-small", "hello", base_url="https://api.openai.com/v1",
        api_key="sk-test",
    )
    assert captured["url"] == "https://api.openai.com/v1/embeddings"
    assert vec == [0.1, 0.2]


# ── llm_invoke.invoke_anthropic ──────────────────────────────────────────────


def _install_fake_anthropic(monkeypatch, captured):
    """Install a minimal fake `anthropic` module into sys.modules."""

    class _Block:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _Usage:
        input_tokens = 21
        output_tokens = 9

    class _Response:
        content = [_Block("Claude says hi.")]
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Response()

    class _Anthropic:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def test_invoke_anthropic(monkeypatch):
    captured = {}
    _install_fake_anthropic(monkeypatch, captured)
    text, tok_in, tok_out = llm_invoke.invoke_anthropic(
        "claude-opus-4-8", "Hi", max_tokens=256, api_key="sk-ant-test",
    )
    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 256
    assert captured["messages"] == [{"role": "user", "content": "Hi"}]
    assert captured["client_kwargs"]["api_key"] == "sk-ant-test"
    assert (text, tok_in, tok_out) == ("Claude says hi.", 21, 9)


def test_invoke_anthropic_no_key_uses_env_resolution(monkeypatch):
    captured = {}
    _install_fake_anthropic(monkeypatch, captured)
    llm_invoke.invoke_anthropic("claude-haiku-4-5", "Hi")
    # No api_key kwarg → SDK resolves ANTHROPIC_API_KEY from environment
    assert "api_key" not in captured["client_kwargs"]


def test_invoke_anthropic_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match="pip install anthropic"):
        llm_invoke.invoke_anthropic("claude-opus-4-8", "Hi")


# ── llm_invoke.invoke_anthropic_structured ──────────────────────────────────


def _install_fake_anthropic_structured(monkeypatch, captured, content_blocks):
    class _Usage:
        input_tokens = 33
        output_tokens = 12

    class _Response:
        content = content_blocks
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Response()

    class _Anthropic:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name, input_):
        self.name = name
        self.input = input_


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


_SCHEMA = {"type": "object", "properties": {"purpose": {"type": "string"}}}


def test_invoke_anthropic_structured_forced_tool(monkeypatch):
    captured = {}
    _install_fake_anthropic_structured(
        monkeypatch, captured, [_ToolUseBlock("emit", {"purpose": "x"})])
    data, text, tok_in, tok_out = llm_invoke.invoke_anthropic_structured(
        "claude-opus-4-8", "Extract.", _SCHEMA, tool_name="emit", max_tokens=512,
    )
    assert data == {"purpose": "x"}
    assert (tok_in, tok_out) == (33, 12)
    # The tool choice must FORCE the named tool (mirrors the Bedrock path)
    assert captured["tool_choice"] == {"type": "tool", "name": "emit"}
    assert captured["tools"][0]["name"] == "emit"
    assert captured["tools"][0]["input_schema"] == _SCHEMA
    assert captured["max_tokens"] == 512


def test_invoke_anthropic_structured_text_fallback(monkeypatch):
    captured = {}
    _install_fake_anthropic_structured(
        monkeypatch, captured, [_TextBlock('{"purpose": "y"}')])
    data, text, tok_in, tok_out = llm_invoke.invoke_anthropic_structured(
        "claude-opus-4-8", "Extract.", _SCHEMA,
    )
    assert data is None
    assert text == '{"purpose": "y"}'


def test_invoke_anthropic_structured_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match="pip install anthropic"):
        llm_invoke.invoke_anthropic_structured("claude-opus-4-8", "Hi", _SCHEMA)


# ── llm_invoke.invoke_openai_compat_structured ───────────────────────────────


def test_invoke_openai_compat_structured_sends_response_format(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(url=url, json=json)
        return _FakeResponse(_chat_payload('{"purpose": "x"}'))

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    data, text, tok_in, tok_out = llm_invoke.invoke_openai_compat_structured(
        "gpt-5-mini", "Extract.", _SCHEMA, schema_name="emit_screen_spec",
        max_tokens=512, base_url="https://api.openai.com/v1", api_key="sk-test",
    )
    assert data == {"purpose": "x"}
    assert (tok_in, tok_out) == (11, 7)
    rf = captured["json"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "emit_screen_spec"
    assert rf["json_schema"]["schema"] == _SCHEMA
    # OpenAI default output-cap field
    assert captured["json"]["max_completion_tokens"] == 512


def test_invoke_openai_compat_structured_non_json_returns_none(monkeypatch):
    def fake_post(url, json=None, timeout=None, headers=None):
        return _FakeResponse(_chat_payload("Sorry, here is prose."))

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    data, text, _, _ = llm_invoke.invoke_openai_compat_structured(
        "gpt-5-mini", "Extract.", _SCHEMA, base_url="https://api.openai.com/v1",
    )
    assert data is None
    assert text == "Sorry, here is prose."


def test_invoke_openai_compat_structured_gemini_max_tokens_field(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(json=json)
        return _FakeResponse(_chat_payload('{"a": 1}'))

    monkeypatch.setattr(llm_invoke.httpx, "post", fake_post)
    llm_invoke.invoke_openai_compat_structured(
        "gemini-3.5-flash", "Extract.", _SCHEMA, max_tokens=64,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        max_tokens_field="max_tokens",
    )
    assert captured["json"]["max_tokens"] == 64


def test_max_tokens_field_for():
    assert llm_router.max_tokens_field_for("openai") == "max_completion_tokens"
    assert llm_router.max_tokens_field_for("gemini") == "max_tokens"


# ── model_defaults.rates_for_model ($/1M tokens, per model family) ───────────


from ai_discovery.shared.model_defaults import rates_for_model  # noqa: E402


@pytest.mark.parametrize("model,expected", [
    # Claude — current pricing, both direct aliases and Bedrock-prefixed IDs
    ("claude-opus-4-8", (5.0, 25.0)),
    ("us.anthropic.claude-sonnet-4-6", (3.0, 15.0)),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", (1.0, 5.0)),
    # OpenAI gpt-5 family — specific variants must win over the family match
    ("gpt-5-nano", (0.05, 0.40)),
    ("gpt-5-mini", (0.25, 2.0)),
    ("gpt-5.1", (1.25, 10.0)),
    # Gemini — flash-lite must win over flash; pro family is the general match
    ("gemini-2.5-flash-lite", (0.10, 0.40)),
    ("gemini-3.5-flash", (0.30, 2.50)),
    ("gemini-2.5-pro", (1.25, 10.0)),
])
def test_rates_for_model_families(model, expected):
    assert rates_for_model(model) == expected


def test_rates_for_model_local_providers_are_free():
    assert rates_for_model("gemma4:31b", provider="ollama") == (0.0, 0.0)
    assert rates_for_model(
        "mlx-community/Qwen3.5-27B-Claude-4.6-Opus-Distilled-MLX-4bit",
        provider="mlx-qwen") == (0.0, 0.0)


def test_rates_for_model_local_claude_distill_still_free():
    """A local model whose NAME mentions a Claude family must not be billed —
    provider locality wins over name matching."""
    assert rates_for_model("Qwen3.5-27B-Claude-4.6-Opus-Distilled",
                           provider="ollama") == (0.0, 0.0)


def test_rates_for_model_unknown_cloud_uses_conservative_default():
    assert rates_for_model("some-future-model", provider="openai") == (3.0, 15.0)


# ── llm_router: resolution + dispatch ────────────────────────────────────────


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_resolve_tier_target_new_providers(provider):
    llm_router.save_config({"provider": provider})
    p, model, base_url = llm_router.resolve_tier_target("fast")
    assert p == provider
    assert model == MODELS[provider]["fast"]
    assert base_url == MODELS[provider]["url"]


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_resolve_model_id_new_providers(provider):
    llm_router.save_config({"provider": provider})
    assert llm_router.resolve_model_id("standard") == MODELS[provider]["standard"]


def test_invoke_llm_routes_openai(monkeypatch):
    calls = {}

    def fake_invoke(model, prompt, max_tokens=4096, base_url="", api_key="",
                    max_tokens_field="max_completion_tokens"):
        calls.update(model=model, base_url=base_url,
                     max_tokens_field=max_tokens_field)
        return "ok", 1, 2

    monkeypatch.setattr(llm_router, "invoke_openai_compat", fake_invoke)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm_router.save_config({"provider": "openai"})
    out = llm_router.invoke_llm("fast", "Hi")
    assert out == "ok"
    assert calls["model"] == MODELS["openai"]["fast"]
    assert calls["base_url"] == MODELS["openai"]["url"]
    assert calls["max_tokens_field"] == "max_completion_tokens"


def test_invoke_llm_routes_gemini_with_max_tokens_field(monkeypatch):
    calls = {}

    def fake_invoke(model, prompt, max_tokens=4096, base_url="", api_key="",
                    max_tokens_field="max_completion_tokens"):
        calls.update(max_tokens_field=max_tokens_field)
        return "ok", 1, 2

    monkeypatch.setattr(llm_router, "invoke_openai_compat", fake_invoke)
    llm_router.save_config({"provider": "gemini"})
    llm_router.invoke_llm("fast", "Hi")
    assert calls["max_tokens_field"] == "max_tokens"


def test_invoke_llm_routes_anthropic(monkeypatch):
    calls = {}

    def fake_invoke(model, prompt, max_tokens=4096, api_key="", base_url=""):
        calls.update(model=model)
        return "ok", 1, 2

    monkeypatch.setattr(llm_router, "invoke_anthropic", fake_invoke)
    llm_router.save_config({"provider": "anthropic"})
    out = llm_router.invoke_llm("expert", "Hi")
    assert out == "ok"
    assert calls["model"] == MODELS["anthropic"]["expert"]


def test_get_embedding_anthropic_raises():
    llm_router.save_config({"provider": "anthropic"})
    with pytest.raises(RuntimeError, match="embedding"):
        llm_router.get_embedding("hello")


def test_get_embedding_openai_routes(monkeypatch):
    monkeypatch.setattr(
        llm_router, "embed_openai_compat",
        lambda model, text, base_url="", api_key="": [0.5],
    )
    llm_router.save_config({"provider": "openai"})
    assert llm_router.get_embedding("hello") == [0.5]


def test_env_overrides_new_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FAST_OPENAI_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    config = llm_router.get_config()
    assert config["provider"] == "openai"
    assert config["tier.fast.openai_model"] == "env-model"
    assert config["openai.url"] == "https://proxy.example/v1"


# ── DiscoveryConfig integration ──────────────────────────────────────────────


def test_discovery_config_has_provider_sections():
    cfg = DiscoveryConfig()
    assert cfg.openai.base_url == MODELS["openai"]["url"]
    assert cfg.gemini.tier1 == MODELS["gemini"]["fast"]
    assert cfg.anthropic.tier3p == MODELS["anthropic"]["expert"]


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_discovery_config_get_model(provider):
    cfg = DiscoveryConfig()
    cfg.provider = provider
    assert cfg.get_model("tier1") == MODELS[provider]["fast"]
    assert cfg.get_model("tier2") == MODELS[provider]["standard"]


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_discovery_config_get_endpoint(provider):
    cfg = DiscoveryConfig()
    cfg.provider = provider
    base_url, api_key = cfg.get_endpoint()
    assert base_url == MODELS[provider]["url"]


def test_configure_flattens_new_providers():
    cfg = DiscoveryConfig()
    cfg.provider = "openai"
    cfg.openai.tier1 = "dc-fast"
    cfg.gemini.base_url = "https://gem.example/openai"
    cfg.anthropic.tier3p = "dc-opus"
    llm_router.configure(cfg)
    config = llm_router.get_config()
    assert config["provider"] == "openai"
    assert config["tier.fast.openai_model"] == "dc-fast"
    assert config["gemini.url"] == "https://gem.example/openai"
    assert config["tier.heavy.anthropic_model"] == "dc-opus"


def test_config_load_env_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-g")
    cfg = DiscoveryConfig.load(None)
    assert cfg.openai.api_key == "sk-o"
    assert cfg.gemini.api_key == "sk-g"


def test_config_load_yaml_provider_sections(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DISCOVERY_LLM_PROVIDER", raising=False)
    yaml_path = tmp_path / "discovery.yaml"
    yaml_path.write_text(
        "provider: gemini\n"
        "gemini:\n"
        "  tier2: gemini-custom\n"
        "anthropic:\n"
        "  tier1: claude-custom\n"
    )
    cfg = DiscoveryConfig.load(str(yaml_path))
    assert cfg.provider == "gemini"
    assert cfg.gemini.tier2 == "gemini-custom"
    assert cfg.anthropic.tier1 == "claude-custom"
