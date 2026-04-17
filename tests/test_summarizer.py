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
