"""Tests for Phase 1.1: import-scoped call resolution.

The motivating problem: when two classes in a repo both expose a method with
the same short name (e.g. `OrderService.save` and `CustomerService.save`),
the legacy short-name resolver fans out to every candidate. With `imports`
and per-site `receiver` info from the parser, the resolver can pick the
right one based on the caller's import statements.

These tests use the Python parser end-to-end (not hand-built CodeNodes) so
they exercise the full chain: parser → call_sites → resolver → CallEdge.
"""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CallEdge, CodeNode
from ai_discovery.parsers.python_parser import PythonParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(tmp_path: Path, **files: str) -> list[CodeNode]:
    out: list[CodeNode] = []
    parser = PythonParser()
    for filename, source in files.items():
        f = tmp_path / filename
        f.write_text(source)
        out.extend(parser.parse_file(f))
    return out


def _edges_from(edges: list[CallEdge], caller: str) -> list[CallEdge]:
    return [e for e in edges if e.caller == caller]


# ---------------------------------------------------------------------------
# Parser-level: imports and call_sites are captured
# ---------------------------------------------------------------------------


def test_parser_captures_plain_import(tmp_path: Path):
    nodes = _parse(tmp_path, handler_py="""
import os

def read_env():
    return os.getenv("X")
""")
    func = next(n for n in nodes if n.name == "read_env")
    assert {"module": "os", "name": None, "alias": None} in func.imports


def test_parser_captures_from_import_with_alias(tmp_path: Path):
    nodes = _parse(tmp_path, handler_py="""
from svc.orders import OrderService as OS

def go():
    OS.save(1)
""")
    func = next(n for n in nodes if n.name == "go")
    assert {"module": "svc.orders", "name": "OrderService", "alias": "OS"} in func.imports


def test_parser_captures_multi_name_from_import(tmp_path: Path):
    nodes = _parse(tmp_path, handler_py="""
from pathlib import Path, PurePath

def fn():
    pass
""")
    func = next(n for n in nodes if n.name == "fn")
    modules = {(i["module"], i["name"]) for i in func.imports}
    assert ("pathlib", "Path") in modules
    assert ("pathlib", "PurePath") in modules


def test_parser_captures_call_site_receiver(tmp_path: Path):
    nodes = _parse(tmp_path, handler_py="""
from svc.orders import OrderService

def h():
    OrderService.save(1)
    plain_call()
""")
    func = next(n for n in nodes if n.name == "h")
    sites = {(s["name"], s["receiver"]) for s in func.call_sites}
    assert ("save", "OrderService") in sites
    assert ("plain_call", None) in sites


# ---------------------------------------------------------------------------
# Resolver: the core acceptance case from the Phase 1.1 spec
# ---------------------------------------------------------------------------


def test_same_named_methods_resolve_independently_via_import(tmp_path: Path):
    """OrderService.save vs CustomerService.save must not fan out to each other."""
    # Two service files in parallel packages.
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "orders.py").write_text("""
class OrderService:
    @staticmethod
    def save(x):
        pass
""")
    (tmp_path / "svc" / "customers.py").write_text("""
class CustomerService:
    @staticmethod
    def save(x):
        pass
""")
    # Caller imports only OrderService.
    (tmp_path / "handler.py").write_text("""
from svc.orders import OrderService

def handle():
    OrderService.save(42)
""")

    parser = PythonParser()
    nodes = (
        parser.parse_file(tmp_path / "svc" / "orders.py")
        + parser.parse_file(tmp_path / "svc" / "customers.py")
        + parser.parse_file(tmp_path / "handler.py")
    )
    edges = build_call_graph(nodes)

    handler_edges = _edges_from(edges, "handler.handle")
    # Exactly one edge to save — the OrderService one, not CustomerService.
    save_edges = [e for e in handler_edges if e.callee.endswith(".save")]
    assert len(save_edges) == 1, f"expected 1 edge to save(), got {save_edges}"
    assert save_edges[0].callee == "orders.OrderService.save"
    assert save_edges[0].metadata["resolved_by"] == "import_scope"
    assert save_edges[0].confidence >= 0.9


