from __future__ import annotations
from pathlib import Path
from ai_discovery import menu_detector as md

FIX = Path(__file__).parent / "fixtures" / "jsp"


def _make_repo(tmp_path):
    (tmp_path / "login.jsp").write_text((FIX / "login.jsp").read_text())
    cust = tmp_path / "customer"; cust.mkdir()
    (cust / "list.jsp").write_text((FIX / "customer" / "list.jsp").read_text())
    (tmp_path / "widget.tag").write_text((FIX / "widget.tag").read_text())
    return tmp_path


def _leaves(items):
    out = []
    for it in items:
        out.extend(_leaves(it.children)) if it.children else out.append(it)
    return out


def test_jsp_detector_builds_menu_items(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.JspMenuDetector().detect(repo)
    assert items is not None
    labels = {leaf.label for leaf in _leaves(items)}
    assert "Login" in labels and "Customers" in labels
    assert "Widget" not in labels


def test_jsp_screens_have_component_and_beans(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.JspMenuDetector().detect(repo)
    screens = md.build_screen_map(items, repo)
    by_label = {s.label: s for s in screens}
    assert "Customers" in by_label
    cust = by_label["Customers"]
    assert cust.fe_component.endswith("list.jsp")
    assert cust.metadata.get("bean_classes") == ["com.acme.CustomerService"]
    assert "customer" in cust.menu_path
    assert "Widget" not in by_label


def test_jsp_is_fallback_behind_jsonyaml(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "menu.json").write_text('[{"label":"X","path":"/x"}]')
    items, screens = md.detect_and_build_screens(repo)
    assert {s.label for s in screens} == {"X"}


def test_jsp_endtoend_when_no_menu(tmp_path):
    repo = _make_repo(tmp_path)
    items, screens = md.detect_and_build_screens(repo)
    assert items is not None
    assert "Login" in {s.label for s in screens}
