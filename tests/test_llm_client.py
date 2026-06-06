"""Tests for the multi-provider LLM client.

LLMClient routes plain text invocation through shared.llm_router, which
imports the invoke functions as module-level names — so patches target the
llm_router module namespace. The Bedrock structured-output path
(invoke_structured) still binds converse_bedrock in the llm_client namespace.
"""

from unittest.mock import patch

import pytest

from ai_discovery.ai.llm_client import LLMClient
from ai_discovery.config import DiscoveryConfig

_BEDROCK_TARGET = "ai_discovery.shared.llm_router.invoke_bedrock"
_OLLAMA_TARGET = "ai_discovery.shared.llm_router.invoke_ollama"
_OPENAI_TARGET = "ai_discovery.shared.llm_router.invoke_openai_compat"
_ANTHROPIC_TARGET = "ai_discovery.shared.llm_router.invoke_anthropic"


def test_invoke_bedrock_tier1():
    cfg = DiscoveryConfig(provider="bedrock")
    client = LLMClient(cfg)
    with patch(_BEDROCK_TARGET, return_value=("summary text", 100, 50)):
        result = client.invoke("tier1", "Summarize this code")
        assert result.text == "summary text"
        assert result.tokens_in == 100
        assert result.tokens_out == 50
        assert result.tier == "tier1"
        assert result.model == cfg.get_model("tier1")


def test_invoke_ollama_tier1_passes_num_ctx():
    """Tier1 must forward tier1_num_ctx to invoke_ollama so KV cache stays small."""
    cfg = DiscoveryConfig(provider="ollama")
    client = LLMClient(cfg)
    with patch(_OLLAMA_TARGET, return_value=("text", 80, 40)) as mock_invoke:
        result = client.invoke("tier1", "prompt")
        assert result.text == "text"
        assert mock_invoke.call_args.kwargs["num_ctx"] == cfg.ollama.tier1_num_ctx


def test_invoke_ollama_tier2_omits_num_ctx():
    """Tier2+ should not constrain the context window — flow analysis needs it."""
    cfg = DiscoveryConfig(provider="ollama")
    client = LLMClient(cfg)
    with patch(_OLLAMA_TARGET, return_value=("text", 80, 40)) as mock_invoke:
        client.invoke("tier2", "prompt")
        assert mock_invoke.call_args.kwargs["num_ctx"] is None


def test_invoke_openai_routes_through_router():
    cfg = DiscoveryConfig(provider="openai")
    client = LLMClient(cfg)
    with patch(_OPENAI_TARGET, return_value=("oai text", 60, 30)) as mock_invoke:
        result = client.invoke("tier2", "prompt")
        assert result.text == "oai text"
        assert result.model == cfg.get_model("tier2")
        assert mock_invoke.call_args.kwargs["max_tokens_field"] == "max_completion_tokens"


def test_invoke_anthropic_routes_through_router():
    cfg = DiscoveryConfig(provider="anthropic")
    client = LLMClient(cfg)
    with patch(_ANTHROPIC_TARGET, return_value=("claude text", 70, 35)):
        result = client.invoke("tier3", "prompt")
        assert result.text == "claude text"
        assert result.model == cfg.get_model("tier3")  # expert tier (prod flag aware)


def test_invoke_uses_config_api_key_over_env(monkeypatch):
    """API key set on the config (e.g. from YAML) wins over the env var."""
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    cfg = DiscoveryConfig(provider="openai")
    cfg.openai.api_key = "cfg-key"
    client = LLMClient(cfg)
    with patch(_OPENAI_TARGET, return_value=("t", 1, 1)) as mock_invoke:
        client.invoke("tier1", "p")
        assert mock_invoke.call_args.kwargs["api_key"] == "cfg-key"


def test_cost_tracking():
    cfg = DiscoveryConfig(provider="bedrock")
    client = LLMClient(cfg)
    with patch(_BEDROCK_TARGET, return_value=("text", 1000, 500)):
        client.invoke("tier1", "p1")
        client.invoke("tier1", "p2")
    costs = client.get_costs()
    assert costs["tier1"]["calls"] == 2
    assert costs["tier1"]["tokens_in"] == 2000
    assert costs["tier1"]["tokens_out"] == 1000


def test_total_cost():
    cfg = DiscoveryConfig(provider="bedrock")
    client = LLMClient(cfg)
    with patch(_BEDROCK_TARGET, return_value=("text", 1_000_000, 500_000)):
        client.invoke("tier1", "p")
    assert client.total_cost_usd() > 0


def test_cost_rates_per_model_bedrock_haiku():
    """tier1 on Bedrock runs Haiku 4.5 — $1/1M in, $5/1M out."""
    cfg = DiscoveryConfig(provider="bedrock")
    client = LLMClient(cfg)
    with patch(_BEDROCK_TARGET, return_value=("text", 1_000_000, 500_000)):
        client.invoke("tier1", "p")
    assert client.total_cost_usd() == pytest.approx(1.0 * 1.0 + 0.5 * 5.0)


