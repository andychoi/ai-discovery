"""Tests for Phase 1.3: entry-point linkage.

Verifies that `link_entry_points` populates `StateTransition.entry_points`
via backward BFS through the call graph, mapping node_type to kind, keeping
shortest-hop entries, and using min-edge confidence along the path.
"""

from __future__ import annotations

from ai_discovery.graph.entry_point_linker import link_entry_points
from ai_discovery.graph.models import CallEdge, CodeNode, StateTransition


def _node(qn: str, node_type: str = "method", name: str | None = None) -> CodeNode:
    return CodeNode(
        file_path=f"{qn.replace('.', '/')}.py",
        language="python",
        node_type=node_type,
        name=name or qn.split(".")[-1],
        qualified_name=qn,
        source_code="",
        line_start=1,
        line_end=2,
    )


def _edge(caller: str, callee: str, conf: float = 1.0) -> CallEdge:
    return CallEdge(caller=caller, callee=callee, edge_type="direct_call", confidence=conf)


def _transition(trigger: str) -> StateTransition:
    return StateTransition(entity="Order", field="status", to_state="approved", trigger_function=trigger)


# ---------------------------------------------------------------------------
# Basic reachability
# ---------------------------------------------------------------------------


def test_single_api_entry_one_hop():
    """Endpoint directly calls the service method that performs the transition."""
    nodes = [
        _node("svc.OrderService.approve"),
        _node("ctrl.OrdersController.approve", node_type="endpoint"),
    ]
    edges = [_edge("ctrl.OrdersController.approve", "svc.OrderService.approve")]
    t = _transition("svc.OrderService.approve")
    link_entry_points([t], nodes, edges)
    assert len(t.entry_points) == 1
    e = t.entry_points[0]
    assert e["kind"] == "API"
    assert e["qualified_name"] == "ctrl.OrdersController.approve"
    assert e["hop_count"] == 1
    assert e["confidence"] == 1.0


def test_trigger_function_is_itself_an_entry():
    """When the trigger function is directly an endpoint, hop_count = 0."""
    nodes = [_node("ctrl.OrdersController.approve", node_type="endpoint")]
    t = _transition("ctrl.OrdersController.approve")
    link_entry_points([t], nodes, [])
    assert len(t.entry_points) == 1
    assert t.entry_points[0]["hop_count"] == 0
    assert t.entry_points[0]["kind"] == "API"


def test_no_entry_reachable():
    """Transition whose trigger has no entry-typed ancestors stays empty."""
    nodes = [
        _node("svc.OrderService.approve"),
        _node("svc.helper"),
    ]
    edges = [_edge("svc.helper", "svc.OrderService.approve")]
    t = _transition("svc.OrderService.approve")
    link_entry_points([t], nodes, edges)
    assert t.entry_points == []


# ---------------------------------------------------------------------------
# Kind mapping
# ---------------------------------------------------------------------------


def test_kind_mapping_covers_all_entry_types():
    nodes = [
        _node("target"),
        _node("a", node_type="endpoint"),
        _node("b", node_type="ui_component"),
        _node("c", node_type="batch_job"),
        _node("d", node_type="event_consumer"),
        _node("e", node_type="cli_command"),
    ]
    edges = [_edge(x, "target") for x in ("a", "b", "c", "d", "e")]
    t = _transition("target")
    link_entry_points([t], nodes, edges)
    kinds = {e["kind"] for e in t.entry_points}
    assert kinds == {"API", "UI", "Batch", "Event", "CLI"}


# ---------------------------------------------------------------------------
# BFS semantics
# ---------------------------------------------------------------------------


def test_multi_hop_chain():
    """ctrl -> svc -> repo.save (trigger). Entry found at hop 2."""
    nodes = [
        _node("ctrl.approve", node_type="endpoint"),
        _node("svc.approve"),
        _node("repo.save"),
    ]
    edges = [
        _edge("ctrl.approve", "svc.approve"),
        _edge("svc.approve", "repo.save"),
    ]
    t = _transition("repo.save")
    link_entry_points([t], nodes, edges)
    assert len(t.entry_points) == 1
    assert t.entry_points[0]["hop_count"] == 2
    assert t.entry_points[0]["qualified_name"] == "ctrl.approve"


def test_multiple_entries_keep_shortest_hop():
    """Same trigger reachable from two entries — both recorded, sorted by hops."""
    nodes = [
        _node("ctrl.approve", node_type="endpoint"),
        _node("batch.nightly", node_type="batch_job"),
        _node("svc.approve"),
    ]
    edges = [
        _edge("ctrl.approve", "svc.approve"),
        _edge("batch.nightly", "ctrl.approve"),  # batch -> ctrl -> svc
    ]
    t = _transition("svc.approve")
    link_entry_points([t], nodes, edges)
    assert len(t.entry_points) == 2
    # ctrl.approve at hop 1, batch.nightly at hop 2
    assert t.entry_points[0]["qualified_name"] == "ctrl.approve"
    assert t.entry_points[0]["hop_count"] == 1
    assert t.entry_points[1]["qualified_name"] == "batch.nightly"
    assert t.entry_points[1]["hop_count"] == 2


def test_confidence_is_min_along_path():
    """Path confidence is the minimum edge confidence along the path."""
    nodes = [
        _node("ctrl.approve", node_type="endpoint"),
        _node("svc.approve"),
        _node("repo.save"),
    ]
    edges = [
        _edge("ctrl.approve", "svc.approve", conf=0.9),
        _edge("svc.approve", "repo.save", conf=0.6),
    ]
    t = _transition("repo.save")
    link_entry_points([t], nodes, edges)
    assert len(t.entry_points) == 1
    assert t.entry_points[0]["confidence"] == 0.6


def test_hop_cap_prevents_runaway():
    """Entries beyond _MAX_HOPS (6) are not discovered."""
    nodes = [_node(f"lvl{i}") for i in range(10)]
    nodes.append(_node("deep.entry", node_type="endpoint"))
    edges = [_edge(f"lvl{i}", f"lvl{i+1}") for i in range(9)]
    edges.append(_edge("deep.entry", "lvl0"))  # entry at depth 10 from lvl9
    t = _transition("lvl9")
    link_entry_points([t], nodes, edges)
    assert t.entry_points == []


def test_cycle_does_not_hang():
    """A cycle in the reverse graph must not cause infinite BFS."""
    nodes = [
        _node("a", node_type="endpoint"),
        _node("b"),
        _node("c"),
    ]
    edges = [
        _edge("a", "b"),
        _edge("b", "c"),
        _edge("c", "b"),  # cycle
    ]
    t = _transition("c")
    link_entry_points([t], nodes, edges)
    assert len(t.entry_points) == 1
    assert t.entry_points[0]["qualified_name"] == "a"


def test_transition_without_trigger_is_skipped():
    t = StateTransition(entity="Order", field="status", to_state="x", trigger_function=None)
    link_entry_points([t], [], [])
    assert t.entry_points == []
