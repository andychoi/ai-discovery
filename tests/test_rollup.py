"""Tests for the Tier 3 doc rollup generator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_discovery.ai.rollup import (
    DOC_TYPES,
    RollupResult,
    _build_rollup_prompt,
    _parse_rollup,
    generate_all_docs,
    generate_domain_docs,
    persist_rollups,
)
from ai_discovery.ai.flow_analyzer import BusinessFlow
from ai_discovery.ai.llm_client import LLMResponse
from ai_discovery.db import get_conn, init_db
from ai_discovery.graph.models import CallEdge, CodeNode, Domain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(qualified_name: str, node_type: str = "method") -> CodeNode:
    return CodeNode(
        file_path="src/main.py",
        language="python",
        node_type=node_type,
        name=qualified_name.split(".")[-1],
        qualified_name=qualified_name,
        source_code="pass",
        line_start=1,
        line_end=5,
    )


def _make_domain(name: str = "orders") -> Domain:
    ep = _make_node("orders.OrderController.create", "endpoint")
    service = _make_node("orders.OrderService.process", "method")
    db_model = _make_node("orders.Order", "db_model")
    edge = CallEdge(
        caller="orders.OrderController.create",
        callee="orders.OrderService.process",
        edge_type="direct_call",
    )
    ext_edge = CallEdge(
        caller="orders.OrderService.process",
        callee="payments.PaymentGateway.charge",
        edge_type="http_call",
    )
    return Domain(
        name=name,
        nodes=[ep, service, db_model],
        internal_edges=[edge],
        external_edges=[ext_edge],
        entry_points=[ep],
        db_models=[db_model],
        tech_stack={"language": "python", "framework": "fastapi"},
    )


SAMPLE_SUMMARIES = {
    "orders.OrderController.create": {
        "purpose": "REST endpoint for order creation",
        "business_rules": "Validates stock before creating order",
        "io_summary": "POST /orders -> OrderResponse",
    },
    "orders.OrderService.process": {
        "purpose": "Core order processing logic",
        "tech_debt_signals": "Missing error handling for payment failures",
    },
}

SAMPLE_FLOWS = [
    BusinessFlow(
        name="Create Order",
        flow_type="user_flow",
        description="User creates a new order via the API.",
        involved_nodes=[
            "orders.OrderController.create",
            "orders.OrderService.process",
        ],
    ),
    BusinessFlow(
        name="Payment Integration",
        flow_type="integration_flow",
        description="Order service calls payment gateway.",
        involved_nodes=[
            "orders.OrderService.process",
            "payments.PaymentGateway.charge",
        ],
    ),
]


def _mock_llm_client(text: str = "# Generated Doc\n\nSome content.\n\nConfidence: 0.85") -> MagicMock:
    client = MagicMock()
    client.invoke.return_value = LLMResponse(
        text=text,
        tokens_in=1000,
        tokens_out=500,
        model="claude-opus",
        tier="tier3",
    )
    return client


# ---------------------------------------------------------------------------
# Tests: _build_rollup_prompt
# ---------------------------------------------------------------------------


def test_build_rollup_prompt_as_is():
    """Verify as-is prompt includes domain info, summaries, flows, and doc-type-specific instructions."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "as-is", SAMPLE_SUMMARIES, SAMPLE_FLOWS)

    # Domain info
    assert "Domain: orders" in prompt
    assert "Total nodes: 3" in prompt
    assert "fastapi" in prompt  # tech stack

    # Entry points with summaries
    assert "orders.OrderController.create" in prompt
    assert "REST endpoint for order creation" in prompt
    assert "Validates stock before creating order" in prompt

    # Node summaries
    assert "Core order processing logic" in prompt
    assert "Missing error handling for payment failures" in prompt

    # Business flows
    assert "Create Order" in prompt
    assert "user_flow" in prompt
    assert "Payment Integration" in prompt

    # DB models
    assert "orders.Order" in prompt

    # As-is specific instructions
    assert "Current-State" in prompt or "As-Is" in prompt
    assert "Architecture" in prompt
    assert "Technical Debt" in prompt
    assert "Dependencies" in prompt

    # Confidence instruction
    assert "Confidence" in prompt


