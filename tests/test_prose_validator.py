"""Tests for deterministic prose validation against the parsed graph (P0-4)."""

from __future__ import annotations

from ai_discovery.ai.prose_validator import (
    annotate,
    build_known_graph,
    validate_prose,
)
from ai_discovery.graph.models import CodeNode


def _node(file_path: str, name: str, qualified_name: str) -> CodeNode:
    return CodeNode(
        file_path=file_path, language="java", node_type="method",
        name=name, qualified_name=qualified_name,
        source_code="", line_start=1, line_end=5,
    )


KNOWN = [
    _node("src/orders/OrderController.java", "create", "com.x.OrderController.create"),
    _node("src/orders/OrderService.java", "process", "com.x.OrderService.process"),
    _node("src/orders/Order.java", "Order", "com.x.Order"),
]


def test_clean_prose_passes():
    g = build_known_graph(KNOWN)
    md = (
        "The `OrderController.create` endpoint at `src/orders/OrderController.java:1` "
        "calls `OrderService.process`. See `com.x.Order`."
    )
    v = validate_prose(md, g)
    assert v.is_clean
    assert v.count == 0


def test_invented_file_is_flagged():
    g = build_known_graph(KNOWN)
    md = "Logic lives in `src/ghost/PhantomController.java:42`."
    v = validate_prose(md, g)
    assert "src/ghost/PhantomController.java" in v.unknown_files


def test_invented_symbol_is_flagged():
    g = build_known_graph(KNOWN)
    # Neither 'PaymentReconciliation' nor 'settle' is any known symbol name.
    md = "The system invokes `PaymentReconciliation.settle` nightly."
    v = validate_prose(md, g)
    assert "PaymentReconciliation.settle" in v.unknown_symbols


def test_real_cross_reference_not_flagged():
    g = build_known_graph(KNOWN)
    # Partially-known symbol: 'Order' is a real class, 'archive' isn't a method
    # in the graph, but because a segment is real we do NOT flag (conservative).
    md = "A future `Order.archive` job is planned."
    v = validate_prose(md, g)
    assert v.is_clean


def test_prose_abbreviations_not_flagged():
    g = build_known_graph(KNOWN)
    md = "This applies broadly, e.g. across modules, i.e. everywhere. See v1.2 notes."
    v = validate_prose(md, g)
    assert v.is_clean


def test_urls_not_flagged_as_files():
    g = build_known_graph(KNOWN)
    md = "Synced via https://crm.partner.com/sync and www.example.com daily."
    v = validate_prose(md, g)
    assert v.unknown_files == []


def test_annotate_appends_note_when_dirty():
    g = build_known_graph(KNOWN)
    md = "Calls `PhantomService.run` in `src/ghost/X.java:9`."
    v = validate_prose(md, g)
    out = annotate(md, v)
    assert "Unverified code references" in out
    assert "PhantomService.run" in out
    assert "src/ghost/X.java" in out


def test_annotate_noop_when_clean():
    g = build_known_graph(KNOWN)
    md = "All good: `OrderService.process`."
    v = validate_prose(md, g)
    assert annotate(md, v) == md


def test_build_known_graph_accepts_dict_rows():
    rows = [{"file_path": "a/B.java", "name": "B", "qualified_name": "p.B"}]
    g = build_known_graph(rows)
    assert "B.java" in g.file_basenames
    assert "B" in g.symbol_names
    assert "p.B" in g.qualified_names
