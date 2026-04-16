"""Tests for the Tier 2 business flow analyzer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ai.flow_analyzer import (
    BusinessFlow,
    _build_flow_prompt,
    _parse_flows,
    analyze_all_domains,
    analyze_domain,
    persist_flows,
)
from app.ai.llm_client import LLMResponse
from app.db import get_conn, init_db
from app.graph.models import CallEdge, CodeNode, Domain


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
    batch = _make_node("orders.BatchProcessor.run", "batch_job")
    service = _make_node("orders.OrderService.process", "method")
    db_model = _make_node("orders.Order", "db_model")
    edge = CallEdge(
        caller="orders.OrderController.create",
        callee="orders.OrderService.process",
        edge_type="direct_call",
    )
    return Domain(
        name=name,
        nodes=[ep, batch, service, db_model],
        internal_edges=[edge],
        external_edges=[],
        entry_points=[ep, batch],
        db_models=[db_model],
    )


def _mock_llm_response(flows: list[dict], tier: str = "tier2") -> MagicMock:
    client = MagicMock()
    client.invoke.return_value = LLMResponse(
        text=json.dumps(flows),
        tokens_in=500,
        tokens_out=200,
        model="claude-sonnet",
        tier=tier,
    )
    return client


SAMPLE_FLOWS = [
    {
        "name": "Create Order",
        "flow_type": "user_flow",
        "description": "User creates a new order via the API.",
        "involved_nodes": [
            "orders.OrderController.create",
            "orders.OrderService.process",
        ],
    },
    {
        "name": "Batch Processing",
        "flow_type": "batch_flow",
        "description": "Scheduled batch job processes pending orders.",
        "involved_nodes": ["orders.BatchProcessor.run"],
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_flow_prompt_includes_domain_info():
    domain = _make_domain()
    prompt = _build_flow_prompt(domain, {})
    assert "orders" in prompt
    assert "orders.OrderController.create" in prompt
    assert "orders.OrderService.process" in prompt
    # Call edge present
    assert "orders.OrderController.create -> orders.OrderService.process" in prompt
    # DB model present
    assert "orders.Order" in prompt


def test_parse_flows_valid_json():
    text = json.dumps(SAMPLE_FLOWS)
    flows = _parse_flows(text)
    assert len(flows) == 2
    assert isinstance(flows[0], BusinessFlow)
    assert flows[0].name == "Create Order"
    assert flows[0].flow_type == "user_flow"
    assert "orders.OrderController.create" in flows[0].involved_nodes
    assert flows[1].flow_type == "batch_flow"


def test_parse_flows_non_json():
    flows = _parse_flows("This is not JSON at all.")
    assert flows == []


def test_analyze_domain_calls_tier2():
    domain = _make_domain()
    client = _mock_llm_response(SAMPLE_FLOWS)
    flows = analyze_domain(domain, {}, client)
    client.invoke.assert_called_once()
    call_args = client.invoke.call_args
    assert call_args[0][0] == "tier2"  # first positional arg is tier
    assert len(flows) == 2


def test_analyze_domain_with_summaries():
    domain = _make_domain()
    summaries = {
        "orders.OrderController.create": {
            "purpose": "REST endpoint for order creation",
            "business_rules": "Validates stock before creating order",
        },
    }
    client = _mock_llm_response(SAMPLE_FLOWS)
    analyze_domain(domain, summaries, client)
    prompt = client.invoke.call_args[0][1]
    assert "REST endpoint for order creation" in prompt
    assert "Validates stock before creating order" in prompt


def test_analyze_all_domains_processes_each():
    domain1 = _make_domain("orders")
    domain2 = _make_domain("payments")
    client = _mock_llm_response(SAMPLE_FLOWS)
    progress_calls: list[tuple[int, int]] = []

    results = analyze_all_domains(
        [domain1, domain2],
        {},
        client,
        on_progress=lambda c, t: progress_calls.append((c, t)),
    )
    assert "orders" in results
    assert "payments" in results
    assert client.invoke.call_count == 2
    assert progress_calls == [(1, 2), (2, 2)]


def test_analyze_domain_identifies_flow_types():
    domain = _make_domain()
    client = _mock_llm_response(SAMPLE_FLOWS)
    flows = analyze_domain(domain, {}, client)
    flow_types = {f.flow_type for f in flows}
    assert "user_flow" in flow_types
    assert "batch_flow" in flow_types


def test_persist_flows_writes_to_db(tmp_path: Path):
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

    flows_by_domain = {
        "orders": [
            BusinessFlow(
                name="Create Order",
                flow_type="user_flow",
                description="Creates an order",
                involved_nodes=["orders.OrderController.create"],
            ),
            BusinessFlow(
                name="Batch Processing",
                flow_type="batch_flow",
                description="Processes orders",
                involved_nodes=["orders.BatchProcessor.run"],
            ),
        ],
        "payments": [
            BusinessFlow(
                name="Process Payment",
                flow_type="integration_flow",
                description="Processes payment via gateway",
                involved_nodes=["payments.PaymentService.charge"],
            ),
        ],
    }

    persist_flows(flows_by_domain, scan_id, db_path, model_used="claude-sonnet")

    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT domain, flow_type, name, description, node_ids, model_used "
        "FROM business_flows WHERE scan_id = ? ORDER BY name",
        (scan_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 3
    names = {r["name"] for r in rows}
    assert names == {"Batch Processing", "Create Order", "Process Payment"}

    # Verify node_ids is valid JSON
    for row in rows:
        node_ids = json.loads(row["node_ids"])
        assert isinstance(node_ids, list)
    # Verify model_used
    assert all(r["model_used"] == "claude-sonnet" for r in rows)
