"""Parse one JSP file (.jsp/.jspx/.tag/.tagx) into a JspPage (regex).

Pure: no CodeNode / Screen knowledge. A readable .jsp-family file always
yields a JspPage (a .jsp is always a JSP — no directive gate); returns None
only on read failure. Never raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from .core.encoding import read_source

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_USEBEAN_RE = re.compile(r'<jsp:useBean\b[^>]*\bclass\s*=\s*"([^"]+)"', re.IGNORECASE)
_FORM_TAG_RE = re.compile(r"<form(?::form)?\b([^>]*)>", re.IGNORECASE)      # <form …> and Spring <form:form …>
_ATTR_RE = re.compile(r"""\b([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_EL_PREFIX_RE = re.compile(r"^(?:\$\{[^}]*\})+")                             # ${pageContext.request.contextPath}/x
_JSP_INCLUDE_RE = re.compile(r'<jsp:include\b[^>]*\bpage\s*=\s*"([^"]+)"', re.IGNORECASE)
_DIRECTIVE_INCLUDE_RE = re.compile(r'<%@\s*include\b[^>]*\bfile\s*=\s*"([^"]+)"', re.IGNORECASE)
_TAG_SUFFIXES = {".tag", ".tagx"}


@dataclass
class JspPage:
    file_path: str
    is_tag_file: bool
    title: str | None = None
    bean_classes: list[str] = field(default_factory=list)
    form_actions: list[str] = field(default_factory=list)
    forms: list[dict] = field(default_factory=list)       # M6.18: {"action", "method"}; method defaults to GET (HTML)
    includes: list[str] = field(default_factory=list)


def extract_jsp_page(path: Path) -> JspPage | None:
    try:
        content = read_source(path).text
    except Exception:
        logger.debug("jsp_extractor: cannot read %s", path)
        return None

    tm = _TITLE_RE.search(content)
    title = tm.group(1).strip() if tm else None

    bean_classes = [m.group(1).strip() for m in _USEBEAN_RE.finditer(content)]

    form_actions: list[str] = []
    forms: list[dict] = []
    for m in _FORM_TAG_RE.finditer(content):
        attrs = {a.group(1).lower(): (a.group(2) if a.group(2) is not None else a.group(3) or "")
                 for a in _ATTR_RE.finditer(m.group(1))}
        action = attrs.get("action", "").strip()
        if not action or action == "#" or action.lower().startswith("javascript:"):
            continue
        form_actions.append(action)
        route = _EL_PREFIX_RE.sub("", action) or action     # the context-path prefix is not part of the route
        forms.append({"action": route, "method": (attrs.get("method") or "get").strip().upper()})

    includes = [m.group(1) for m in _JSP_INCLUDE_RE.finditer(content)]
    includes += [m.group(1) for m in _DIRECTIVE_INCLUDE_RE.finditer(content)]

    return JspPage(
        file_path=str(path),
        is_tag_file=path.suffix.lower() in _TAG_SUFFIXES,
        title=title,
        bean_classes=bean_classes,
        form_actions=form_actions,
        forms=forms,
        includes=includes,
    )
