from __future__ import annotations
from pathlib import Path
from ai_discovery.parsers.webforms import WebFormsParser
from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CodeNode

FIX = Path(__file__).parent / "fixtures" / "webforms"


def test_parser_extensions_and_can_parse():
    p = WebFormsParser()
    assert p.extensions == frozenset({".aspx", ".ascx"})
    assert p.can_parse(FIX / "Default.aspx") is True
    assert p.can_parse(FIX / "x.cs") is False


def test_parse_emits_ui_component_with_hints():
    nodes = WebFormsParser().parse_file(FIX / "Default.aspx")
    assert len(nodes) == 1
    n = nodes[0]
    assert n.node_type == "ui_component"
    assert n.qualified_name.endswith("Default.aspx")
    assert n.framework_hints["code_behind_class"] == "MyApp.Default"
    assert n.framework_hints["framework"] == "webforms"
    assert "MyApp.Default.btnSave_Click" in n.calls


def test_page_resolves_edge_to_codebehind_handler():
    page = WebFormsParser().parse_file(FIX / "Default.aspx")[0]
    handler = CodeNode(
        file_path="Default.aspx.cs", language="csharp", node_type="method",
        name="btnSave_Click", qualified_name="MyApp.Default.btnSave_Click",
        source_code="", line_start=1, line_end=2,
    )
    edges = build_call_graph([page, handler])
    assert any(e.callee == "MyApp.Default.btnSave_Click" and e.metadata.get("resolved_by") == "exact"
               for e in edges if e.caller == page.qualified_name)


def test_user_control_is_component_not_screen_flagged():
    n = WebFormsParser().parse_file(FIX / "Widget.ascx")[0]
    assert n.node_type == "ui_component"
    assert n.framework_hints["is_user_control"] is True
