"""Parse one ASP.NET WebForms .aspx/.ascx file into a WebFormsPage (regex).

Pure: no CodeNode / Screen knowledge. Returns None when the file is not a
server page (no Page/Control directive) or cannot be read. Never raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DIRECTIVE_RE = re.compile(r"<%@\s*(?P<kind>Page|Control)\b(?P<body>.*?)%>", re.DOTALL | re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
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


def _attrs(body: str) -> dict[str, str]:
    """Lower-cased attribute name -> value for a directive body."""
    return {m.group(1).lower(): m.group(2) for m in _ATTR_RE.finditer(body)}


def extract_webforms_page(path: Path) -> WebFormsPage | None:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        logger.debug("webforms_extractor: cannot read %s", path)
        return None

    m = _DIRECTIVE_RE.search(content)
    if m is None:
        return None  # not a server page

    attrs = _attrs(m.group("body"))
    is_user_control = m.group("kind").lower() == "control"

    inherits = attrs.get("inherits")
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
    return page


def _extract_events(content: str) -> list[dict]:
    """Server-control event wiring: On<Event>="Handler" on <asp:*> tags. Skips
    client-side OnClient* handlers. (Implemented in Task 2.)"""
    return []
