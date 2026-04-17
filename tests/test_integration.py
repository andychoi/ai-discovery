"""End-to-end integration test for the discovery pipeline with mocked LLM."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_discovery.ai.llm_client import LLMResponse
from ai_discovery.config import DiscoveryConfig


# ---------------------------------------------------------------------------
# Test repo content
# ---------------------------------------------------------------------------

_PYTHON_FILE = '''\
from fastapi import FastAPI, HTTPException

app = FastAPI()


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    """Retrieve an order by ID."""
    if order_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid order ID")
    return {"order_id": order_id, "status": "pending"}
'''

_CSHARP_FILE = '''\
using System;

namespace Services
{
    public class OrderService
    {
        public string ProcessOrder(int orderId)
        {
            if (orderId <= 0)
                throw new ArgumentException("Invalid order ID");
            return "processed";
        }
    }
}
'''


# ---------------------------------------------------------------------------
# Mock LLM factory
# ---------------------------------------------------------------------------

def _make_mock_llm():
    """Build a MagicMock that stands in for LLMClient.

    invoke() returns tier-appropriate JSON so every downstream parser succeeds.
    get_embedding() returns a deterministic 256-dim vector.
    Cost helpers return zero / no-op.
    """
    mock = MagicMock()

    def invoke_side_effect(tier, prompt, max_tokens=4096):
        if tier == "tier1":
            # Summarizer expects JSON with 4 keys.
            # Self-review extract_claims also uses tier1 — return a JSON array
            # when the prompt contains "extract" (claim extraction), otherwise
            # return a summary dict.
            if "extract" in prompt.lower() and "claim" in prompt.lower():
                text = json.dumps([
                    "The OrderService class handles order processing",
                    "The /orders endpoint accepts GET requests",
                ])
            elif "verif" in prompt.lower():
                text = "verified"
            else:
                text = json.dumps({
                    "purpose": "Handles business logic",
                    "business_rules": "Validates input",
                    "io_summary": "Reads from DB",
                    "tech_debt_signals": "None detected",
                })
        elif tier == "tier2":
            # Flow analyzer expects a JSON array of flow objects.
            text = json.dumps([{
                "name": "Order Processing",
                "flow_type": "user_flow",
                "description": "Handles order creation and retrieval",
                "involved_nodes": [],
            }])
        elif tier == "tier3":
            # Rollup expects markdown ending with a Confidence line.
            text = (
                "# Current State\n\n"
                "This is a generated document.\n\n"
                "Confidence: 0.8"
            )
        else:
            text = "mock response"

        return LLMResponse(
            text=text,
            tokens_in=100,
            tokens_out=50,
            model=f"mock-{tier}",
            tier=tier,
        )

    mock.invoke.side_effect = invoke_side_effect
    mock.get_embedding.return_value = [0.1] * 256
    mock.total_cost_usd.return_value = 0.0
    mock.persist_costs = MagicMock()
    mock.get_costs.return_value = {}

    return mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_default_branch() -> str:
    """Return the default branch name configured in git (usually main or master)."""
    result = subprocess.run(
        ["git", "config", "--global", "init.defaultBranch"],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "master"  # git default when not configured


def _init_test_repo(tmp_path: Path, branch: str) -> Path:
    """Create a tiny multi-language git repo in *tmp_path* and return its path."""
    repo = tmp_path / "test-repo"
    repo.mkdir()

    # Python file
    api_dir = repo / "api"
    api_dir.mkdir()
    (api_dir / "main.py").write_text(_PYTHON_FILE, encoding="utf-8")

    # C# file
    svc_dir = repo / "Services"
    svc_dir.mkdir()
    (svc_dir / "OrderService.cs").write_text(_CSHARP_FILE, encoding="utf-8")

    # Initialise git repo with one commit
    subprocess.run(
        ["git", "init", "-b", branch],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


def _get_conn(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

def test_full_pipeline(tmp_path):
    """Run the entire pipeline on a small test repo with a mocked LLM and
    verify that all expected DB tables are populated and markdown files are
    written to the output directory.
    """
    branch = "test-branch"
    repo_path = _init_test_repo(tmp_path, branch)
    output_dir = tmp_path / "output"

    config = DiscoveryConfig(
        provider="bedrock",
        budget_limit_usd=100.0,
        max_concurrent=2,
    )

    mock_llm = _make_mock_llm()

    # Patch LLMClient at the source module; pipeline imports it lazily
    # inside run_pipeline so patching the class there makes the
    # constructor return our mock.
    # Also patch:
    # - embed_chunks: avoid needing sqlite-vec extension for RAG embedding
    # - rag.retriever.search: avoid sqlite-vec in summarizer threads
    # - review_document: avoid sqlite "database is locked" contention
    #   (the pipeline holds an open write txn while calling persist_claims
    #   which opens a second connection -- a known limitation)
    from ai_discovery.ai.self_review import ReviewClaim

    with (
        patch("app.ai.llm_client.LLMClient", return_value=mock_llm),
        patch(
            "app.rag.embedder.embed_chunks",
            return_value={"embedded": 0, "dim": 256},
        ),
        patch("app.rag.retriever.search", return_value=[]),
        patch(
            "app.ai.self_review.review_document",
            return_value=[
                ReviewClaim(claim_text="test claim", status="verified"),
            ],
        ),
        patch("app.ai.self_review.persist_claims"),
    ):
        from ai_discovery.pipeline import run_pipeline

        run_pipeline(
            repo=str(repo_path),
            branch=branch,
            project_slug="test-proj",
            output_dir=output_dir,
            config=config,
        )

    # ── Verify DB ──────────────────────────────────────────────────────
    db_path = output_dir / "test-proj" / "discovery-test-proj.db"
    assert db_path.exists(), f"{db_path} should be created"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # scan_runs — should have a completed entry
    rows = conn.execute("SELECT * FROM scan_runs").fetchall()
    assert len(rows) >= 1
    assert rows[-1]["status"] == "completed"

    # code_nodes — should have entries for both Python and C# files
    nodes = conn.execute("SELECT * FROM code_nodes").fetchall()
    assert len(nodes) >= 2, f"Expected >=2 code nodes, got {len(nodes)}"

    languages = {r["language"] for r in nodes}
    assert "python" in languages, "Should detect Python nodes"
    assert "csharp" in languages, "Should detect C# nodes"

    # call_edges — should have at least one edge
    edges = conn.execute("SELECT * FROM call_edges").fetchall()
    assert len(edges) >= 1, f"Expected >=1 call edge, got {len(edges)}"

    # domains — should have at least one domain
    domains = conn.execute("SELECT * FROM domains").fetchall()
    assert len(domains) >= 1, f"Expected >=1 domain, got {len(domains)}"

    # node_summaries — Tier 1 should produce entries
    summaries = conn.execute("SELECT * FROM node_summaries").fetchall()
    assert len(summaries) >= 1, f"Expected >=1 summary, got {len(summaries)}"

    # business_flows — Tier 2 should produce entries
    flows = conn.execute("SELECT * FROM business_flows").fetchall()
    assert len(flows) >= 1, f"Expected >=1 flow, got {len(flows)}"

    # generated_docs — Tier 3 should produce entries
    docs = conn.execute("SELECT * FROM generated_docs").fetchall()
    assert len(docs) >= 1, f"Expected >=1 generated doc, got {len(docs)}"

    conn.close()

    # ── Verify markdown output ─────────────────────────────────────────
    docs_dir = output_dir / "test-proj" / "docs"
    assert docs_dir.exists(), f"{docs_dir} should exist"

    md_files = list(docs_dir.rglob("*.md"))
    assert len(md_files) >= 1, f"Expected >=1 markdown file, got {len(md_files)}"

    # Check frontmatter of the first markdown file
    sample = md_files[0].read_text(encoding="utf-8")
    assert sample.startswith("---"), "Markdown should start with YAML frontmatter"
    assert "doc_id:" in sample
    assert "discovery_confidence:" in sample
