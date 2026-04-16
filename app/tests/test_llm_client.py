"""Tests for the multi-provider LLM client."""

from unittest.mock import patch

import pytest

from app.ai.llm_client import LLMClient
from app.config import DiscoveryConfig

# LLMClient imports invoke_bedrock / invoke_ollama as module-level names
# from app.shared.llm_invoke, so patches must target the llm_client module
# namespace (where the names are bound), not shared.llm_invoke.
_BEDROCK_TARGET = "app.ai.llm_client.invoke_bedrock"
_OLLAMA_TARGET = "app.ai.llm_client.invoke_ollama"


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


def test_invalid_provider():
    cfg = DiscoveryConfig(provider="unknown")
    client = LLMClient(cfg)
    with pytest.raises(ValueError, match="Unknown provider"):
        client.invoke("tier1", "p")
