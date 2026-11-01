"""Parse one ASP.NET WebForms .aspx/.ascx file into a WebFormsPage (regex).

Pure: no CodeNode / Screen knowledge. Returns None when the file is not a
server page (no Page/Control directive) or cannot be read. Never raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from .core.encoding import read_source

logger = logging.getLogger(__name__)

_DIRECTIVE_RE = re.compile(r"<%@\s*(?P<kind>Page|Control|Master|WebService|WebHandler|Application)\b(?P<body>.*?)%>",
                           re.DOTALL | re.IGNORECASE)
_REGISTER_RE = re.compile(r"<%@\s*Register\b(?P<body>.*?)%>", re.DOTALL | re.IGNORECASE)
_SQLDS_RE = re.compile(r"<asp:SqlDataSource\b([^>]*)>", re.DOTALL | re.IGNORECASE)
_SQL_ATTRS = ("selectcommand", "insertcommand", "updatecommand", "deletecommand")
_ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
# Opening <asp:*> tag, capturing its attribute string. `[^>]*` correctly spans
# self-closing `/>` tags and `/` inside attribute values (e.g. ~/Pages/x.aspx).
# Known limitation: a literal `>` inside an attribute value would truncate the
# captured attributes — rare in practice and accepted for this regex approach.
_ASP_TAG_RE = re.compile(r"<asp:[\w.]+\b([^>]*)>", re.DOTALL | re.IGNORECASE)
_EVENT_RE = re.compile(r'\bOn([A-Z]\w*)\s*=\s*"(\w+)"')
_ID_RE = re.compile(r'\bID\s*=\s*"([^"]*)"', re.IGNORECASE)


@dataclass
class WebFormsPage:
    file_path: str
    is_user_control: bool
    code_behind_class: str | None = None
    master_page: str | None = None
    title: str | None = None
    events: list[dict] = field(default_factory=list)
    # M6.11
    kind: str = "page"                    # page | control | master | webservice | handler | application
    auto_event_wireup: bool = True        # Page_Load & co. are called without markup naming them
    registers: list[dict] = field(default_factory=list)   # <%@ Register TagPrefix TagName Src %>
    sql: list[dict] = field(default_factory=list)         # <asp:SqlDataSource ID *Command>


def _attrs(body: str) -> dict[str, str]:
    """Lower-cased attribute name -> value for a directive body."""
    return {m.group(1).lower(): m.group(2) for m in _ATTR_RE.finditer(body)}


def extract_webforms_page(path: Path) -> WebFormsPage | None:
    try:
        content = read_source(path).text
    except Exception:
        logger.debug("webforms_extractor: cannot read %s", path)
        return None

    m = _DIRECTIVE_RE.search(content)
    if m is None:
        return None  # not a server page

    attrs = _attrs(m.group("body"))
    kind = {"webhandler": "handler"}.get(m.group("kind").lower(), m.group("kind").lower())
    is_user_control = kind == "control"

    # Inherits (pages, controls, Global.asax) or Class (.asmx / .ashx) names the code class
    inherits = attrs.get("inherits") or attrs.get("class")
    code_behind_class = inherits.split(",")[0].strip() if inherits else None

    title = attrs.get("title")
    if not title:
        tm = _TITLE_TAG_RE.search(content)
        title = tm.group(1).strip() if tm else None

    page = WebFormsPage(
        file_path=str(path),
        is_user_control=is_user_control,
        code_behind_class=code_behind_class,
        master_page=attrs.get("masterpagefile"),
        title=title,
    )
    page.events = _extract_events(content)
    page.kind = kind
    page.auto_event_wireup = attrs.get("autoeventwireup", "true").strip().lower() != "false"
    page.registers = [{"prefix": a.get("tagprefix"), "name": a.get("tagname"), "src": a.get("src")}
                      for a in (_attrs(r.group("body")) for r in _REGISTER_RE.finditer(content)) if a.get("src")]
    for ds in _SQLDS_RE.finditer(content):
        a = _attrs(ds.group(1))
        for key in _SQL_ATTRS:
            if a.get(key):
                page.sql.append({"id": a.get("id"), "op": key[: -len("command")], "sql": a[key]})
    return page


def _extract_events(content: str) -> list[dict]:
    """Server-control event wiring: On<Event>="Handler" on <asp:*> opening tags.
    Skips client-side OnClient* handlers (their value is JS, not a method)."""
    events: list[dict] = []
    for tag in _ASP_TAG_RE.finditer(content):
        attr_str = tag.group(1)
        id_match = _ID_RE.search(attr_str)
        control_id = id_match.group(1) if id_match else None
        for ev in _EVENT_RE.finditer(attr_str):
            event_name, handler = ev.group(1), ev.group(2)
            if event_name.startswith("Client"):
                continue  # OnClientClick etc. are client-side JS hooks
            events.append({"control_id": control_id, "event": event_name, "handler": handler})
    return events
