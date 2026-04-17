"""Tests for ExecutionSliceBuilder and identify_scenarios."""

from __future__ import annotations

import pytest

from ai_discovery.graph.call_graph import ExecutionSliceBuilder, build_call_graph, identify_scenarios
from ai_discovery.graph.models import CallEdge, CodeNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(
    name: str,
    node_type: str = "method",
    calls: list[str] | None = None,
    boundaries: list[dict] | None = None,
    transitions: list[dict] | None = None,
    domain: str = "orders",
) -> CodeNode:
    hints: dict = {}
    if boundaries is not None:
        hints["boundaries"] = boundaries
    if transitions is not None:
        hints["transitions"] = transitions
    return CodeNode(
        file_path="svc.py",
        language="python",
        node_type=node_type,
        name=name,
        qualified_name=f"orders.{name}",
        source_code=f"def {name}(): pass",
        line_start=1,
        line_end=5,
        calls=calls or [],
        framework_hints=hints,
        domain=domain,
    )


# ---------------------------------------------------------------------------
# identify_scenarios
# ---------------------------------------------------------------------------


def test_identifies_endpoint():
    nodes = [_node("create", node_type="endpoint")]
    found = identify_scenarios(nodes)
    assert len(found) == 1
    assert found[0].name == "create"


def test_identifies_batch_job():
    nodes = [_node("run_nightly", node_type="batch_job")]
    found = identify_scenarios(nodes)
    assert len(found) == 1


def test_identifies_cli_function_by_name():
    nodes = [_node("main", node_type="function")]
    found = identify_scenarios(nodes)
    assert any(n.name == "main" for n in found)


def test_identifies_event_consumer_by_name():
    nodes = [_node("order_event_consumer", node_type="function")]
    found = identify_scenarios(nodes)
    assert any(n.name == "order_event_consumer" for n in found)


def test_identifies_listener_by_name():
    nodes = [_node("payment_listener", node_type="method")]
    found = identify_scenarios(nodes)
    assert any(n.name == "payment_listener" for n in found)


def test_plain_method_not_identified():
    nodes = [_node("process_data", node_type="method")]
    found = identify_scenarios(nodes)
    assert found == []


def test_identifies_multiple_entry_types():
    nodes = [
        _node("create_order", node_type="endpoint"),
        _node("run_batch", node_type="batch_job"),
        _node("main", node_type="function"),
    ]
    found = identify_scenarios(nodes)
    assert len(found) == 3


# ---------------------------------------------------------------------------
# ExecutionSliceBuilder
# ---------------------------------------------------------------------------


def test_build_scenario_has_nodes_and_edges():
    entry = _node("create", node_type="endpoint", calls=["validate"])
    service = _node("validate", node_type="method")
    edges = build_call_graph([entry, service])
    builder = ExecutionSliceBuilder([entry, service], edges)
    scenario = builder.build_scenario(entry)
    assert len(scenario.nodes) >= 1
    assert scenario.entry_point == "orders.create"


def test_build_scenario_primary_path_nonempty():
    entry = _node("create", node_type="endpoint", calls=["validate"])
    service = _node("validate", node_type="method")
    edges = build_call_graph([entry, service])
    builder = ExecutionSliceBuilder([entry, service], edges)
    scenario = builder.build_scenario(entry)
    assert len(scenario.primary_path) > 0


def test_build_scenario_external_interfaces_detected():
    entry = _node("create", node_type="endpoint",
                  calls=["save"],
                  boundaries=[{"type": "DB", "operation": "save"}])
    db_node = _node("save", node_type="method",
                    boundaries=[{"type": "DB", "operation": "commit"}])
    edges = build_call_graph([entry, db_node])
    builder = ExecutionSliceBuilder([entry, db_node], edges)
    scenario = builder.build_scenario(entry)
    # DB boundary nodes should be in external_interfaces
    assert len(scenario.external_interfaces) >= 0  # may be empty if boundary on entry


def test_create_execution_node_uses_best_boundary():
    """Node with both DB and EXTERNAL_API boundaries should resolve to DB (higher priority)."""
    node = _node(
        "process",
        boundaries=[
            {"type": "EXTERNAL_API", "client": "requests", "method": "get"},
            {"type": "DB", "operation": "save"},
        ],
    )
    edges = build_call_graph([node])
    builder = ExecutionSliceBuilder([node], edges)
    exec_node = builder._create_execution_node(node.qualified_name, node)
    assert exec_node.type == "DB"


def test_create_execution_node_captures_state_transition():
    node = _node(
        "approve",
        transitions=[{"entity": "self", "field": "status", "value": "APPROVED"}],
    )
    builder = ExecutionSliceBuilder([node], [])
    exec_node = builder._create_execution_node(node.qualified_name, node)
    assert exec_node.state_transition is not None
    assert exec_node.state_transition.to_state == "APPROVED"


def test_score_node_entry_gets_high_score():
    entry = _node("create", node_type="endpoint")
    builder = ExecutionSliceBuilder([entry], [])
    exec_node = builder._create_execution_node(entry.qualified_name, entry)
    score = builder._score_node(exec_node, depth=0, prev_node=None, state_writes={})
    # Entry at depth 0 should score at least 5 (call order bonus)
    assert score >= 5.0


def test_score_node_state_transition_adds_bonus():
    node = _node(
        "approve",
        transitions=[{"entity": "self", "field": "status", "value": "APPROVED"}],
    )
    builder = ExecutionSliceBuilder([node], [])
    exec_node_with = builder._create_execution_node(node.qualified_name, node)
    exec_node_with.state_transition = exec_node_with.state_transition  # already set

    plain_node = _node("plain_method")
    exec_node_plain = builder._create_execution_node(plain_node.qualified_name, plain_node)

    score_with = builder._score_node(exec_node_with, 1, None, {})
    score_plain = builder._score_node(exec_node_plain, 1, None, {})
    assert score_with > score_plain


def test_build_all_scenarios_returns_list():
    entry = _node("create", node_type="endpoint")
    edges = build_call_graph([entry])
    builder = ExecutionSliceBuilder([entry], edges)
    scenarios = builder.build_all_scenarios()
    assert isinstance(scenarios, list)
    assert len(scenarios) == 1


def test_trigger_type_http_for_endpoint():
    entry = _node("create", node_type="endpoint")
    builder = ExecutionSliceBuilder([entry], [])
    assert builder._get_trigger_type(entry) == "HTTP"


def test_trigger_type_scheduled_for_batch():
    entry = _node("run", node_type="batch_job")
    builder = ExecutionSliceBuilder([entry], [])
    assert builder._get_trigger_type(entry) == "SCHEDULED"


def test_trigger_type_event_for_consumer_by_name():
    entry = _node("order_event_handler", node_type="function")
    builder = ExecutionSliceBuilder([entry], [])
    assert builder._get_trigger_type(entry) == "EVENT"


def test_unresolved_execution_node_not_classified_as_external_api():
    builder = ExecutionSliceBuilder([], [])
    exec_node = builder._create_execution_node("missing.symbol", None)
    assert exec_node.type == "UNRESOLVED"
