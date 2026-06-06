"""Tests for Tier 1 chunk summarizer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from ai_discovery.ai.summarizer import (
    _SKIP_TYPES,
    _build_prompt,
    _parse_response,
    summarize_chunk,
    summarize_chunks,
)
from ai_discovery.graph.models import CodeChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(
    text: str = "def hello(): pass",
    chunk_type: str = "function",
    qualified_name: str = "mod.hello",
    language: str = "python",
    file_path: str = "mod.py",
    **kwargs,
) -> CodeChunk:
    return CodeChunk(
        text=text,
        chunk_index=0,
        chunk_type=chunk_type,
        file_path=file_path,
        language=language,
        qualified_name=qualified_name,
        **kwargs,
    )


@dataclass
class _FakeLLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    tier: str


def _make_llm_client(response_text: str = "") -> MagicMock:
    """Return a mock LLMClient that returns a canned response."""
    if not response_text:
        response_text = json.dumps({
            "purpose": "Says hello",
            "business_rules": "None detected",
            "io_summary": "No I/O",
            "tech_debt_signals": "None detected",
        })
    client = MagicMock()
    client.invoke.return_value = _FakeLLMResponse(
        text=response_text,
        tokens_in=100,
        tokens_out=50,
        model="haiku-test",
        tier="tier1",
    )
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_prompt_includes_code():
    """Verify prompt contains the chunk's source code."""
    chunk = _make_chunk(text="def compute_tax(amount): return amount * 0.1")
    prompt = _build_prompt(chunk)
    assert "def compute_tax(amount): return amount * 0.1" in prompt
    assert "mod.hello" in prompt or "mod.compute_tax" in prompt  # qualified_name
    assert "python" in prompt  # language


def test_parse_response_valid_json():
    """Verify JSON parsing returns all 4 fields."""
    raw = json.dumps({
        "purpose": "Calculates tax",
        "business_rules": "10% flat rate",
        "io_summary": "Input: amount; Output: float",
        "tech_debt_signals": "No validation",
    })
    result = _parse_response(raw)
    assert result["purpose"] == "Calculates tax"
    assert result["business_rules"] == "10% flat rate"
    assert result["io_summary"] == "Input: amount; Output: float"
    assert result["tech_debt_signals"] == "No validation"


def test_parse_response_non_json():
    """Verify graceful handling of plain text response."""
    raw = "This function calculates tax at a flat 10% rate. No notable debt."
    result = _parse_response(raw)
    # Should not crash; purpose should contain something
    assert isinstance(result, dict)
    assert "purpose" in result
    assert len(result["purpose"]) > 0


def test_summarize_chunk_calls_tier1():
    """Mock LLM, verify tier1 is used."""
    chunk = _make_chunk()
    client = _make_llm_client()
    result = summarize_chunk(chunk, client)

    client.invoke.assert_called_once()
    call_args = client.invoke.call_args
    assert call_args[0][0] == "tier1"  # first positional arg is tier
    assert result["tier"] == "tier1"
    assert result["purpose"] == "Says hello"
    assert result["tokens_in"] == 100
    assert result["tokens_out"] == 50


def test_summarize_chunks_skips_dtos():
    """Include a chunk with chunk_type='dto', verify it's skipped."""
    dto_chunk = _make_chunk(chunk_type="dto", qualified_name="mod.MyDto")
    fn_chunk = _make_chunk(chunk_type="function", qualified_name="mod.process")
    client = _make_llm_client()

    results = summarize_chunks([dto_chunk, fn_chunk], client)

    assert len(results) == 1
    assert results[0]["qualified_name"] == "mod.process"
    # LLM should only be called once (for the function, not the dto)
    assert client.invoke.call_count == 1


def test_summarize_chunks_concurrent():
    """3 chunks, max_concurrent=2, verify all 3 get summarized."""
    chunks = [
        _make_chunk(qualified_name=f"mod.fn_{i}", chunk_type="function")
        for i in range(3)
    ]
    client = _make_llm_client()

    results = summarize_chunks(chunks, client, max_concurrent=2)

    assert len(results) == 3
    assert client.invoke.call_count == 3
    names = {r["qualified_name"] for r in results}
    assert names == {"mod.fn_0", "mod.fn_1", "mod.fn_2"}


