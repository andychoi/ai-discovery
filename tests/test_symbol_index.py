"""Tests for the LSP/SCIP symbol-resolution tier (Stage 0)."""

import json
from pathlib import Path

from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CodeNode
from ai_discovery.graph.symbol_index import SymbolIndex, load_symbol_index


def _method(qn):
    return CodeNode(file_path="x.java", language="java", node_type="method",
                    name=qn.split(".")[-1], qualified_name=qn, source_code="",
                    line_start=1, line_end=2)


def _interface_dispatch_nodes():
    stripe = _method("com.ex.StripeProcessor.process")
    paypal = _method("com.ex.PaypalProcessor.process")
    run = CodeNode(file_path="x.java", language="java", node_type="method",
                   name="run", qualified_name="com.ex.Checkout.run", source_code="",
                   line_start=1, line_end=3,
                   call_sites=[{"name": "process", "receiver": "processor"}])
    return [stripe, paypal, run]


def test_without_index_interface_dispatch_fans_out():
    edges = build_call_graph(_interface_dispatch_nodes())
    callees = {e.callee for e in edges if e.caller == "com.ex.Checkout.run"}
    # Heuristics can't disambiguate the interface → both impls (the false edge).
    assert "com.ex.StripeProcessor.process" in callees
    assert "com.ex.PaypalProcessor.process" in callees


def test_with_index_resolves_to_exact_impl_no_fanout():
    idx = SymbolIndex(entries={
        ("com.ex.Checkout.run", "process"): [
            {"receiver": "processor", "callee": "com.ex.StripeProcessor.process"}]})
    edges = build_call_graph(_interface_dispatch_nodes(), symbol_index=idx)
    run_edges = [(e.callee, e.confidence, e.metadata.get("resolved_by"))
                 for e in edges if e.caller == "com.ex.Checkout.run"]
    assert ("com.ex.StripeProcessor.process", 1.0, "index") in run_edges
    assert not any(c == "com.ex.PaypalProcessor.process" for c, _, _ in run_edges)


def test_load_symbol_index_from_dir_and_absent(tmp_path):
    assert load_symbol_index(tmp_path) is None         # absent → None (heuristic)
    (tmp_path / "symbol_index.json").write_text(json.dumps({
        "version": 1, "tool": "scip",
        "edges": [{"caller": "A.f", "call": "g", "callee": "B.g"}]}))
    idx = load_symbol_index(tmp_path)
    assert idx is not None and idx.tool == "scip"
    assert idx.resolve("A.f", "g", None) == "B.g"
    assert idx.resolve("A.f", "missing", None) is None


def test_index_receiver_disambiguation():
    idx = SymbolIndex(entries={("A.f", "save"): [
        {"receiver": "orders", "callee": "OrderRepo.save"},
        {"receiver": "users", "callee": "UserRepo.save"}]})
    assert idx.resolve("A.f", "save", "orders") == "OrderRepo.save"
    assert idx.resolve("A.f", "save", "users") == "UserRepo.save"