def test_import_alias_resolves_correctly(tmp_path: Path):
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "orders.py").write_text("""
class OrderService:
    @staticmethod
    def save(x):
        pass
""")
    (tmp_path / "handler.py").write_text("""
from svc.orders import OrderService as OS

def handle():
    OS.save(1)
""")

    parser = PythonParser()
    nodes = (
        parser.parse_file(tmp_path / "svc" / "orders.py")
        + parser.parse_file(tmp_path / "handler.py")
    )
    edges = build_call_graph(nodes)

    handler_edges = _edges_from(edges, "handler.handle")
    save_edges = [e for e in handler_edges if e.callee.endswith(".save")]
    assert len(save_edges) == 1
    assert save_edges[0].callee == "orders.OrderService.save"
    assert save_edges[0].metadata["resolved_by"] == "import_scope"


def test_unimported_receiver_falls_through_to_short_name(tmp_path: Path):
    """A call to a class the caller didn't import goes to short-name stage, not import-scope."""
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "orders.py").write_text("""
class OrderService:
    @staticmethod
    def save(x):
        pass
""")
    (tmp_path / "handler.py").write_text("""
# No import of OrderService.
def handle():
    save(1)  # plain, no receiver
""")

    parser = PythonParser()
    nodes = (
        parser.parse_file(tmp_path / "svc" / "orders.py")
        + parser.parse_file(tmp_path / "handler.py")
    )
    edges = build_call_graph(nodes)

    handler_edges = _edges_from(edges, "handler.handle")
    save_edges = [e for e in handler_edges if e.callee.endswith("save")]
    # Short-name stage picks up the single candidate (or falls to unresolved); either way
    # `resolved_by` must not be `import_scope` since there was no relevant import.
    for e in save_edges:
        assert e.metadata["resolved_by"] != "import_scope"


def test_exact_qualified_name_match_is_stage_one():
    """When a caller records a full qualified name as the call, Stage 1 resolves it."""
    caller = CodeNode(
        file_path="a.py",
        language="python",
        node_type="function",
        name="caller",
        qualified_name="a.caller",
        source_code="",
        line_start=1,
        line_end=1,
        call_sites=[{"name": "helpers.util", "receiver": None}],
    )
    target = CodeNode(
        file_path="helpers.py",
        language="python",
        node_type="function",
        name="util",
        qualified_name="helpers.util",
        source_code="",
        line_start=1,
        line_end=1,
    )
    edges = build_call_graph([caller, target])
    util_edges = [e for e in edges if e.callee == "helpers.util"]
    assert util_edges
    assert util_edges[0].metadata["resolved_by"] == "exact"
    assert util_edges[0].confidence == 1.0


def test_metadata_records_resolution_stage_for_unresolved(tmp_path: Path):
    nodes = _parse(tmp_path, handler_py="""
def handle():
    completely_unknown_thing()
""")
    edges = build_call_graph(nodes)
    unresolved = [e for e in edges if e.callee == "completely_unknown_thing"]
    assert unresolved
    assert unresolved[0].metadata["resolved_by"] == "unresolved"
    assert unresolved[0].confidence == 0.5


def test_backward_compat_nodes_without_call_sites_still_resolve():
    """Old-style CodeNodes (no call_sites, just `calls`) must still work via short-name stage."""
    caller = CodeNode(
        file_path="a.py",
        language="python",
        node_type="method",
        name="run",
        qualified_name="a.Foo.run",
        source_code="",
        line_start=1,
        line_end=5,
        calls=["helper"],  # legacy
    )
    helper = CodeNode(
        file_path="a.py",
        language="python",
        node_type="method",
        name="helper",
        qualified_name="a.Foo.helper",
        source_code="",
        line_start=10,
        line_end=15,
    )
    edges = build_call_graph([caller, helper])
    assert any(e.callee == "a.Foo.helper" for e in edges)
