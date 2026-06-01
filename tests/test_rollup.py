"""Tests for the Tier 3 doc rollup generator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_discovery.ai.rollup import (
    DOC_TYPES,
    RollupResult,
    RollupTotalFailureError,
    UNVERIFIABLE_CONFIDENCE,
    UNREVIEWED_WITH_FACTS_CONFIDENCE,
    _build_rollup_prompt,
    _parse_rollup,
    generate_all_docs,
    generate_domain_docs,
    persist_rollups,
    unreviewed_confidence,
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
    response = LLMResponse(
        text=text,
        tokens_in=1000,
        tokens_out=500,
        model="claude-opus",
        tier="tier3",
    )
    # rollup dispatches through invoke_with_advisor (post-91880bb).
    client.invoke.return_value = response
    client.invoke_with_advisor.return_value = response
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
    assert "Cross-Domain & Outbound Calls" in prompt

    # Confidence instruction
    assert "Confidence" in prompt


def test_build_rollup_prompt_as_is_detail():
    """Verify as-is-detail prompt merges functional spec + API contracts (old 'spec' + 'interface')."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "as-is-detail", SAMPLE_SUMMARIES, SAMPLE_FLOWS)

    # Functional-spec half
    assert "Use Cases" in prompt
    assert "Business Rules" in prompt
    # API-contracts half
    assert "Endpoints" in prompt or "APIs" in prompt
    assert "Request" in prompt
    assert "Response" in prompt

    # Domain info preserved
    assert "Domain: orders" in prompt
    assert "POST /orders -> OrderResponse" in prompt  # io_summary from summaries


def test_build_rollup_prompt_as_is_schema():
    """Verify as-is-schema prompt covers entities, relationships, constraints (old 'data-model')."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "as-is-schema", SAMPLE_SUMMARIES, SAMPLE_FLOWS)

    assert "Entity Definitions" in prompt or "Entity" in prompt
    assert "Relationships" in prompt
    assert "Constraints" in prompt


def test_build_rollup_prompt_external_edges():
    """Verify external edges appear in the prompt."""
    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "as-is", {}, [])
    # HIGH-8: relabeled from "External Dependencies" — these are cross-domain /
    # outbound calls, not necessarily external systems.
    assert "Cross-Domain & Outbound Calls" in prompt
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


def test_unreviewed_confidence_is_deterministic():
    """P1-b: unreviewed confidence depends only on AST-fact rows, never the LLM."""
    assert unreviewed_confidence(3) == UNREVIEWED_WITH_FACTS_CONFIDENCE
    assert unreviewed_confidence(1) == UNREVIEWED_WITH_FACTS_CONFIDENCE
    assert unreviewed_confidence(0) == UNVERIFIABLE_CONFIDENCE
    assert unreviewed_confidence(None) == UNVERIFIABLE_CONFIDENCE
    # Capped below 1.0 — unreviewed prose is never "certain".
    assert UNREVIEWED_WITH_FACTS_CONFIDENCE < 1.0


def test_generate_domain_docs_all_types():
    """Mock LLM, verify one RollupResult per doc type in DOC_TYPES."""
    domain = _make_domain()
    client = _mock_llm_client()

    results = generate_domain_docs(domain, SAMPLE_SUMMARIES, SAMPLE_FLOWS, client)

    assert len(results) == len(DOC_TYPES)
    assert client.invoke_with_advisor.call_count == len(DOC_TYPES)

    doc_types_generated = {r.doc_type for r in results}
    assert doc_types_generated == set(DOC_TYPES)

    for result in results:
        assert isinstance(result, RollupResult)
        assert result.domain == "orders"
        # P1-b: confidence is deterministic (AST-fact based), never the LLM's
        # self-asserted 0.85 from the mock response.
        assert result.confidence in (0.3, 0.6)
        assert result.tokens_in == 1000
        assert result.tokens_out == 500
        assert result.model == "claude-opus"
        assert "orders" in result.title

    # Verify tier3 is used
    for call in client.invoke_with_advisor.call_args_list:
        assert call[0][0] == "tier3"


def test_generate_domain_docs_custom_types():
    """Can generate a subset of doc types."""
    domain = _make_domain()
    client = _mock_llm_client()

    results = generate_domain_docs(
        domain, {}, [], client, doc_types=("as-is", "as-is-detail")
    )
    assert len(results) == 2
    assert client.invoke_with_advisor.call_count == 2


# ---------------------------------------------------------------------------
# Tests: generate_all_docs
# ---------------------------------------------------------------------------


def test_generate_all_docs_multiple_domains():
    """2 domains -> 2 * len(DOC_TYPES) results."""
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

    expected_count = 2 * len(DOC_TYPES)
    assert len(results) == expected_count
    assert client.invoke_with_advisor.call_count == expected_count

    # Progress callbacks: 1..N out of N
    assert len(progress_calls) == expected_count
    assert progress_calls[0] == (1, expected_count)
    assert progress_calls[-1] == (expected_count, expected_count)

    # Both domains represented
    domains_seen = {r.domain for r in results}
    assert domains_seen == {"orders", "payments"}


def test_generate_all_docs_empty_domains():
    """No domains -> empty results, no LLM calls. Must NOT raise (nothing attempted)."""
    client = _mock_llm_client()
    results = generate_all_docs([], {}, {}, client)
    assert results == []
    assert client.invoke_with_advisor.call_count == 0


def test_generate_all_docs_all_fail_raises():
    """Every (domain, doc_type) rollup fails -> fatal RollupTotalFailureError.

    Regression guard for the silent-success bug: previously generate_all_docs
    swallowed every failure and returned [], so phase 14 was recorded complete
    and the scan exited 0 with missing ASIS/ASD/ASSC docs. The classic trigger
    is an invalid tier3 model id ('model identifier is invalid').
    """
    client = _mock_llm_client()
    client.invoke_with_advisor.side_effect = RuntimeError(
        "ValidationException: The provided model identifier is invalid"
    )

    with pytest.raises(RollupTotalFailureError):
        generate_all_docs([_make_domain("orders")], SAMPLE_SUMMARIES, {}, client)


def test_generate_all_docs_partial_failure_does_not_raise():
    """At least one rollup succeeds -> return the successes, do NOT raise.

    Partial degradation is acceptable; only total failure is fatal.
    """
    client = _mock_llm_client()
    ok = LLMResponse(
        text="# Doc\n\nContent.\n\nConfidence: 0.8",
        tokens_in=10, tokens_out=10, model="claude-opus", tier="tier3",
    )

    def _route(tier, prompt, *args, **kwargs):
        # Fail every 'payments' doc, succeed for 'orders'.
        if "Domain: payments" in prompt:
            raise RuntimeError("model identifier is invalid")
        return ok

    client.invoke_with_advisor.side_effect = _route

    results = generate_all_docs(
        [_make_domain("orders"), _make_domain("payments")],
        SAMPLE_SUMMARIES,
        {},
        client,
    )

    # Only the 'orders' docs survive; no exception raised.
    assert results
    assert {r.domain for r in results} == {"orders"}
    assert len(results) == len(DOC_TYPES)


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
