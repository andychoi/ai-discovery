"""Keyless scan mode (roadmap item M0.10): `discover scan --no-llm`."""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.config import DiscoveryConfig
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("network access attempted in --no-llm mode")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def no_keys(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AWS_ACCESS_KEY_ID",
                "AWS_PROFILE", "DISCOVERY_LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)


def _db(out: Path, slug: str) -> sqlite3.Connection:
    conn = sqlite3.connect(out / f"output-{slug}" / f"discovery-{slug}.db")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.parametrize("provider", ["openai", "bedrock", "ollama"])
def test_no_llm_scan_completes_without_keys_or_network(provider, tmp_path, no_network, no_keys) -> None:
    from ai_discovery.pipeline import run_pipeline

    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    cfg = DiscoveryConfig(provider=provider, budget_limit_usd=0.0, max_concurrent=1)
    cfg.no_llm = True
    status = run_pipeline(repo=str(repo), branch="main", project_slug="k", output_dir=tmp_path / "out",
                          docs_root=tmp_path / "docs", config=cfg)
    assert status == "completed"

    conn = _db(tmp_path / "out", "k")
    count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
    assert conn.execute("SELECT status FROM scan_runs").fetchone()[0] == "completed"
    assert count("code_nodes") > 0 and count("call_edges") > 0 and count("domains") > 0
    # Screens mapped structurally (no LLM prose)
    assert count("screen_specs") > 0
    # Nothing LLM-derived, nothing billed
    assert count("node_summaries") == 0 and count("generated_docs") == 0
    assert conn.execute("SELECT COALESCE(SUM(est_usd), 0) FROM llm_costs").fetchone()[0] == 0
    # Deterministic artifacts still written
    assert (tmp_path / "out" / "output-k" / "interfaces.json").exists()
    assert list((tmp_path / "docs").rglob("*dependencies.md"))
    assert list((tmp_path / "docs").rglob("screens/*.md"))
    conn.close()


def test_no_llm_matches_structural_parts_of_full_scan(tmp_path, no_keys) -> None:
    """Keyless and LLM scans produce the same graph (LLM phases don't alter it)."""
    from tests.golden.test_golden import _scan, snapshot
    from ai_discovery.pipeline import run_pipeline

    full = snapshot("java-spring-jsp", *_scan("java-spring-jsp", tmp_path / "full"))

    repo = _build_repo(tmp_path / "kl" / "repo", GOLDEN_REPOS["java-spring-jsp"])
    cfg = DiscoveryConfig(provider="bedrock", budget_limit_usd=100.0, max_concurrent=1)
    cfg.no_llm = True
    run_pipeline(repo=str(repo), branch="main", project_slug="java-spring-jsp",
                 output_dir=tmp_path / "kl" / "out", docs_root=tmp_path / "kl" / "docs", config=cfg)
    from tests.golden.stub_llm import StubLLM
    keyless = snapshot("java-spring-jsp", repo, tmp_path / "kl" / "out", tmp_path / "kl" / "docs", StubLLM())

    assert keyless["call_edges"] == full["call_edges"]
    assert keyless["domains"] == full["domains"]
    assert keyless["screen_ids"] == full["screen_ids"]
    for table in ("code_nodes", "call_edges", "domains", "db_relationship"):
        assert keyless["table_counts"][table] == full["table_counts"][table], table


def test_cli_no_llm_flag_sets_config(tmp_path) -> None:
    from unittest.mock import patch

    from ai_discovery.cli import app

    seen = {}

    def fake_run_pipeline(**kwargs):
        seen["no_llm"] = kwargs["config"].no_llm
        return "completed"

    with patch("ai_discovery.pipeline.run_pipeline", side_effect=fake_run_pipeline):
        result = CliRunner().invoke(app, ["scan", str(tmp_path), "-p", "x", "-o", str(tmp_path / "o"), "--no-llm"])
    assert result.exit_code == 0, result.output
    assert seen["no_llm"] is True
