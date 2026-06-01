from __future__ import annotations
from pathlib import Path
from ai_discovery.jsp_extractor import JspPage, extract_jsp_page

FIX = Path(__file__).parent / "fixtures" / "jsp"


def test_login_bean_action_title():
    page = extract_jsp_page(FIX / "login.jsp")
    assert page is not None
    assert page.is_tag_file is False
    assert page.title == "Login"
    assert page.bean_classes == ["com.app.UserBean"]
    assert page.form_actions == ["/doLogin"]


def test_includes_and_nested():
    page = extract_jsp_page(FIX / "customer" / "list.jsp")
    assert page.title == "Customers"
    assert page.bean_classes == ["com.app.CustomerService"]
    assert page.form_actions == ["/customers/search"]
    assert set(page.includes) == {"../header.jsp", "nav.jsp"}


def test_tag_file_flag():
    page = extract_jsp_page(FIX / "widget.tag")
    assert page is not None
    assert page.is_tag_file is True
    assert page.bean_classes == []


def test_plain_page_empty_lists():
    page = extract_jsp_page(FIX / "plain.jsp")
    assert page is not None
    assert page.title is None
    assert page.bean_classes == [] and page.form_actions == [] and page.includes == []


def test_missing_file_returns_none():
    assert extract_jsp_page(FIX / "does-not-exist.jsp") is None