def test_summarize_chunks_progress_callback():
    """Verify on_progress called with correct counts."""
    chunks = [
        _make_chunk(qualified_name=f"mod.fn_{i}", chunk_type="method")
        for i in range(4)
    ]
    client = _make_llm_client()
    progress_calls: list[tuple[int, int]] = []

    def on_progress(completed: int, total: int):
        progress_calls.append((completed, total))

    summarize_chunks(chunks, client, on_progress=on_progress, max_concurrent=2)

    # Should be called once per chunk
    assert len(progress_calls) == 4
    # All calls should have total=4
    assert all(t == 4 for _, t in progress_calls)
    # Completed counts should be 1..4 (in some order due to concurrency)
    assert sorted(c for c, _ in progress_calls) == [1, 2, 3, 4]


def test_summarize_chunks_stops_on_budget():
    """P1-e: Tier-1 stops summarizing once the budget predicate trips, returning
    a partial result instead of running every chunk."""
    chunks = [
        _make_chunk(qualified_name=f"mod.fn{i}", text=f"def fn{i}(): pass")
        for i in range(8)
    ]
    client = _make_llm_client()
    results = summarize_chunks(
        chunks, client, max_concurrent=1, skip_rag=True,
        budget_exhausted=lambda: True,  # exhausted from the first completion
    )
    assert 0 < len(results) < len(chunks)


# ---------------------------------------------------------------------------
# Batched Tier-1 summarization (assessment 07 A-1 — Louvain semantic batching)
# ---------------------------------------------------------------------------

from ai_discovery.ai.summarizer import _build_batch_prompt, summarize_batch  # noqa: E402
from ai_discovery.graph.models import CallEdge  # noqa: E402


@dataclass
class _FakeStructuredResponse:
    data: dict
    tokens_in: int = 200
    tokens_out: int = 80
    model: str = "haiku-test"
    tier: str = "tier1"
    via_tool: bool = True
    raw_text: str = ""


def _summary_payload(qnames):
    return {
        "summaries": [
            {
                "qualified_name": q,
                "purpose": f"Does {q}",
                "business_rules": "None detected",
                "io_summary": "No I/O",
                "tech_debt_signals": "None detected",
            }
            for q in qnames
        ]
    }


def _batch_chunks():
    return [
        _make_chunk(qualified_name="orders.create", file_path="src/orders/svc.py"),
        _make_chunk(qualified_name="orders.save", file_path="src/orders/repo.py"),
        _make_chunk(qualified_name="orders.post", file_path="src/orders/api.py"),
    ]


def test_build_batch_prompt_contains_all_chunks():
    chunks = _batch_chunks()
    prompt = _build_batch_prompt(chunks)
    for c in chunks:
        assert c.qualified_name in prompt
        assert c.text in prompt
    # one shared instruction block, not one per chunk
    assert prompt.count("same module community") == 1


def test_summarize_batch_one_call_per_batch():
    chunks = _batch_chunks()
    client = MagicMock()
    client.invoke_structured.return_value = _FakeStructuredResponse(
        data=_summary_payload([c.qualified_name for c in chunks])
    )
    results = summarize_batch(chunks, client)
    assert client.invoke_structured.call_count == 1
    assert client.invoke.call_count == 0
    assert {r["qualified_name"] for r in results} == {c.qualified_name for c in chunks}
    by_q = {r["qualified_name"]: r for r in results}
    assert by_q["orders.create"]["purpose"] == "Does orders.create"
    # batch token cost split across members so per-node ledger rows stay sane
    assert sum(r["tokens_in"] for r in results) == 200


def test_summarize_batch_falls_back_per_chunk_for_missing_members():
    """A member the model skipped must get an individual Tier-1 call, not a
    silent empty summary."""
    chunks = _batch_chunks()
    client = _make_llm_client()
    client.invoke_structured.return_value = _FakeStructuredResponse(
        data=_summary_payload(["orders.create", "orders.save"])  # orders.post missing
    )
    results = summarize_batch(chunks, client)
    assert {r["qualified_name"] for r in results} == {c.qualified_name for c in chunks}
    assert client.invoke.call_count == 1  # one per-chunk fallback call
    by_q = {r["qualified_name"]: r for r in results}
    assert by_q["orders.post"]["purpose"] == "Says hello"


