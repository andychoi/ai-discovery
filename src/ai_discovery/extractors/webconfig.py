"""Endpoints declared in deployment descriptors (roadmap item M6.11).

- `WEB-INF/web.xml`: `<servlet>` name → class, `<servlet-mapping>` url-pattern → endpoint calling the servlet's
  `service` / `doGet` / `doPost` / … (whichever exist);
- `web.config`: `<httpHandlers>` / `<handlers>` `<add path verb type>` → endpoint calling `ProcessRequest`;
- Struts 1 `struts-config.xml` `<action path type>` → endpoint calling `execute`; Struts 2 `struts.xml`
  `<package namespace><action name class method>` → endpoint calling `method` (default `execute`).

Each endpoint's call sites carry binding `xml_mapping`; the resolver binds a convention method only if the class
defines (or inherits) it. Regex over the XML: descriptors are small and often not well-formed enough for a
strict parser. Never raises.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..graph.models import CodeNode

_ATTR = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_TAG_TEXT = lambda tag: re.compile(rf"<{tag}>\s*([^<]+?)\s*</{tag}>", re.S)  # noqa: E731
_SERVLET = re.compile(r"<servlet>(.*?)</servlet>", re.S)
_SERVLET_MAPPING = re.compile(r"<servlet-mapping>(.*?)</servlet-mapping>", re.S)
_HANDLER_ADD = re.compile(r"<(?:httpHandlers|handlers)>(.*?)</(?:httpHandlers|handlers)>", re.S | re.I)
_ADD = re.compile(r"<add\b([^>]*)/?>", re.I)
_STRUTS1_ACTION = re.compile(r"<action\b([^>]*\bpath\s*=[^>]*)>", re.S)
_STRUTS2_PACKAGE = re.compile(r"<package\b([^>]*)>(.*?)</package>", re.S)
_STRUTS2_ACTION = re.compile(r"<action\b([^>]*\bname\s*=[^>]*)/?>", re.S)
_SERVLET_METHODS = ("service", "doGet", "doPost", "doPut", "doDelete")


def _attrs(text: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR.findall(text)}


def _endpoint(source: Path, route: str, method: str, cls: str, members: tuple[str, ...], kind: str) -> CodeNode:
    return CodeNode(
        file_path=str(source), language="java" if kind in ("servlet", "struts") else "csharp",
        node_type="endpoint", name=route, qualified_name=f"{source}#{kind}:{route}", source_code="",
        line_start=0, line_end=0,
        call_sites=[{"name": f"{cls}.{m}", "receiver": None, "binding": "xml_mapping"} for m in members],
        calls=[f"{cls}.{m}" for m in members],
        framework_hints={"method": method, "route": route, "framework": kind, "handler_class": cls,
                         "source": "descriptor"},
    )


def extract_web_xml(text: str, source: Path) -> list[CodeNode]:
    classes = {}
    for block in _SERVLET.findall(text):
        name, cls = _TAG_TEXT("servlet-name").search(block), _TAG_TEXT("servlet-class").search(block)
        if name and cls:
            classes[name.group(1)] = cls.group(1)
    out = []
    for block in _SERVLET_MAPPING.findall(text):
        name = _TAG_TEXT("servlet-name").search(block)
        for pattern in _TAG_TEXT("url-pattern").findall(block):
            if name and name.group(1) in classes:
                out.append(_endpoint(source, pattern, "ANY", classes[name.group(1)], _SERVLET_METHODS, "servlet"))
    return out


def extract_web_config(text: str, source: Path) -> list[CodeNode]:
    out = []
    for section in _HANDLER_ADD.findall(text):
        for add in _ADD.findall(section):
            a = _attrs(add)
            if a.get("path") and a.get("type"):
                verb = a.get("verb", "*")
                out.append(_endpoint(source, "/" + a["path"].lstrip("/"), "ANY" if verb == "*" else verb.upper(),
                                     a["type"].split(",")[0].strip(), ("ProcessRequest",), "httphandler"))
    return out


def extract_struts(text: str, source: Path) -> list[CodeNode]:
    out = []
    if "<action-mappings" in text:   # Struts 1
        for tag in _STRUTS1_ACTION.findall(text):
            a = _attrs(tag)
            if a.get("path") and a.get("type"):
                out.append(_endpoint(source, a["path"], "ANY", a["type"], ("execute",), "struts"))
    for pkg_attrs, body in _STRUTS2_PACKAGE.findall(text):   # Struts 2
        ns = _attrs(pkg_attrs).get("namespace", "/").rstrip("/")
        for tag in _STRUTS2_ACTION.findall(body):
            a = _attrs(tag)
            if a.get("name") and a.get("class"):
                out.append(_endpoint(source, f"{ns}/{a['name']}", "ANY", a["class"], (a.get("method") or "execute",),
                                     "struts"))
    return out


def read_webconfig_files(repo_path) -> list[CodeNode]:
    """Descriptor endpoints under *repo_path* (files the walker would read)."""
    from ..repo.walker import excluded_path

    root = Path(repo_path)
    out: list[CodeNode] = []
    for path in sorted(root.rglob("*.xml")) + sorted(root.rglob("*.config")):
        name = path.name.lower()
        if name not in ("web.xml", "web.config", "struts-config.xml", "struts.xml") or excluded_path(path, root):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name == "web.xml":
            out += extract_web_xml(text, path)
        elif name == "web.config":
            out += extract_web_config(text, path)
        else:
            out += extract_struts(text, path)
    return out