def test_build_rollup_prompt_interface():
    """Verify interface prompt focuses on endpoints/APIs."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "interface", SAMPLE_SUMMARIES, SAMPLE_FLOWS)

    # Interface-specific instructions
    assert "Endpoints" in prompt or "APIs" in prompt
    assert "Request Schema" in prompt or "Request" in prompt
    assert "Response Schema" in prompt or "Response" in prompt
    assert "Error Handling" in prompt

    # Still has domain info
    assert "Domain: orders" in prompt
    assert "POST /orders -> OrderResponse" in prompt  # io_summary from summaries


def test_build_rollup_prompt_spec():
    """Verify spec prompt includes use cases and business rules."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "spec", SAMPLE_SUMMARIES, SAMPLE_FLOWS)

    assert "Use Cases" in prompt
    assert "Business Rules" in prompt
    assert "Functional Requirements" in prompt


def test_build_rollup_prompt_data_model():
    """Verify data-model prompt includes entity and relationship info."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "data-model", SAMPLE_SUMMARIES, SAMPLE_FLOWS)

    assert "Entity Definitions" in prompt or "Entity" in prompt
    assert "Relationships" in prompt
    assert "Constraints" in prompt


def test_build_rollup_prompt_external_edges():
    """Verify external edges appear in the prompt."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "as-is", {}, [])
    assert "External Dependencies" in prompt
    assert "payments.PaymentGateway.charge" in prompt


# ---------------------------------------------------------------------------
# Tests: _parse_rollup
# ---------------------------------------------------------------------------


def test_parse_rollup_with_confidence():
    """LLM response includes 'Confidence: 0.85', verify extracted."""
    text = "# As-Is Assessment\n\nSome content here.\n\nConfidence: 0.85"
    content, confidence = _parse_rollup(text, "orders", "as-is")

    assert confidence == 0.85
    assert "# As-Is Assessment" in content
    assert "Some content here." in content
    assert "Confidence:" not in content


def test_parse_rollup_default_confidence():
    """No confidence line -> default 0.7."""
    text = "# Generated Document\n\nContent without confidence score."
    content, confidence = _parse_rollup(text, "orders", "spec")

    assert confidence == 0.7
    assert "# Generated Document" in content
    assert "Content without confidence score." in content


def test_parse_rollup_confidence_edge_values():
    """Confidence values outside 0-1 range should be ignored."""
    text = "Content\n\nConfidence: 1.5"
    _, confidence = _parse_rollup(text, "orders", "as-is")
    assert confidence == 0.7  # out of range, use default


def test_parse_rollup_case_insensitive():
    """Confidence line can be lowercase."""
    text = "Content\n\nconfidence: 0.92"
    _, confidence = _parse_rollup(text, "orders", "as-is")
    assert confidence == 0.92


# ---------------------------------------------------------------------------
# Tests: generate_domain_docs
# ---------------------------------------------------------------------------


def test_generate_domain_docs_all_types():
    """Mock LLM, verify 4 RollupResults (one per doc type)."""
    domain = _make_domain()
    client = _mock_llm_client()

    results = generate_domain_docs(domain, SAMPLE_SUMMARIES, SAMPLE_FLOWS, client)

    assert len(results) == 4
    assert client.invoke.call_count == 4

    doc_types_generated = {r.doc_type for r in results}
    assert doc_types_generated == {"as-is", "spec", "interface", "data-model"}

    for result in results:
        assert isinstance(result, RollupResult)
        assert result.domain == "orders"
        assert result.confidence == 0.85
        assert result.tokens_in == 1000
        assert result.tokens_out == 500
        assert result.model == "claude-opus"
        assert "orders" in result.title

    # Verify tier3 is used
    for call in client.invoke.call_args_list:
        assert call[0][0] == "tier3"


def test_generate_domain_docs_custom_types():
    """Can generate a subset of doc types."""
    domain = _make_domain()
    client = _mock_llm_client()

    results = generate_domain_docs(
        domain, {}, [], client, doc_types=("as-is", "spec")
    )
    assert len(results) == 2
    assert client.invoke.call_count == 2


# ---------------------------------------------------------------------------
# Tests: generate_all_docs
# ---------------------------------------------------------------------------


