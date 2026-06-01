# ASP.NET WebForms Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Walk `.aspx`/`.ascx`, emit a `ui_component` `CodeNode` per page linked to its code-behind class via page→handler call edges, and surface `.aspx` pages as `Screen`s via a fallback `WebFormsMenuDetector`.

**Architecture:** A pure `webforms_extractor.py` parses one server-page file into a `WebFormsPage` (regex — no tree-sitter grammar for ASPX markup). Two thin consumers use it: `WebFormsParser` (graph `CodeNode`s) and `WebFormsMenuDetector` (folder-tree `MenuItem`s → the existing `build_screen_map` → `Screen`s).

**Tech Stack:** Python 3.10+, stdlib `re`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-01-webforms-parsing-design.md`

**Branch:** `feat-webforms-parsing` (stacked on `feat-menu-route-parsing`, which provides the format-aware `build_screen_map` and `_humanize`).

---

## File Structure

- **Create** `src/ai_discovery/webforms_extractor.py` — `WebFormsPage` dataclass + `extract_webforms_page(path)`.
- **Create** `src/ai_discovery/parsers/webforms.py` — `WebFormsParser(LanguageParser)`.
- **Modify** `src/ai_discovery/repo/file_walker.py` — add `"webforms"` extensions.
- **Modify** `src/ai_discovery/repo/lang_detector.py` — add `.aspx`/`.ascx` to `_EXT_MAP`.
- **Modify** `src/ai_discovery/pipeline.py` — register `WebFormsParser()` in the parse phase.
- **Modify** `src/ai_discovery/menu_detector.py` — add `WebFormsMenuDetector` + register it last in `HybridMenuDetector`.
- **Create** fixtures `tests/fixtures/webforms/{Default.aspx, Pages/Customer.aspx, Widget.ascx, NoCodeBehind.aspx, notaspage.txt}`.
- **Create** `tests/test_webforms_extractor.py`, `tests/test_webforms_parser.py`, `tests/test_webforms_screens.py`.

Canonical types/signatures (defined in Task 1, used later):

```python
@dataclass
class WebFormsPage:
    file_path: str
    is_user_control: bool
    code_behind_class: str | None = None
    master_page: str | None = None
    title: str | None = None
    events: list[dict] = field(default_factory=list)   # [{"control_id": str|None, "event": str, "handler": str}]

def extract_webforms_page(path: Path) -> WebFormsPage | None
```

---

## Task 1: `webforms_extractor` — directive, code-behind, master, title

**Files:**
- Create: `src/ai_discovery/webforms_extractor.py`
- Create: `tests/fixtures/webforms/Default.aspx`, `tests/fixtures/webforms/Pages/Customer.aspx`, `tests/fixtures/webforms/Widget.ascx`, `tests/fixtures/webforms/NoCodeBehind.aspx`, `tests/fixtures/webforms/notaspage.txt`
- Test: `tests/test_webforms_extractor.py`

- [ ] **Step 1: Create fixtures**

`tests/fixtures/webforms/Default.aspx`:
```aspx
<%@ Page Language="C#" AutoEventWireup="true" CodeBehind="Default.aspx.cs" Inherits="MyApp.Default" MasterPageFile="~/Site.Master" Title="Home" %>
<asp:Content runat="server">
  <asp:Button ID="btnSave" runat="server" OnClick="btnSave_Click" Text="Save" />
  <asp:LinkButton ID="lnkCancel" runat="server" OnClick="lnkCancel_Click">Cancel</asp:LinkButton>
</asp:Content>
```

`tests/fixtures/webforms/Pages/Customer.aspx`:
```aspx
<%@ Page Language="C#" CodeBehind="Customer.aspx.cs" Inherits="MyApp.Pages.Customer, MyApp" %>
<title>Customer</title>
<asp:GridView ID="grid" runat="server" OnRowCommand="grid_RowCommand" />
```

`tests/fixtures/webforms/Widget.ascx`:
```aspx
<%@ Control Language="C#" CodeBehind="Widget.ascx.cs" Inherits="MyApp.Widget" %>
<asp:Label ID="lbl" runat="server" />
```

`tests/fixtures/webforms/NoCodeBehind.aspx`:
```aspx
<%@ Page Language="C#" %>
<html><body><h1>Static</h1></body></html>
```

`tests/fixtures/webforms/notaspage.txt`:
```
just some text, no directive
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_webforms_extractor.py
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
    assert page.code_behind_class == "MyApp.Pages.Customer"   # ", MyApp" stripped


