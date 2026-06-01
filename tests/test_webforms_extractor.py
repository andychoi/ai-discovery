from __future__ import annotations
from pathlib import Path
from ai_discovery.webforms_extractor import WebFormsPage, extract_webforms_page

FIX = Path(__file__).parent / "fixtures" / "webforms"


def test_default_page_directive():
    page = extract_webforms_page(FIX / "Default.aspx")
    assert page is not None
    assert page.is_user_control is False
    assert page.code_behind_class == "MyApp.Default"
    assert page.master_page == "~/Site.Master"
    assert page.title == "Home"


def test_inherits_strips_assembly_suffix():
    page = extract_webforms_page(FIX / "Pages" / "Customer.aspx")
    assert page.code_behind_class == "MyApp.Pages.Customer"


def test_title_falls_back_to_title_tag():
    page = extract_webforms_page(FIX / "Pages" / "Customer.aspx")
    assert page.title == "Customer"


def test_user_control_flag():
    page = extract_webforms_page(FIX / "Widget.ascx")
    assert page.is_user_control is True
    assert page.code_behind_class == "MyApp.Widget"


def test_no_codebehind_still_a_page():
    page = extract_webforms_page(FIX / "NoCodeBehind.aspx")
    assert page is not None
    assert page.code_behind_class is None
    assert page.events == []


def test_non_page_returns_none():
    assert extract_webforms_page(FIX / "notaspage.txt") is None