def test_summarize_batch_falls_back_per_chunk_on_call_failure():
    chunks = _batch_chunks()
    client = _make_llm_client()
    client.invoke_structured.side_effect = RuntimeError("provider down")
    results = summarize_batch(chunks, client)
    assert {r["qualified_name"] for r in results} == {c.qualified_name for c in chunks}
    assert client.invoke.call_count == 3


def test_summarize_chunks_semantic_batching_groups_calls():
    """With call edges + batching enabled, Tier-1 fires one structured call
    per community instead of one plain call per chunk."""
    chunks = [
        _make_chunk(qualified_name="orders.create", file_path="src/orders/svc.py"),
        _make_chunk(qualified_name="orders.save", file_path="src/orders/repo.py"),
        _make_chunk(qualified_name="orders.post", file_path="src/orders/api.py"),
        _make_chunk(qualified_name="billing.charge", file_path="src/billing/svc.py"),
        _make_chunk(qualified_name="billing.put", file_path="src/billing/repo.py"),
        _make_chunk(qualified_name="billing.pay", file_path="src/billing/api.py"),
    ]
    edges = [
        CallEdge(caller="orders.post", callee="orders.create", edge_type="direct_call"),
        CallEdge(caller="orders.create", callee="orders.save", edge_type="direct_call"),
        CallEdge(caller="billing.pay", callee="billing.charge", edge_type="direct_call"),
        CallEdge(caller="billing.charge", callee="billing.put", edge_type="direct_call"),
    ]
    client = MagicMock()

    def _structured(tier, prompt, schema, **kwargs):
        qnames = [c.qualified_name for c in chunks if c.qualified_name in prompt]
        return _FakeStructuredResponse(data=_summary_payload(qnames))

    client.invoke_structured.side_effect = _structured

    progress: list[tuple[int, int]] = []
    results = summarize_chunks(
        chunks, client, call_edges=edges, semantic_batching=True,
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert client.invoke_structured.call_count == 2  # one per community
    assert client.invoke.call_count == 0
    assert {r["qualified_name"] for r in results} == {c.qualified_name for c in chunks}
    # progress reports chunk counts, ending at (6, 6)
    assert progress[-1] == (6, 6)


def test_summarize_chunks_batching_off_keeps_per_chunk_path():
    chunks = [_make_chunk(qualified_name=f"m.f{i}", file_path=f"f{i}.py") for i in range(3)]
    client = _make_llm_client()
    results = summarize_chunks(chunks, client, semantic_batching=False)
    assert client.invoke.call_count == 3
    assert client.invoke_structured.call_count == 0
    assert len(results) == 3


def test_summarize_chunks_batching_respects_budget_stop():
    """budget_exhausted must stop after the current batch, skipping the rest."""
    chunks = [
        _make_chunk(qualified_name=f"a.f{i}", file_path=f"src/a/f{i}.py") for i in range(3)
    ] + [
        _make_chunk(qualified_name=f"b.f{i}", file_path=f"src/b/f{i}.py") for i in range(3)
    ]
    edges = (
        [CallEdge(caller=f"a.f{i}", callee=f"a.f{(i+1)%3}", edge_type="direct_call") for i in range(3)]
        + [CallEdge(caller=f"b.f{i}", callee=f"b.f{(i+1)%3}", edge_type="direct_call") for i in range(3)]
    )
    client = MagicMock()

    def _structured(tier, prompt, schema, **kwargs):
        qnames = [c.qualified_name for c in chunks if c.qualified_name in prompt]
        return _FakeStructuredResponse(data=_summary_payload(qnames))

    client.invoke_structured.side_effect = _structured
    results = summarize_chunks(
        chunks, client, call_edges=edges, semantic_batching=True,
        max_concurrent=1, budget_exhausted=lambda: True,
    )
    # stopped after the first completed batch — not all 6 summarized
    assert 0 < len(results) <= 3
