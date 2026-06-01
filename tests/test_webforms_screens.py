from __future__ import annotations
from pathlib import Path
from ai_discovery import menu_detector as md

FIX = Path(__file__).parent / "fixtures" / "webforms"


def _make_repo(tmp_path):
    (tmp_path / "Default.aspx").write_text((FIX / "Default.aspx").read_text())
    pages = tmp_path / "Pages"; pages.mkdir()
    (pages / "Customer.aspx").write_text((FIX / "Pages" / "Customer.aspx").read_text())
    (tmp_path / "Widget.ascx").write_text((FIX / "Widget.ascx").read_text())
    return tmp_path


def _leaves(items):
    out = []
    for it in items:
        if it.children:
            out.extend(_leaves(it.children))
        else:
            out.append(it)
    return out


def test_webforms_detector_builds_menu_items(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.WebFormsMenuDetector().detect(repo)
    assert items is not None
    labels = {leaf.label for leaf in _leaves(items)}
    assert "Home" in labels and "Customer" in labels
    assert "Widget" not in labels


def test_webforms_screens_have_component_and_codebehind(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.WebFormsMenuDetector().detect(repo)
    screens = md.build_screen_map(items, repo)
    by_label = {s.label: s for s in screens}
    assert "Customer" in by_label
    cust = by_label["Customer"]
    assert cust.fe_component.endswith("Customer.aspx")
    assert cust.metadata.get("code_behind_class") == "MyApp.Pages.Customer"
    assert "Pages" in cust.menu_path
    assert "Widget" not in by_label


def test_webforms_is_fallback_behind_jsonyaml(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "menu.json").write_text('[{"label":"X","path":"/x"}]')
    items, screens = md.detect_and_build_screens(repo)
    assert {s.label for s in screens} == {"X"}


def test_webforms_endtoend_when_no_menu(tmp_path):
    repo = _make_repo(tmp_path)
    items, screens = md.detect_and_build_screens(repo)
    assert items is not None
    assert "Home" in {s.label for s in screens}
