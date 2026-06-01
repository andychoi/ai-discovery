"""Tests for the Tier 2 business flow analyzer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_discovery.ai.flow_analyzer import (
    BusinessFlow,
    ScenarioFlow,
    ScenarioFlowInference,
    _build_flow_prompt,
    _parse_flows,
    analyze_all_domains,
    analyze_domain,
    load_scenario_flows,
    persist_flows,
    persist_scenario_flows,
)
from ai_discovery.ai.llm_client import LLMResponse
from ai_discovery.db import get_conn, init_db
from ai_discovery.graph.models import CallEdge, CodeNode, Domain, ExecutionEdge, ExecutionNode, Scenario


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
    response = LLMResponse(
        text=json.dumps(flows),
        tokens_in=500,
        tokens_out=200,
        model="claude-sonnet",
        tier=tier,
    )
    # flow_analyzer dispatches through invoke_with_advisor (post-91880bb).
    # Mock both so tests asserting on either surface keep working.
    client.invoke.return_value = response
    client.invoke_with_advisor.return_value = response
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
    client.invoke_with_advisor.assert_called_once()
    call_args = client.invoke_with_advisor.call_args
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
    prompt = client.invoke_with_advisor.call_args[0][1]
    assert "REST endpoint for order creation" in prompt
    assert "Validates stock before creating order" in prompt


def test_build_flow_prompt_includes_rag_source_when_provided():
    """P1-d: when source context is retrieved, it is embedded in the prompt with
    a grounding instruction so Tier-2 flows are anchored to real code."""
    domain = _make_domain()
    rag = "### orders.OrderService.process (src/orders/OrderService.java)\n```\nvoid process(){ charge(); }\n```"
    prompt = _build_flow_prompt(domain, {}, rag_context=rag)
    assert "void process(){ charge(); }" in prompt
    assert "Source Code Context" in prompt
    # a grounding directive must be present
    assert "ground" in prompt.lower() or "do not invent" in prompt.lower()


def test_build_flow_prompt_no_source_section_when_empty():
    """Backward compat: no source section when nothing was retrieved."""
    domain = _make_domain()
    prompt = _build_flow_prompt(domain, {}, rag_context="")
    assert "Source Code Context" not in prompt


def test_analyze_domain_grounds_with_rag(monkeypatch):
    """P1-d: with a db_path, analyze_domain retrieves source and the retrieved
    snippet reaches the Tier-2 prompt."""
    domain = _make_domain()
    client = _mock_llm_response(SAMPLE_FLOWS)

    def _fake_search(query, db_path, llm_client, top_k=5):
        return [{
            "qualified_name": "orders.OrderService.process",
            "file_path": "src/orders/OrderService.java",
            "chunk_text": "UNIQUE_RAG_MARKER void process(){}",
        }]

    monkeypatch.setattr("ai_discovery.rag.retriever.search", _fake_search)
    analyze_domain(domain, {}, client, db_path=Path("/tmp/x.db"))
    prompt = client.invoke_with_advisor.call_args[0][1]
    assert "UNIQUE_RAG_MARKER" in prompt


def test_analyze_domain_no_db_path_no_retrieval(monkeypatch):
    """db_path=None: no retrieval attempted, no crash, prompt has no source section."""
    domain = _make_domain()
    client = _mock_llm_response(SAMPLE_FLOWS)

    def _boom(*a, **k):
        raise AssertionError("search must not be called without db_path")

    monkeypatch.setattr("ai_discovery.rag.retriever.search", _boom)
    analyze_domain(domain, {}, client)  # no db_path
    prompt = client.invoke_with_advisor.call_args[0][1]
    assert "Source Code Context" not in prompt


def test_analyze_all_domains_stops_on_budget():
    """P1-e: Tier-2 stops starting new domains once the budget predicate trips."""
    domains = [_make_domain("orders"), _make_domain("payments"), _make_domain("billing")]
    client = _mock_llm_response(SAMPLE_FLOWS)
    # Trip the budget after the first domain's LLM call.
    results = analyze_all_domains(
        domains, {}, client,
        budget_exhausted=lambda: client.invoke_with_advisor.call_count >= 1,
    )
    assert client.invoke_with_advisor.call_count == 1
    assert len(results) == 1  # only the first domain analyzed; rest skipped


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
    assert client.invoke_with_advisor.call_count == 2
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


# ---------------------------------------------------------------------------
# ScenarioFlowInference tests
# ---------------------------------------------------------------------------


def _make_scenario(domain: str = "orders") -> Scenario:
    node = ExecutionNode(
        id="orders.OrderService.create",
        type="ENTRY",
        name="create",
        qualified_name="orders.OrderService.create",
    )
    return Scenario(
        scenario_id="scenario_create_1",
        name="Flow: create",
        entry_point="orders.OrderService.create",
        trigger_type="HTTP",
        nodes=[node],
        domain=domain,
    )


def _mock_inference_client(steps=None, ipo=None, interfaces=None) -> MagicMock:
    """Return an LLM client mock that returns different payloads per call."""
    steps_payload = json.dumps({"flow": steps or [
        {"step": 1, "name": "Validate", "type": "PROCESS", "description": "Check input"}
    ]})
    ipo_payload = json.dumps(ipo or {
        "input": ["order_id"], "process": ["validate"], "output": ["confirmation"], "data_flow": []
    })
    iface_payload = json.dumps({"interfaces": interfaces or [
        {"name": "PostgreSQL", "type": "DB", "operation": "INSERT orders"}
    ]})

    client = MagicMock()
    # ScenarioFlowInference dispatches through invoke_with_advisor (post-91880bb).
    client.invoke_with_advisor.side_effect = [
        LLMResponse(text=steps_payload, tokens_in=100, tokens_out=50, model="sonnet", tier="tier2"),
        LLMResponse(text=ipo_payload, tokens_in=80, tokens_out=40, model="sonnet", tier="tier2"),
        LLMResponse(text=iface_payload, tokens_in=60, tokens_out=30, model="sonnet", tier="tier2"),
    ]
    return client


def test_scenario_flow_inference_returns_scenario_flow():
    scenario = _make_scenario()
    client = _mock_inference_client()
    inference = ScenarioFlowInference(client)
    flow = inference.infer_flow(scenario, {})
    assert isinstance(flow, ScenarioFlow)
    assert flow.scenario_id == "scenario_create_1"


def test_scenario_flow_inference_propagates_domain():
    scenario = _make_scenario(domain="payments")
    client = _mock_inference_client()
    inference = ScenarioFlowInference(client)
    flow = inference.infer_flow(scenario, {})
    assert flow.domain == "payments"


def test_scenario_flow_inference_populates_steps():
    scenario = _make_scenario()
    client = _mock_inference_client()
    inference = ScenarioFlowInference(client)
    flow = inference.infer_flow(scenario, {})
    assert len(flow.steps) == 1
    assert flow.steps[0]["name"] == "Validate"


def test_scenario_flow_inference_populates_ipo():
    scenario = _make_scenario()
    client = _mock_inference_client()
    inference = ScenarioFlowInference(client)
    flow = inference.infer_flow(scenario, {})
    assert "order_id" in flow.input
    assert "validate" in flow.process
    assert "confirmation" in flow.output


def test_scenario_flow_inference_populates_interfaces():
    scenario = _make_scenario()
    client = _mock_inference_client()
    inference = ScenarioFlowInference(client)
    flow = inference.infer_flow(scenario, {})
    assert len(flow.external_interfaces) == 1
    assert flow.external_interfaces[0]["name"] == "PostgreSQL"


def test_scenario_flow_inference_includes_state_transition_in_prompt():
    from ai_discovery.graph.models import StateTransition
    scenario = _make_scenario()
    scenario.nodes[0].state_transition = StateTransition(
        entity="order", field="status", to_state="SUBMITTED",
        trigger_function="orders.OrderService.create",
    )
    client = _mock_inference_client()
    inference = ScenarioFlowInference(client)
    inference.infer_flow(scenario, {})
    # The first call (steps) should include the transition in the prompt
    first_prompt = client.invoke_with_advisor.call_args_list[0][0][1]
    assert "SUBMITTED" in first_prompt


def test_parse_json_response_handles_markdown_fences():
    inference = ScenarioFlowInference(MagicMock())
    text = '```json\n{"flow": [{"step": 1}]}\n```'
    result = inference._parse_json_response(text, "flow")
    assert result == [{"step": 1}]


def test_parse_json_response_handles_bare_json():
    inference = ScenarioFlowInference(MagicMock())
    text = '{"flow": [{"step": 1}]}'
    result = inference._parse_json_response(text, "flow")
    assert result == [{"step": 1}]


def test_parse_json_response_returns_empty_on_failure():
    inference = ScenarioFlowInference(MagicMock())
    result = inference._parse_json_response("not json at all", "flow")
    assert result == []


# ---------------------------------------------------------------------------
# persist_scenario_flows / load_scenario_flows
# ---------------------------------------------------------------------------


def test_persist_and_load_scenario_flows(tmp_path: Path):
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

    flows = [
        ScenarioFlow(
            scenario_id="scenario_create_1",
            domain="orders",
            steps=[{"step": 1, "name": "Validate", "type": "PROCESS", "description": ""}],
            input=["order_id"],
            process=["validate"],
            output=["confirmation"],
            data_flow=["order -> db"],
            external_interfaces=[{"name": "PostgreSQL", "type": "DB"}],
            confidence=0.9,
        )
    ]
    artifacts = {
        "scenario_create_1": {
            "mermaid": "sequenceDiagram\n    User->>System: Validate",
            "mermaid_flowchart": 'flowchart TD\n    start(("Start"))\n    step_0["Validate"]\n    end_node(("End"))\n    start --> step_0\n    step_0 --> end_node',
            "bpmn": "<bpmn/>",
            "ipo": "### IPO",
        }
    }

    persist_scenario_flows(flows, artifacts, scan_id, db_path)
    loaded_flows, loaded_artifacts = load_scenario_flows(scan_id, db_path)

    assert len(loaded_flows) == 1
    lf = loaded_flows[0]
    assert lf.scenario_id == "scenario_create_1"
    assert lf.domain == "orders"
    assert lf.input == ["order_id"]
    assert lf.steps[0]["name"] == "Validate"
    assert abs(lf.confidence - 0.9) < 1e-6

    la = loaded_artifacts["scenario_create_1"]
    assert "sequenceDiagram" in la["mermaid"]
    assert la["bpmn"] == "<bpmn/>"


# ---------------------------------------------------------------------------
# CRIT-2: source-fed prompts, source citations, and flow verification.
# ---------------------------------------------------------------------------

def _scenario_with_source() -> Scenario:
    node = ExecutionNode(
        id="orders.OrderService.create", type="ENTRY", name="create",
        qualified_name="orders.OrderService.create",
        file_path="src/orders/service.py", line_number=42,
    )
    return Scenario(
        scenario_id="s1", name="Flow: create", entry_point="orders.OrderService.create",
        trigger_type="HTTP", nodes=[node], domain="orders",
    )


def test_steps_prompt_includes_source_locations():
    scenario = _scenario_with_source()
    inference = ScenarioFlowInference(MagicMock())
    prompt = inference._build_steps_prompt(scenario, {})
    assert "src/orders/service.py:42" in prompt  # CRIT-2: source-fed prompt


def test_infer_flow_populates_source_refs():
    scenario = _scenario_with_source()
    flow = ScenarioFlowInference(_mock_inference_client()).infer_flow(scenario, {})
    assert any(r["source"] == "src/orders/service.py:42" for r in flow.source_refs)


def test_verify_flow_sets_verified_confidence(monkeypatch):
    import ai_discovery.ai.self_review as sr
    import ai_discovery.ai.rollup as rollup
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: ["claim"])
    monkeypatch.setattr(sr, "get_review_summary", lambda claims: {"verified": 1, "unverified": 0, "contradicted": 0, "total": 1})
    monkeypatch.setattr(rollup, "blend_confidence", lambda n, s: 0.82)

    flow = ScenarioFlow(scenario_id="s1", steps=[{"name": "Save", "description": "persist order"}])
    inference = ScenarioFlowInference(MagicMock())
    inference.verify_flow(flow, db_path=None)
    assert flow.verified is True
    assert flow.confidence == 0.82


def test_verify_flow_failure_leaves_low_unverified(monkeypatch):
    import ai_discovery.ai.self_review as sr
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no rag")))
    flow = ScenarioFlow(scenario_id="s1", steps=[{"name": "Save", "description": "x"}])
    ScenarioFlowInference(MagicMock()).verify_flow(flow, db_path=None)
    assert flow.verified is False
    assert flow.confidence == 0.4


def test_verify_flow_no_evidence_is_not_source_verified(monkeypatch):
    """With an empty/unmatched RAG index every claim is unverified — verify_flow
    must NOT mark the flow source-verified (it would overstate; real-scan finding)."""
    import ai_discovery.ai.self_review as sr
    import ai_discovery.ai.rollup as rollup
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: ["c"])
    monkeypatch.setattr(sr, "get_review_summary", lambda c: {"verified": 0, "unverified": 3, "contradicted": 0, "total": 3})
    monkeypatch.setattr(rollup, "blend_confidence", lambda n, s: 0.5)
    flow = ScenarioFlow(scenario_id="s1", steps=[{"name": "Save", "description": "x"}])
    ScenarioFlowInference(MagicMock()).verify_flow(flow, db_path=None)
    assert flow.verified is False     # no confirmed claims → not source-verified
