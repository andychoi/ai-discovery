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


def test_events_extracted_with_id_and_handler():
    page = extract_webforms_page(FIX / "Default.aspx")
    pairs = {(e["control_id"], e["event"], e["handler"]) for e in page.events}
    assert ("btnSave", "Click", "btnSave_Click") in pairs
    assert ("lnkCancel", "Click", "lnkCancel_Click") in pairs


def test_events_row_command():
    page = extract_webforms_page(FIX / "Pages" / "Customer.aspx")
    assert {(e["event"], e["handler"]) for e in page.events} == {("RowCommand", "grid_RowCommand")}


def test_client_side_handlers_skipped():
    from ai_discovery.webforms_extractor import _extract_events
    content = '<asp:Button ID="b" runat="server" OnClientClick="doJs" OnClick="b_Click" />'
    events = _extract_events(content)
    assert {(e["event"], e["handler"]) for e in events} == {("Click", "b_Click")}


def test_events_self_closing_tag_with_slash_in_attr():
    # Self-closing tag with a '/' inside an attribute value must still yield the event.
    from ai_discovery.webforms_extractor import _extract_events
    content = '<asp:Button ID="b" runat="server" OnClick="b_Click" NavigateUrl="~/Pages/Edit.aspx" />'
    events = _extract_events(content)
    assert {(e["control_id"], e["event"], e["handler"]) for e in events} == {("b", "Click", "b_Click")}