def test_generate_all_docs_multiple_domains():
    """2 domains -> 8 results."""
    domain1 = _make_domain("orders")
    domain2 = _make_domain("payments")
    client = _mock_llm_client()
    progress_calls: list[tuple[int, int]] = []

    flows_by_domain = {
        "orders": SAMPLE_FLOWS,
        "payments": [],
    }

    results = generate_all_docs(
        [domain1, domain2],
        SAMPLE_SUMMARIES,
        flows_by_domain,
        client,
        on_progress=lambda c, t: progress_calls.append((c, t)),
    )

    assert len(results) == 8
    assert client.invoke.call_count == 8

    # Progress callbacks: 1..8 out of 8
    assert len(progress_calls) == 8
    assert progress_calls[0] == (1, 8)
    assert progress_calls[-1] == (8, 8)

    # Both domains represented
    domains_seen = {r.domain for r in results}
    assert domains_seen == {"orders", "payments"}


def test_generate_all_docs_empty_domains():
    """No domains -> empty results, no LLM calls."""
    client = _mock_llm_client()
    results = generate_all_docs([], {}, {}, client)
    assert results == []
    assert client.invoke.call_count == 0


# ---------------------------------------------------------------------------
# Tests: persist_rollups
# ---------------------------------------------------------------------------


def test_persist_rollups_writes_to_db(tmp_path: Path):
    """Create test DB with init_db, persist results, verify rows in generated_docs."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    # Create a scan_run so FK is satisfied
    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO scan_runs (repo_url, started_at, status) VALUES (?, ?, ?)",
        ("https://example.com/repo", "2026-01-01T00:00:00Z", "running"),
    )
    conn.commit()
    scan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    rollups = [
        RollupResult(
            domain="orders",
            doc_type="as-is",
            title="orders \u2014 As-Is Assessment",
            content_md="# As-Is\n\nContent here.",
            confidence=0.85,
            tokens_in=1000,
            tokens_out=500,
            model="claude-opus",
        ),
        RollupResult(
            domain="orders",
            doc_type="spec",
            title="orders \u2014 Functional Specification",
            content_md="# Spec\n\nSpec content.",
            confidence=0.78,
            tokens_in=900,
            tokens_out=450,
            model="claude-opus",
        ),
        RollupResult(
            domain="payments",
            doc_type="data-model",
            title="payments \u2014 Data Model",
            content_md="# Data Model\n\nEntities.",
            confidence=0.65,
            tokens_in=800,
            tokens_out=400,
            model="claude-opus",
        ),
    ]

    persist_rollups(rollups, scan_id, db_path, project_slug="myproject")

    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT domain, doc_type, doc_id, title, content_md, confidence, "
        "push_status FROM generated_docs WHERE scan_id = ? ORDER BY doc_type",
        (scan_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 3

    # Verify doc_ids are slugified
    doc_ids = {r["doc_id"] for r in rows}
    assert "myproject-orders-as-is" in doc_ids
    assert "myproject-orders-spec" in doc_ids
    assert "myproject-payments-data-model" in doc_ids

    # Verify content
    as_is_row = [r for r in rows if r["doc_type"] == "as-is"][0]
    assert as_is_row["domain"] == "orders"
    assert "# As-Is" in as_is_row["content_md"]
    assert as_is_row["confidence"] == 0.85
    assert as_is_row["push_status"] == "local"

    # Verify titles
    titles = {r["title"] for r in rows}
    assert "orders \u2014 As-Is Assessment" in titles


def test_persist_rollups_upsert(tmp_path: Path):
    """Persisting same domain+doc_type twice should upsert (UNIQUE constraint)."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO scan_runs (repo_url, started_at, status) VALUES (?, ?, ?)",
        ("https://example.com/repo", "2026-01-01T00:00:00Z", "running"),
    )
    conn.commit()
    scan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    rollup_v1 = [
        RollupResult(
            domain="orders",
            doc_type="as-is",
            title="orders \u2014 As-Is Assessment",
            content_md="Version 1",
            confidence=0.7,
            tokens_in=100,
            tokens_out=50,
            model="claude-opus",
        ),
    ]
    rollup_v2 = [
        RollupResult(
            domain="orders",
            doc_type="as-is",
            title="orders \u2014 As-Is Assessment",
            content_md="Version 2",
            confidence=0.9,
            tokens_in=200,
            tokens_out=100,
            model="claude-opus",
        ),
    ]

    persist_rollups(rollup_v1, scan_id, db_path, project_slug="proj")
    persist_rollups(rollup_v2, scan_id, db_path, project_slug="proj")

    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT content_md, confidence FROM generated_docs WHERE scan_id = ?",
        (scan_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 1  # upsert, not duplicate
    assert rows[0]["content_md"] == "Version 2"
    assert rows[0]["confidence"] == 0.9
