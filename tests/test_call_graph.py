"""Tests for call graph builder."""

from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CodeNode


def test_build_call_graph_resolves_internal_calls():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="process",
            qualified_name="svc.PaymentService.process",
            source_code="",
            line_start=1,
            line_end=10,
            calls=["validate", "charge"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="validate",
            qualified_name="svc.PaymentService.validate",
            source_code="",
            line_start=12,
            line_end=20,
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="charge",
            qualified_name="svc.PaymentService.charge",
            source_code="",
            line_start=22,
            line_end=30,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 2
    callee_names = {e.callee for e in edges}
    assert "svc.PaymentService.validate" in callee_names
    assert "svc.PaymentService.charge" in callee_names


def test_unresolved_calls_recorded():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="process",
            qualified_name="svc.process",
            source_code="",
            line_start=1,
            line_end=10,
            calls=["external_api_call"],
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "external_api_call"
    assert edges[0].confidence < 1.0


def test_empty_calls_no_edges():
    nodes = [
        CodeNode(
            file_path="x.py",
            language="python",
            node_type="method",
            name="noop",
            qualified_name="x.noop",
            source_code="",
            line_start=1,
            line_end=2,
        )
    ]
    assert build_call_graph(nodes) == []


def test_qualified_name_exact_match():
    """Exact qualified_name match should get confidence=1.0."""
    nodes = [
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="caller",
            qualified_name="mod.caller",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["mod.target"],
        ),
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="target",
            qualified_name="mod.target",
            source_code="",
            line_start=7,
            line_end=10,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].confidence == 1.0
    assert edges[0].callee == "mod.target"


def test_ambiguous_short_name():
    """Multiple nodes with the same short name prefer the same-file candidate."""
    nodes = [
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="caller",
            qualified_name="mod.caller",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["run"],
        ),
        CodeNode(
            file_path="a.py",
            language="python",
            node_type="method",
            name="run",
            qualified_name="mod.A.run",
            source_code="",
            line_start=7,
            line_end=10,
        ),
        CodeNode(
            file_path="b.py",
            language="python",
            node_type="method",
            name="run",
            qualified_name="mod.B.run",
            source_code="",
            line_start=1,
            line_end=5,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "mod.A.run"
    assert edges[0].confidence == 0.9


def test_same_class_match_preferred_over_global_ambiguity():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="process",
            qualified_name="svc.PaymentService.process",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["validate"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="validate",
            qualified_name="svc.PaymentService.validate",
            source_code="",
            line_start=7,
            line_end=10,
        ),
        CodeNode(
            file_path="other.py",
            language="python",
            node_type="method",
            name="validate",
            qualified_name="svc.OtherService.validate",
            source_code="",
            line_start=1,
            line_end=3,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "svc.PaymentService.validate"
    assert edges[0].confidence == 0.95


def test_same_module_match_preferred_when_no_same_class_match():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="function",
            name="process",
            qualified_name="svc.process",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["validate"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="function",
            name="validate",
            qualified_name="svc.validate",
            source_code="",
            line_start=7,
            line_end=10,
        ),
        CodeNode(
            file_path="other.py",
            language="python",
            node_type="function",
            name="validate",
            qualified_name="other.validate",
            source_code="",
            line_start=1,
            line_end=3,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "svc.validate"
    assert edges[0].confidence == 0.9


def test_suffix_match_resolves_partially_qualified_call():
    nodes = [
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="function",
            name="caller",
            qualified_name="svc.caller",
            source_code="",
            line_start=1,
            line_end=5,
            calls=["PaymentService.charge"],
        ),
        CodeNode(
            file_path="svc.py",
            language="python",
            node_type="method",
            name="charge",
            qualified_name="billing.PaymentService.charge",
            source_code="",
            line_start=7,
            line_end=10,
        ),
    ]
    edges = build_call_graph(nodes)
    assert len(edges) == 1
    assert edges[0].callee == "billing.PaymentService.charge"
    assert edges[0].confidence == 0.85
