"""LLM response cache, prompt-caching prefix and cost estimate (roadmap item M1.11.b)."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_discovery.ai.llm_client import LLMClient
from ai_discovery.config import DiscoveryConfig
from ai_discovery.llm.estimate import ScanShape, estimate, shape_from_db
from ai_discovery.llm.transport import TextResult

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "projects" / "spring-boot-app"
_BEDROCK = "ai_discovery.llm.transport.invoke_bedrock"


def _client(tmp_path: Path, *, read: bool = True) -> LLMClient:
    client = LLMClient(DiscoveryConfig(provider="bedrock"))
    client.attach_cache(tmp_path / "llm_cache.db", read=read)
    return client


# ── response cache ────────────────────────────────────────────────────────

def test_llm_cache_repeat_call_is_a_free_hit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with patch(_BEDROCK, return_value=("answer", 1000, 50)) as wire:
        first = client.invoke("tier1", "same prompt", 1024)
        second = client.invoke("tier1", "same prompt", 1024)
        other = client.invoke("tier1", "different prompt", 1024)
    assert wire.call_count == 2                        # second call never left the process
    assert (first.cached, second.cached, other.cached) == (False, True, False)
    assert second.text == "answer" and second.tokens_in == 0
    assert client.get_costs()["tier1"]["calls"] == 2   # the hit cost nothing
    assert client.cache_stats() == {"hits": 1, "writes": 2}


def test_llm_cache_survives_a_new_client(tmp_path: Path) -> None:
    with patch(_BEDROCK, return_value=("answer", 10, 5)):
        _client(tmp_path).invoke("tier2", "p", 512)
    with patch(_BEDROCK) as wire:
        assert _client(tmp_path).invoke("tier2", "p", 512).cached
    wire.assert_not_called()


def test_llm_cache_key_includes_model_and_max_tokens(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with patch(_BEDROCK, return_value=("a", 10, 5)) as wire:
        client.invoke("tier1", "p", 512)
        client.invoke("tier1", "p", 1024)              # different output cap
        client._config.bedrock.fast = "another-model"
        client.invoke("tier1", "p", 512)               # different model
    assert wire.call_count == 3


def test_llm_cache_never_stores_truncated_answers(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with patch(_BEDROCK, return_value=("cut of", 10, 1000)) as wire:   # 1000 ≥ 95% of 1024
        client.invoke("tier1", "p", 1024)
        client.invoke("tier1", "p", 1024)
    assert wire.call_count == 2 and client.cache_stats()["writes"] == 0


def test_llm_cache_full_rescan_records_but_does_not_read(tmp_path: Path) -> None:
    with patch(_BEDROCK, return_value=("a", 10, 5)):
        _client(tmp_path).invoke("tier1", "p", 512)
    with patch(_BEDROCK, return_value=("b", 10, 5)) as wire:
        assert _client(tmp_path, read=False).invoke("tier1", "p", 512).text == "b"
    assert wire.call_count == 1


def test_llm_cache_structured_calls(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client._config.bedrock.standard = "us.anthropic.claude-sonnet-4-6"
    reply = {"content": [{"toolUse": {"name": "emit", "input": {"purpose": "x"}}}], "stop_reason": "tool_use",
             "usage": {"input_tokens": 5, "output_tokens": 5}}
    schema = {"type": "object", "properties": {"purpose": {"type": "string"}}}
    with patch("ai_discovery.ai.llm_client.converse_bedrock", return_value=reply) as wire:
        a = client.invoke_structured("tier2", "p", schema, tool_name="emit")
        b = client.invoke_structured("tier2", "p", schema, tool_name="emit")
    assert wire.call_count == 1 and a.data == b.data == {"purpose": "x"} and b.via_tool


# ── prompt caching (Anthropic system prefix) ──────────────────────────────

def test_system_prefix_is_cache_marked_on_anthropic_and_prepended_elsewhere() -> None:
    seen = {}

    def fake_anthropic(model, prompt, max_tokens=4096, api_key="", base_url="", system=""):
        seen["anthropic"] = (prompt, system)
        return TextResult("ok", 1, 1, cache_read=900)

    with patch("ai_discovery.llm.transport.invoke_anthropic", side_effect=fake_anthropic):
        client = LLMClient(DiscoveryConfig(provider="anthropic"))
        client.invoke("tier1", "task", 256, system="STABLE RULES")
    assert seen["anthropic"] == ("task", "STABLE RULES")
    assert client.get_costs()["tier1"]["cache_read_tokens"] == 900
    with patch(_BEDROCK, return_value=("ok", 1, 1)) as wire:
        LLMClient(DiscoveryConfig(provider="bedrock")).invoke("tier1", "task", 256, system="STABLE RULES")
    assert wire.call_args.args[1] == "STABLE RULES\n\ntask"


def test_anthropic_transport_sends_a_cache_control_system_block(monkeypatch) -> None:
    import sys
    import types

    captured = {}

    class _Messages:
        def create(self, **kw):
            captured.update(kw)
            usage = types.SimpleNamespace(input_tokens=3, output_tokens=2, cache_read_input_tokens=7,
                                          cache_creation_input_tokens=0)
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="hi")], usage=usage)

    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: types.SimpleNamespace(messages=_Messages())
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    from ai_discovery.llm import transport

    result = transport.invoke_anthropic("claude-haiku-5-5", "q", 64, system="RULES")
    assert captured["system"] == [{"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}]
    assert result.cache_read == 7


# ── estimate ──────────────────────────────────────────────────────────────

def _shape(**kw) -> ScanShape:
    return ScanShape(**{"tier1_chunks": 1000, "avg_chunk_tokens": 400, "domains": 20, "scenarios": 100,
                        "screens": 30, **kw})


def test_estimate_has_a_range_and_per_stage_breakdown() -> None:
    cfg = DiscoveryConfig(provider="anthropic")
    est = estimate(cfg, _shape())
    assert 0 < est.low_usd < est.expected_usd < est.high_usd
    assert {s.stage for s in est.stages} >= {"summaries", "flows", "scenario_flows", "screens", "page_prose"}
    assert not {"rollup", "review", "onboarding"} & {s.stage for s in est.stages}   # removed in M3.7.b
    assert est.expected_usd == pytest.approx(sum(s.usd for s in est.stages))
    assert estimate(cfg, _shape(tier1_chunks=2000, domains=40, scenarios=200, screens=60)).expected_usd \
        > est.expected_usd


def test_final_quality_adds_screen_source_verification() -> None:
    draft = DiscoveryConfig(provider="anthropic")
    final = DiscoveryConfig(provider="anthropic", quality="final")
    stages = lambda c: {s.stage for s in estimate(c, _shape()).stages}  # noqa: E731
    assert "screen_review" in stages(final) and "screen_review" not in stages(draft)
    assert estimate(final, _shape()).expected_usd > estimate(draft, _shape()).expected_usd


def test_local_provider_estimates_zero_with_a_note() -> None:
    est = estimate(DiscoveryConfig(provider="ollama"), _shape())
    assert est.expected_usd == 0 and any("local" in n for n in est.notes)


def test_shape_from_a_keyless_scan(tmp_path: Path) -> None:
    from ai_discovery.core.paths import ProjectLayout
    from ai_discovery.pipeline import run_pipeline

    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    cfg = DiscoveryConfig()
    cfg.no_llm = True
    layout = ProjectLayout.for_repo(repo, state_dir=tmp_path / "state")
    run_pipeline(str(repo), "main", "app", tmp_path / "o", cfg, layout=layout)
    shape = shape_from_db(layout.db_path, 1)
    assert shape.tier1_chunks > 0 and shape.domains > 0 and shape.screens == 11 and shape.scenarios > 0
    assert estimate(DiscoveryConfig(provider="anthropic"), shape).expected_usd > 0