def test_title_falls_back_to_title_tag():
    page = extract_webforms_page(FIX / "Pages" / "Customer.aspx")
    assert page.title == "Customer"   # from <title>, no Title= attr


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_webforms_extractor.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_discovery.webforms_extractor`.

- [ ] **Step 4: Write minimal implementation**

```python
# src/ai_discovery/webforms_extractor.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_webforms_extractor.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add src/ai_discovery/webforms_extractor.py tests/fixtures/webforms tests/test_webforms_extractor.py
git commit -m "feat(webforms): extractor — directive, code-behind, master, title"
```

---

## Task 2: `webforms_extractor` — server-control event extraction

**Files:**
- Modify: `src/ai_discovery/webforms_extractor.py` (`_extract_events`)
- Test: `tests/test_webforms_extractor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_events_extracted_with_id_and_handler():
    page = extract_webforms_page(FIX / "Default.aspx")
    pairs = {(e["control_id"], e["event"], e["handler"]) for e in page.events}
    assert ("btnSave", "Click", "btnSave_Click") in pairs
    assert ("lnkCancel", "Click", "lnkCancel_Click") in pairs


def test_events_row_command():
    page = extract_webforms_page(FIX / "Pages" / "Customer.aspx")
    assert {(e["event"], e["handler"]) for e in page.events} == {("RowCommand", "grid_RowCommand")}