def test_cost_rates_per_model_openai_mini():
    """tier2 on OpenAI runs gpt-5-mini — $0.25/1M in, $2/1M out."""
    cfg = DiscoveryConfig(provider="openai")
    client = LLMClient(cfg)
    with patch(_OPENAI_TARGET, return_value=("text", 1_000_000, 1_000_000)):
        client.invoke("tier2", "p")
    assert client.total_cost_usd() == pytest.approx(0.25 + 2.0)


def test_cost_rates_local_provider_is_free():
    """Local inference has no per-token cost — phantom cost would trip the
    pipeline budget guard on long Ollama/MLX scans."""
    cfg = DiscoveryConfig(provider="ollama")
    client = LLMClient(cfg)
    with patch(_OLLAMA_TARGET, return_value=("text", 5_000_000, 5_000_000)):
        client.invoke("tier1", "p")
        client.invoke("tier3", "p")
    assert client.total_cost_usd() == 0.0
    assert client.get_costs()["tier1"]["calls"] == 1  # tokens still tracked


def test_invalid_provider():
    cfg = DiscoveryConfig(provider="unknown")
    client = LLMClient(cfg)
    with pytest.raises(ValueError, match="Unknown provider"):
        client.invoke("tier1", "p")


def test_invalid_tier():
    cfg = DiscoveryConfig(provider="bedrock")
    client = LLMClient(cfg)
    with pytest.raises(ValueError, match="Invalid tier"):
        client.invoke("tier9", "p")


# ── Simulated advisor (router-unified) ───────────────────────────────────────


def test_simulated_advisor_works_on_any_provider():
    """The advisor pre-call routes through the router, so non-bedrock/ollama
    providers get advisor support too (previously they silently skipped it)."""
    cfg = DiscoveryConfig(provider="openai")
    cfg.advisor.enabled = True
    cfg.advisor.provider = "simulated"
    client = LLMClient(cfg)
    calls = []

    def fake_invoke(model, prompt, max_tokens=4096, base_url="", api_key="",
                    max_tokens_field="max_completion_tokens"):
        calls.append((model, max_tokens))
        return ("plan" if max_tokens == 256 else "answer"), 10, 5

    with patch(_OPENAI_TARGET, side_effect=fake_invoke):
        result = client.invoke_with_advisor(
            "tier2", "analyze the architecture trade-off here", 512)
    assert result.text == "answer"
    # First call: advisor pre-call (256 tokens, expert model); then executor
    assert calls[0] == (cfg.get_model("tier3"), 256)
    assert calls[1][1] == 512
    assert "advisor" in client.get_costs()


# ── Embeddings (provider override via rag.embedding_provider) ────────────────


def test_embedding_provider_bedrock(monkeypatch):
    cfg = DiscoveryConfig(provider="ollama")
    cfg.rag.embedding_provider = "bedrock"
    client = LLMClient(cfg)
    with patch("ai_discovery.shared.llm_router.embed_bedrock",
               return_value=[0.1]) as mock_embed:
        assert client.get_embedding("hello") == [0.1]
        assert mock_embed.call_args.args[0] == cfg.rag.bedrock_model


def test_embedding_active_ollama_like_provider():
    cfg = DiscoveryConfig(provider="ollama")
    client = LLMClient(cfg)
    with patch("ai_discovery.shared.llm_router.embed_ollama",
               return_value=[0.2]) as mock_embed:
        assert client.get_embedding("hello") == [0.2]
        assert mock_embed.call_args.args[0] == cfg.rag.ollama_model


def test_embedding_openai_provider_while_chatting_anthropic():
    """Anthropic has no embeddings API — rag.embedding_provider must be able
    to point at a different provider."""
    cfg = DiscoveryConfig(provider="anthropic")
    cfg.rag.embedding_provider = "openai"
    client = LLMClient(cfg)
    with patch("ai_discovery.shared.llm_router.embed_openai_compat",
               return_value=[0.3]):
        assert client.get_embedding("hello") == [0.3]


def test_embedding_anthropic_active_with_ollama_fallback():
    """Active provider anthropic + ollama-like embedding_provider routes to
    the embedding provider's own endpoint, not the active provider's."""
    cfg = DiscoveryConfig(provider="anthropic")
    cfg.rag.embedding_provider = "ollama"
    client = LLMClient(cfg)
    with patch("ai_discovery.shared.llm_router.embed_ollama",
               return_value=[0.4]):
        assert client.get_embedding("hello") == [0.4]


def test_embeddings_batch_routes(monkeypatch):
    cfg = DiscoveryConfig(provider="ollama")
    client = LLMClient(cfg)

    def fake_post(url, json=None, timeout=None, headers=None):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [
                    {"index": i, "embedding": [float(i)]}
                    for i in range(len(json["input"]))
                ]}
        return R()

    import ai_discovery.shared.llm_router  # noqa: F401 — patched below
    with patch("httpx.post", side_effect=fake_post):
        vecs = client.get_embeddings(["a", "b"])
    assert vecs == [[0.0], [1.0]]
