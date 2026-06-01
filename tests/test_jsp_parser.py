from __future__ import annotations
from pathlib import Path
from ai_discovery.parsers.jsp import JspParser
from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.domain_classifier import classify_domains
from ai_discovery.graph.models import CodeNode

FIX = Path(__file__).parent / "fixtures" / "jsp"


def test_parser_extensions_and_can_parse():
    p = JspParser()
    assert p.extensions == frozenset({".jsp", ".jspx", ".tag", ".tagx"})
    assert p.can_parse(FIX / "login.jsp") is True
    assert p.can_parse(FIX / "x.java") is False


def test_parse_emits_ui_component_with_hints():
    nodes = JspParser().parse_file(FIX / "login.jsp")
    assert len(nodes) == 1
    n = nodes[0]
    assert n.node_type == "ui_component"
    assert n.qualified_name.endswith("login.jsp")
    assert n.framework_hints["framework"] == "jsp"
    assert n.framework_hints["bean_classes"] == ["com.app.UserBean"]
    assert n.framework_hints["form_actions"] == ["/doLogin"]
    assert "com.app.UserBean" in n.calls


def test_page_resolves_edge_to_usebean_class():
    page = JspParser().parse_file(FIX / "login.jsp")[0]
    bean = CodeNode(
        file_path="UserBean.java", language="java", node_type="class",
        name="UserBean", qualified_name="com.app.UserBean",
        source_code="", line_start=1, line_end=2,
    )
    edges = build_call_graph([page, bean])
    assert any(e.callee == "com.app.UserBean" and e.metadata.get("resolved_by") == "exact"
               for e in edges if e.caller == page.qualified_name)


def test_page_colocates_with_bean_domain():
    page = JspParser().parse_file(FIX / "login.jsp")[0]
    bean = CodeNode(
        file_path="UserBean.java", language="java", node_type="class",
        name="UserBean", qualified_name="com.app.UserBean",
        source_code="", line_start=1, line_end=2,
    )
    classify_domains([page, bean])
    assert "/" not in page.domain and "\\" not in page.domain
    assert page.domain == bean.domain


def test_tag_file_flagged():
    n = JspParser().parse_file(FIX / "widget.tag")[0]
    assert n.node_type == "ui_component"
    assert n.framework_hints["is_tag_file"] is True
