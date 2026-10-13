"""Cost accounting (roadmap item M1.11.a): all usage fields priced, per-model price table."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_discovery.ai.llm_client import LLMClient
from ai_discovery.config import DiscoveryConfig
from ai_discovery.llm.cost import Usage, cost_usd, price_for
from ai_discovery.llm.transport import TextResult


@pytest.mark.parametrize("model,provider,expected", [
    ("claude-opus-5-5", "anthropic", (4.0, 20.0, 0.4, 5.0)),
    ("global.anthropic.claude-haiku-5-5", "bedrock", (0.10, 0.50, 0.01, 0.125)),
    ("anthropic/claude-sonnet-5.5", "openrouter", (2.0, 10.0, 0.2, 2.5)),
    ("claude-sonnet-4-6", "anthropic", (3.0, 15.0, 0.3, 3.75)),
    ("gpt-5-mini", "openai", (0.25, 2.0, 0.025, 0.25)),
    ("gemma4:31b", "ollama", (0.0, 0.0, 0.0, 0.0)),
    ("some-future-model", "openai", (3.0, 15.0, 3.0, 3.0)),       # unknown: conservative
])
def test_price_table(model, provider, expected) -> None:
    p = price_for(model, provider)
    assert (p.input, p.output, p.cache_read, p.cache_write) == pytest.approx(expected)


def test_cost_sums_every_usage_field() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=2_000_000,
                  cache_write_tokens=400_000)
    # Opus 5.5: 1M×$4 + 0.1M×$20 + 2M×$0.40 + 0.4M×$5
    assert cost_usd(usage, price_for("claude-opus-5-5", "anthropic")) == pytest.approx(4 + 2 + 0.8 + 2)


def test_client_tracks_cache_tokens_from_the_transport() -> None:
    cfg = DiscoveryConfig(provider="anthropic")
    client = LLMClient(cfg)
    result = TextResult("ok", 1_000_000, 0, cache_read=1_000_000, cache_write=1_000_000)
    with patch("ai_discovery.llm.transport.invoke_anthropic", return_value=result):
        client.invoke("tier1", "p")
    entry = client.get_costs()["tier1"]
    assert entry["cache_read_tokens"] == 1_000_000 and entry["cache_write_tokens"] == 1_000_000
    # Haiku 5.5: $0.10 input + $0.01 read + $0.125 write per 1M
    assert client.total_cost_usd() == pytest.approx(0.10 + 0.01 + 0.125)


def test_plain_tuple_results_still_work() -> None:
    client = LLMClient(DiscoveryConfig(provider="anthropic"))
    with patch("ai_discovery.llm.transport.invoke_anthropic", return_value=("ok", 10, 20)):
        assert client.invoke("tier1", "p").text == "ok"
    assert client.get_costs()["tier1"]["cache_read_tokens"] == 0
