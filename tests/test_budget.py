"""Budget enforcement for every paid provider (roadmap item M0.5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ai_discovery.config import DiscoveryConfig
from ai_discovery.pipeline import _budget_exhausted_fn, _budget_ok, _is_metered

PAID = ["bedrock", "openai", "gemini", "anthropic"]
LOCAL = ["ollama", "mlx-gemma", "mlx-qwen"]


def _client(cost: float) -> MagicMock:
    client = MagicMock()
    client.total_cost_usd.return_value = cost
    return client


@pytest.mark.parametrize("provider", PAID)
def test_paid_providers_are_metered(provider: str) -> None:
    assert _is_metered(provider)


@pytest.mark.parametrize("provider", LOCAL)
def test_local_providers_are_not_metered(provider: str) -> None:
    assert not _is_metered(provider)


@pytest.mark.parametrize("provider", PAID)
def test_budget_ok_stops_every_paid_provider(provider: str) -> None:
    cfg = DiscoveryConfig(provider=provider, budget_limit_usd=1.0)
    assert _budget_ok(_client(0.5), cfg, "phase")
    assert not _budget_ok(_client(1.0), cfg, "phase")


@pytest.mark.parametrize("provider", LOCAL)
def test_budget_ok_ignores_local_providers(provider: str) -> None:
    cfg = DiscoveryConfig(provider=provider, budget_limit_usd=1.0)
    assert _budget_ok(_client(999.0), cfg, "phase")


@pytest.mark.parametrize("provider", PAID)
def test_mid_phase_predicate_for_paid_providers(provider: str) -> None:
    cfg = DiscoveryConfig(provider=provider, budget_limit_usd=1.0)
    exhausted = _budget_exhausted_fn(_client(2.0), cfg)
    assert exhausted is not None and exhausted()


def test_mid_phase_predicate_absent_for_local_or_unlimited() -> None:
    assert _budget_exhausted_fn(_client(2.0), DiscoveryConfig(provider="ollama", budget_limit_usd=1.0)) is None
    assert _budget_exhausted_fn(_client(2.0), DiscoveryConfig(provider="openai", budget_limit_usd=0.0)) is None


def test_pipeline_returns_budget_exceeded_for_openai(tmp_path: Path) -> None:
    """End-to-end: an over-budget OpenAI scan stops and records budget_exceeded."""
    import sqlite3

    from tests.golden.stub_llm import StubLLM
    from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    stub = StubLLM()
    stub.total_cost_usd = lambda: 5.0  # already over the $1 limit
    from ai_discovery.ai.self_review import ReviewClaim

    with (
        patch("ai_discovery.ai.llm_client.LLMClient", return_value=stub),
        patch("ai_discovery.rag.embedder.embed_chunks", return_value={"embedded": 0, "dim": 256}),
        patch("ai_discovery.rag.retriever.search", return_value=[]),
        patch("ai_discovery.ai.self_review.review_document",
              return_value=[ReviewClaim(claim_text="c", status="verified")]),
        patch("ai_discovery.ai.self_review.persist_claims"),
    ):
        from ai_discovery.pipeline import run_pipeline

        status = run_pipeline(
            repo=str(repo), branch="main", project_slug="b", output_dir=tmp_path / "out",
            docs_root=tmp_path / "docs",
            config=DiscoveryConfig(provider="openai", budget_limit_usd=1.0, max_concurrent=1),
        )
    assert status == "budget_exceeded"
    conn = sqlite3.connect(tmp_path / "out" / "output-b" / "discovery-b.db")
    try:
        assert conn.execute("SELECT status FROM scan_runs").fetchone()[0] == "budget_exceeded"
    finally:
        conn.close()


@pytest.mark.parametrize("status,code", [("budget_exceeded", 3), ("completed", 0), (None, 0)])
def test_scan_cli_exit_code(status, code, tmp_path: Path) -> None:
    from ai_discovery.cli import app

    with patch("ai_discovery.pipeline.run_pipeline", return_value=status):
        result = CliRunner().invoke(app, ["scan", str(tmp_path), "-p", "x", "-o", str(tmp_path / "o")])
    assert result.exit_code == code, result.output