def test_client_side_handlers_skipped():
    # OnClientClick is a client-side JS hook, not a server event handler.
    from ai_discovery.webforms_extractor import _extract_events
    content = '<asp:Button ID="b" runat="server" OnClientClick="doJs" OnClick="b_Click" />'
    events = _extract_events(content)
    assert {(e["event"], e["handler"]) for e in events} == {("Click", "b_Click")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_webforms_extractor.py -q -k events`
Expected: FAIL — `_extract_events` returns `[]`.

- [ ] **Step 3: Replace `_extract_events` with the real implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_webforms_extractor.py -q`
Expected: PASS (all 9).

- [ ] **Step 5: Commit**

```bash
git add src/ai_discovery/webforms_extractor.py tests/test_webforms_extractor.py
git commit -m "feat(webforms): extract server-control event wiring"
```

---

## Task 3: `WebFormsParser` + wiring (file_walker, lang_detector, pipeline)

**Files:**
- Create: `src/ai_discovery/parsers/webforms.py`
- Modify: `src/ai_discovery/repo/file_walker.py`, `src/ai_discovery/repo/lang_detector.py`, `src/ai_discovery/pipeline.py`
- Test: `tests/test_webforms_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webforms_parser.py
from __future__ import annotations
from pathlib import Path
from ai_discovery.parsers.webforms import WebFormsParser
from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CodeNode

FIX = Path(__file__).parent / "fixtures" / "webforms"


def test_parser_extensions_and_can_parse():
    p = WebFormsParser()
    assert p.extensions == frozenset({".aspx", ".ascx"})
    assert p.can_parse(FIX / "Default.aspx") is True
    assert p.can_parse(FIX / "x.cs") is False


def test_parse_emits_ui_component_with_hints():
    nodes = WebFormsParser().parse_file(FIX / "Default.aspx")
    assert len(nodes) == 1
    n = nodes[0]
    assert n.node_type == "ui_component"
    assert n.qualified_name.endswith("Default.aspx")
    assert n.framework_hints["code_behind_class"] == "MyApp.Default"
    assert n.framework_hints["framework"] == "webforms"
    assert "MyApp.Default.btnSave_Click" in n.calls


def test_page_resolves_edge_to_codebehind_handler():
    page = WebFormsParser().parse_file(FIX / "Default.aspx")[0]
    handler = CodeNode(
        file_path="Default.aspx.cs", language="csharp", node_type="method",
        name="btnSave_Click", qualified_name="MyApp.Default.btnSave_Click",
        source_code="", line_start=1, line_end=2,
    )
    edges = build_call_graph([page, handler])
    assert any(e.callee == "MyApp.Default.btnSave_Click" and e.metadata.get("resolved_by") == "exact"
               for e in edges if e.caller == page.qualified_name)


def test_user_control_is_component_not_screen_flagged():
    n = WebFormsParser().parse_file(FIX / "Widget.ascx")[0]
    assert n.node_type == "ui_component"
    assert n.framework_hints["is_user_control"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_webforms_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_discovery.parsers.webforms`.

- [ ] **Step 3: Write `WebFormsParser`**

```python
# src/ai_discovery/parsers/webforms.py
"""WebForms parser: .aspx/.ascx -> ui_component CodeNode linked to code-behind."""
from __future__ import annotations

from pathlib import Path

from ..graph.models import CodeNode
from ..webforms_extractor import extract_webforms_page
from .base import LanguageParser


class WebFormsParser(LanguageParser):
    @property
    def language(self) -> str:
        return "webforms"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".aspx", ".ascx"})

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        page = extract_webforms_page(file_path)
        if page is None:
            return []
        calls = [
            f"{page.code_behind_class}.{ev['handler']}"
            for ev in page.events
            if page.code_behind_class
        ]
        text = ""
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        return [
            CodeNode(
                file_path=str(file_path),
                language="webforms",
                node_type="ui_component",
                name=file_path.stem,
                qualified_name=str(file_path),
                source_code=text,
                line_start=1,
                line_end=text.count("\n") + 1,
                calls=calls,
                framework_hints={
                    "framework": "webforms",
                    "code_behind_class": page.code_behind_class,
                    "master_page": page.master_page,
                    "title": page.title,
                    "is_user_control": page.is_user_control,
                    "events": page.events,
                },
            )
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_webforms_parser.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire into walker, detector-map, and pipeline**

In `src/ai_discovery/repo/file_walker.py`, add to `_LANG_EXTENSIONS`:
```python
    "webforms": frozenset({".aspx", ".ascx"}),
```

In `src/ai_discovery/repo/lang_detector.py`, add to `_EXT_MAP` (alongside the other extensions):
```python
    ".aspx": "webforms",
    ".ascx": "webforms",
```

In `src/ai_discovery/pipeline.py`, import and register the parser. Find:
```python
from .parsers.javascript import JavaScriptParser
```
add after it:
```python
from .parsers.webforms import WebFormsParser
```
Find:
```python
parsers = [PythonParser(), CSharpParser(), JavaParser(), JavaScriptParser()]
```
replace with:
```python
parsers = [PythonParser(), CSharpParser(), JavaParser(), JavaScriptParser(), WebFormsParser()]
```

- [ ] **Step 6: Verify wiring + no regression**

Run: `python -m pytest tests/test_webforms_parser.py -q`
Then confirm language detection picks up the extensions:
```bash
python -c "
from pathlib import Path
from ai_discovery.repo.lang_detector import _EXT_MAP
assert _EXT_MAP['.aspx'] == 'webforms' and _EXT_MAP['.ascx'] == 'webforms'
from ai_discovery.repo.file_walker import _LANG_EXTENSIONS
assert '.aspx' in _LANG_EXTENSIONS['webforms']
from ai_discovery.pipeline import run_pipeline  # import smoke
print('wiring OK')
"
```
Expected: PASS + `wiring OK`.

- [ ] **Step 7: Commit**

```bash
git add src/ai_discovery/parsers/webforms.py src/ai_discovery/repo/file_walker.py src/ai_discovery/repo/lang_detector.py src/ai_discovery/pipeline.py tests/test_webforms_parser.py
git commit -m "feat(webforms): WebFormsParser + walker/detector/pipeline wiring"
```

---

## Task 4: `WebFormsMenuDetector` + screens + regression

**Files:**
- Modify: `src/ai_discovery/menu_detector.py`
- Test: `tests/test_webforms_screens.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webforms_screens.py
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


def test_webforms_detector_builds_menu_items(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.WebFormsMenuDetector().detect(repo)
    assert items is not None
    # leaf labels include the two pages; .ascx excluded
    labels = {leaf.label for leaf in _leaves(items)}
    assert "Home" in labels and "Customer" in labels
    assert "Widget" not in labels


def _leaves(items):
    out = []
    for it in items:
        if it.children:
            out.extend(_leaves(it.children))
        else:
            out.append(it)
    return out


def test_webforms_screens_have_component_and_codebehind(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.WebFormsMenuDetector().detect(repo)
    screens = md.build_screen_map(items, repo)
    by_label = {s.label: s for s in screens}
    assert "Customer" in by_label
    cust = by_label["Customer"]
    assert cust.fe_component.endswith("Customer.aspx")
    assert cust.metadata.get("code_behind_class") == "MyApp.Pages.Customer"
    assert "Pages" in cust.menu_path           # folder breadcrumb
    assert "Widget" not in by_label            # .ascx not a screen


def test_webforms_is_fallback_behind_jsonyaml(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "menu.json").write_text('[{"label":"X","path":"/x"}]')
    items, screens = md.detect_and_build_screens(repo)
    # JSON menu wins; WebForms is not consulted
    assert {s.label for s in screens} == {"X"}


def test_webforms_endtoend_when_no_menu(tmp_path):
    repo = _make_repo(tmp_path)
    items, screens = md.detect_and_build_screens(repo)
    assert items is not None
    assert "Home" in {s.label for s in screens}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_webforms_screens.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'WebFormsMenuDetector'`.

- [ ] **Step 3: Add `WebFormsMenuDetector` and register it**

In `src/ai_discovery/menu_detector.py`, add the import near the top (with the existing `from .route_parser import ...`):
```python
from .webforms_extractor import extract_webforms_page
from .repo.lang_detector import _SKIP_DIRS
```

Add this detector class (after `FrameworkRoutingDetector`, before `HybridMenuDetector`):
```python
class WebFormsMenuDetector(MenuDetector):
    """Build screens from ASP.NET WebForms .aspx pages (folder hierarchy = menu).

    Fallback for apps with no JS menu/router. .ascx user controls are excluded
    (they are partial components, not top-level screens)."""

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        repo = Path(repo_path)
        aspx = [
            p for p in sorted(repo.rglob("*.aspx"))
            if not (_SKIP_DIRS & set(p.parts))
        ]
        if not aspx:
            return None

        # Build a nested MenuItem tree keyed by directory segments relative to repo.
        root: dict = {"_dirs": {}, "_pages": []}
        for path in aspx:
            rel = path.relative_to(repo)
            parts = rel.parts[:-1]  # directory segments
            node = root
            for seg in parts:
                node = node["_dirs"].setdefault(seg, {"_dirs": {}, "_pages": []})
            node["_pages"].append(path)

        def build(node: dict, url_prefix: str) -> list[MenuItem]:
            items: list[MenuItem] = []
            for seg, child in sorted(node["_dirs"].items()):
                wrapper = MenuItem(
                    id=JsonYamlDetector._slugify(seg),
                    label=_humanize(seg),
                    path=f"{url_prefix}/{seg}",
                    metadata={"is_screen": False},
                    children=build(child, f"{url_prefix}/{seg}"),
                )
                items.append(wrapper)
            for path in sorted(node["_pages"]):
                page = extract_webforms_page(path)
                rel = path.relative_to(repo)
                label = (page.title if page and page.title else None) or _humanize(path.stem)
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(str(rel)),
                    label=label,
                    path=f"{url_prefix}/{path.name}",
                    metadata={
                        "is_screen": True,
                        "component_source": str(rel),
                        "code_behind_class": page.code_behind_class if page else None,
                        "master_page": page.master_page if page else None,
                        "framework": "webforms",
                    },
                ))
            return items

        return build(root, "")
```

In `HybridMenuDetector.__init__`, append the new detector LAST:
```python
        self.detectors = [
            JsonYamlDetector(),
            TypeScriptConstantDetector(),
            FrameworkRoutingDetector(),
            WebFormsMenuDetector(),
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_webforms_screens.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Full regression**

Run: `python -m pytest -q`
Expected: PASS (entire suite). If any existing menu/screen test fails, investigate — `WebFormsMenuDetector` must only fire when no JSON/YAML/TS/framework menu is found (it's last), so existing repos without `.aspx` are unaffected (`rglob("*.aspx")` empty → `None`).

- [ ] **Step 6: Commit**

```bash
git add src/ai_discovery/menu_detector.py tests/test_webforms_screens.py
git commit -m "feat(webforms): WebFormsMenuDetector — .aspx pages as fallback screens"
```

---

## Self-review notes (addressed)

- **Spec coverage:** directive/code-behind/master/title (T1), event wiring (T2), `ui_component` node + page→handler edge + wiring (T3), `WebFormsMenuDetector` + folder breadcrumb screens + `.ascx` exclusion + fallback precedence + e2e (T4). All spec sections map to a task.
- **Type consistency:** `WebFormsPage` fields and `extract_webforms_page` signature from T1 are used unchanged in T3/T4. `WebFormsParser` emits `framework_hints` keys (`code_behind_class`, `framework`, `is_user_control`, `events`, `master_page`, `title`) consumed consistently. `MenuItem` metadata keys (`is_screen`, `component_source`, `code_behind_class`, `master_page`) match what `build_screen_map` (Task 7 of route-parsing) reads.
- **No placeholders:** every code step is complete and runnable. The Task-1 `_extract_events` stub is explicitly replaced in Task 2 (declared as such).
- **Dependency note:** relies on `build_screen_map`'s `is_screen` metadata handling and `_humanize` from the stacked `feat-menu-route-parsing` branch — both present on this branch.
