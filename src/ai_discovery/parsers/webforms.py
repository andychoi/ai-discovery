"""WebForms parser: .aspx/.ascx/.master/.asmx/.ashx/Global.asax -> CodeNode linked to its code class.

Edges it declares (all resolved as `framework` bindings, M6.11):
- `markup_event`: `OnClick="Save_Click"` on a server control → `<CodeClass>.Save_Click`;
- `markup_auto`: lifecycle methods ASP.NET calls by name (`Page_Load`, `Application_Start`, `ProcessRequest`) —
  only when the method exists (or is inherited) in the code class, and only with `AutoEventWireup` on;
- `markup_register`: `<%@ Register Src="~/Controls/Cart.ascx" %>` → the user control's node.
"""
from __future__ import annotations

import posixpath
from pathlib import Path

from ..graph.domain_classifier import infer_domain
from ..graph.models import CodeNode
from ..webforms_extractor import extract_webforms_page
from .base import LanguageParser
from ..core.encoding import read_source

_PAGE_LIFECYCLE = ("Page_PreInit", "Page_Init", "Page_InitComplete", "Page_PreLoad", "Page_Load",
                   "Page_LoadComplete", "Page_PreRender", "Page_PreRenderComplete", "Page_SaveStateComplete",
                   "Page_Unload", "Page_Error", "Page_DataBind")
_APPLICATION = ("Application_Start", "Application_End", "Application_Error", "Application_BeginRequest",
                "Application_EndRequest", "Application_AuthenticateRequest", "Application_AuthorizeRequest",
                "Session_Start", "Session_End")
_AUTO = {"page": _PAGE_LIFECYCLE, "control": _PAGE_LIFECYCLE, "master": _PAGE_LIFECYCLE,
         "application": _APPLICATION, "handler": ("ProcessRequest",), "webservice": ()}
_NODE_TYPE = {"handler": "endpoint", "webservice": "endpoint"}


def _app_root(path: Path) -> Path:
    """The web application root `~` refers to: nearest ancestor holding a project file or web.config."""
    for d in path.parents:
        try:
            if any(d.glob("*.csproj")) or any(d.glob("*.vbproj")) or (d / "web.config").exists() \
                    or (d / "Web.config").exists():
                return d
        except OSError:
            break
    return path.parent


def _resolve_src(src: str, page: Path) -> str:
    if src.startswith("~/"):
        return str(_app_root(page) / src[2:])
    return posixpath.normpath(str(page.parent / src))


class WebFormsParser(LanguageParser):
    @property
    def language(self) -> str:
        return "webforms"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".aspx", ".ascx", ".master", ".asmx", ".ashx", ".asax"})

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        page = extract_webforms_page(file_path)
        if page is None:
            return []
        cls = page.code_behind_class
        sites: list[dict] = []
        if cls:
            sites += [{"name": f"{cls}.{ev['handler']}", "receiver": None, "binding": "markup_event"}
                      for ev in page.events]
            if page.auto_event_wireup or page.kind in ("application", "handler"):
                sites += [{"name": f"{cls}.{m}", "receiver": None, "binding": "markup_auto"}
                          for m in _AUTO.get(page.kind, ())]
        sites += [{"name": _resolve_src(r["src"], file_path), "receiver": None, "binding": "markup_register"}
                  for r in page.registers]
        text = ""
        try:
            text = read_source(file_path).text
        except Exception:
            pass
        domain = infer_domain(cls, str(file_path)) if cls else None
        hints = {
            "framework": "webforms",
            "webforms_kind": page.kind,
            "code_behind_class": cls,
            "master_page": page.master_page,
            "title": page.title,
            "events": page.events,
            "is_user_control": page.is_user_control,
        }
        if page.registers:
            hints["registers"] = page.registers
        if page.sql:
            hints["sql"] = page.sql   # SqlDataSource commands (table edges: M6.8)
        node_type = _NODE_TYPE.get(page.kind, "ui_component")
        if node_type == "endpoint":
            try:
                route = "/" + file_path.relative_to(_app_root(file_path)).as_posix()
            except ValueError:
                route = "/" + file_path.name
            hints.update({"method": "ANY", "route": route})
        return [
            CodeNode(
                file_path=str(file_path),
                language="webforms",
                node_type=node_type,
                name=file_path.stem,
                qualified_name=str(file_path),
                source_code=text,
                line_start=1,
                line_end=text.count("\n") + 1,
                calls=[s["name"] for s in sites],
                call_sites=sites,
                domain=domain,
                framework_hints=hints,
            )
        ]
