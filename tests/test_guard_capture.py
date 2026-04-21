"""Tests for Phase 1.2: guard predicate capture on StateTransition.

Verifies each language parser records the enclosing `if`/`elif` condition as
`guard` in the transition hint dict, and that StateTransition objects built
from those hints carry `guard_expr` through to downstream consumers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.parsers.python_parser import PythonParser
from ai_discovery.parsers.java import JavaParser
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.javascript import JavaScriptParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _transitions(nodes) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        out.extend(n.framework_hints.get("transitions", []) or [])
    return out


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def test_python_captures_simple_if_guard(tmp_path: Path):
    code = '''
class Order:
    def approve(self, total):
        if total > 1000:
            self.status = "review"
'''
    f = tmp_path / "o.py"
    f.write_text(code)
    nodes = PythonParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["value"] == "review"
    assert ts[0]["guard"] == "total > 1000"


def test_python_no_guard_when_unconditional(tmp_path: Path):
    code = '''
class Order:
    def close(self):
        self.status = "closed"
'''
    f = tmp_path / "o.py"
    f.write_text(code)
    nodes = PythonParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["guard"] is None


def test_python_captures_elif_guard(tmp_path: Path):
    """elif's condition should be captured, not the parent if's."""
    code = '''
class Order:
    def route(self, score):
        if score > 900:
            self.status = "vip"
        elif score > 500:
            self.status = "standard"
'''
    f = tmp_path / "o.py"
    f.write_text(code)
    nodes = PythonParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 2
    by_value = {t["value"]: t for t in ts}
    assert by_value["vip"]["guard"] == "score > 900"
    assert by_value["standard"]["guard"] == "score > 500"


def test_python_innermost_guard_wins(tmp_path: Path):
    """Nested ifs: the innermost condition should be captured."""
    code = '''
class Order:
    def approve(self, total, vip):
        if vip:
            if total > 1000:
                self.status = "auto_review"
'''
    f = tmp_path / "o.py"
    f.write_text(code)
    nodes = PythonParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["guard"] == "total > 1000"


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


def test_java_captures_if_guard_and_strips_parens(tmp_path: Path):
    code = '''
public class Order {
    public void approve(int total) {
        if (total > 1000) {
            this.status = "review";
        }
    }
}
'''
    f = tmp_path / "Order.java"
    f.write_text(code)
    nodes = JavaParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["value"] == "review"
    assert ts[0]["guard"] == "total > 1000"


def test_java_no_guard_when_unconditional(tmp_path: Path):
    code = '''
public class Order {
    public void close() {
        this.status = "closed";
    }
}
'''
    f = tmp_path / "Order.java"
    f.write_text(code)
    nodes = JavaParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["guard"] is None


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------


def test_csharp_captures_if_guard_and_strips_parens(tmp_path: Path):
    code = '''
public class Order {
    public void Approve(int total) {
        if (total > 1000) {
            this.Status = "Review";
        }
    }
}
'''
    f = tmp_path / "Order.cs"
    f.write_text(code)
    nodes = CSharpParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["guard"] == "total > 1000"


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------


def test_javascript_captures_if_guard_and_strips_parens(tmp_path: Path):
    code = '''
class Order {
    approve(total) {
        if (total > 1000) {
            this.status = "review";
        }
    }
}
'''
    f = tmp_path / "order.js"
    f.write_text(code)
    nodes = JavaScriptParser().parse_file(f)
    ts = _transitions(nodes)
    assert len(ts) == 1
    assert ts[0]["guard"] == "total > 1000"


# ---------------------------------------------------------------------------
# End-to-end: guard_expr reaches StateTransition via call_graph
# ---------------------------------------------------------------------------


def test_state_transition_carries_guard_expr(tmp_path: Path):
    """End-to-end: parser hint → _create_execution_node → StateTransition.guard_expr."""
    from ai_discovery.graph.call_graph import ExecutionSliceBuilder

    code = '''
class Order:
    def approve(self, total):
        if total > 1000:
            self.status = "review"
'''
    f = tmp_path / "o.py"
    f.write_text(code)
    nodes = PythonParser().parse_file(f)

    # Find the `approve` method node
    approve = next(n for n in nodes if n.name == "approve")

    builder = ExecutionSliceBuilder(nodes, [])
    exec_node = builder._create_execution_node(approve.qualified_name, approve)
    assert exec_node.state_transition is not None
    assert exec_node.state_transition.to_state == "review"
    assert exec_node.state_transition.guard_expr == "total > 1000"
