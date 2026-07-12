"""Tests for the shared tree-sitter helpers (parsers/_ts.py).

These back the call-site / query-runner / inside-class logic that the four AST
parsers used to each copy verbatim. The parser suites exercise them indirectly;
this pins the shared contract directly (including the None-query guard).
"""

from __future__ import annotations

from ai_discovery.parsers import _ts


def test_inside_class_range_membership():
    ranges = {(2, 5), (10, 12)}

    class _N:
        def __init__(self, row):
            self.start_point = type("P", (), {"row": row})()

    assert _ts.inside_class(_N(3), ranges) is True
    assert _ts.inside_class(_N(2), ranges) is True   # inclusive lower
    assert _ts.inside_class(_N(5), ranges) is True   # inclusive upper
    assert _ts.inside_class(_N(10), ranges) is True
    assert _ts.inside_class(_N(7), ranges) is False
    assert _ts.inside_class(_N(0), set()) is False


def test_extract_call_sites_none_query_returns_empty():
    # Grammars that fail to compile the call-site query pass None — must yield
    # [] rather than raising.
    assert _ts.extract_call_sites(None, object()) == []


def test_extract_call_sites_on_real_python_source():
    """End-to-end through the real Python parser query: receiver is retained."""
    from ai_discovery.parsers import python_parser as pp

    src = b"class A:\n    def m(self):\n        self.svc.save(x)\n        helper(y)\n"
    tree = pp._PARSER.parse(src) if hasattr(pp, "_PARSER") else None
    if tree is None:
        # Parser instance is created per-parse; build one the same way the
        # module does.
        from tree_sitter import Parser
        parser = Parser(pp.PY_LANGUAGE)
        tree = parser.parse(src)

    sites = _ts.extract_call_sites(pp._CALL_SITE_QUERY, tree.root_node)
    by_name = {s["name"]: s["receiver"] for s in sites}
    assert "save" in by_name
    assert by_name["save"] == "self.svc"
    assert by_name.get("helper") is None  # unqualified → receiver None
