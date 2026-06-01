# JSP Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Walk `.jsp`/`.jspx`/`.tag`/`.tagx`, emit a `ui_component` `CodeNode` per page linked to its `<jsp:useBean class>` backend class via an exact call edge, and surface `.jsp`/`.jspx` pages as `Screen`s via a fallback `JspMenuDetector`.

**Architecture:** A pure `jsp_extractor.py` parses one JSP file into a `JspPage` (regex). Two thin consumers use it: `JspParser` (graph `CodeNode`s) and `JspMenuDetector` (folder-tree `MenuItem`s → the existing `build_screen_map` → `Screen`s). Mirrors the WebForms feature.

**Tech Stack:** Python 3.10+, stdlib `re`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-01-jsp-parsing-design.md`

**Branch:** `feat-jsp-parsing` (stacked on `feat-webforms-parsing`, which provides the `infer_domain` path-qn guard, `classify_domains` pre-set-domain honoring, `build_screen_map`, and `_humanize`).

---

## File Structure

- **Create** `src/ai_discovery/jsp_extractor.py` — `JspPage` + `extract_jsp_page(path)`.
- **Create** `src/ai_discovery/parsers/jsp.py` — `JspParser(LanguageParser)`.
- **Modify** `src/ai_discovery/repo/file_walker.py`, `src/ai_discovery/repo/lang_detector.py`, `src/ai_discovery/pipeline.py` — wiring.
- **Modify** `src/ai_discovery/menu_detector.py` — `JspMenuDetector` + register last.
- **Create** fixtures `tests/fixtures/jsp/{login.jsp, customer/list.jsp, widget.tag, plain.jsp}`.
- **Create** `tests/test_jsp_extractor.py`, `tests/test_jsp_parser.py`, `tests/test_jsp_screens.py`.

Canonical types/signatures (Task 1, used later):

```python
@dataclass
class JspPage:
    file_path: str
    is_tag_file: bool
    title: str | None = None
    bean_classes: list[str] = field(default_factory=list)
    form_actions: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)

def extract_jsp_page(path: Path) -> JspPage | None
```

---

## Task 1: `jsp_extractor` — title, useBean, form actions, includes

**Files:**
- Create: `src/ai_discovery/jsp_extractor.py`
- Create: `tests/fixtures/jsp/login.jsp`, `tests/fixtures/jsp/customer/list.jsp`, `tests/fixtures/jsp/widget.tag`, `tests/fixtures/jsp/plain.jsp`
- Test: `tests/test_jsp_extractor.py`

- [ ] **Step 1: Create fixtures**

`tests/fixtures/jsp/login.jsp`:
```jsp
<%@ page contentType="text/html" %>
<jsp:useBean id="user" class="com.app.UserBean" scope="request" />
<html><head><title>Login</title></head>
<body>
  <form action="/doLogin" method="post">
    <input name="u"/><input name="p" type="password"/>
  </form>
  <a href="bad" onclick="return false"></a>
  <form action="#"></form>
  <form action="javascript:void(0)"></form>
</body></html>
```

`tests/fixtures/jsp/customer/list.jsp`:
```jsp
<%@ include file="../header.jsp" %>
<jsp:include page="nav.jsp" />
<jsp:useBean id="svc" class="com.app.CustomerService" scope="session" />
<title>Customers</title>
<form action="/customers/search"></form>
```

`tests/fixtures/jsp/widget.tag`:
```jsp
<%@ tag body-content="empty" %>
<span>widget</span>
```

`tests/fixtures/jsp/plain.jsp`:
```jsp
<html><body><h1>Static page, no beans or forms</h1></body></html>
```

- [ ] **Step 2: Write the failing test `tests/test_jsp_extractor.py`:**

```python
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
    assert page.form_actions == ["/doLogin"]   # "#" and javascript: skipped


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_jsp_extractor.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_discovery.jsp_extractor`.

- [ ] **Step 4: Write `src/ai_discovery/jsp_extractor.py`:**

```python
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

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_USEBEAN_RE = re.compile(r'<jsp:useBean\b[^>]*\bclass\s*=\s*"([^"]+)"', re.IGNORECASE)
_FORM_ACTION_RE = re.compile(r'<form\b[^>]*\baction\s*=\s*"([^"]*)"', re.IGNORECASE)
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
    includes: list[str] = field(default_factory=list)


def extract_jsp_page(path: Path) -> JspPage | None:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        logger.debug("jsp_extractor: cannot read %s", path)
        return None

    tm = _TITLE_RE.search(content)
    title = tm.group(1).strip() if tm else None

    bean_classes = [m.group(1).strip() for m in _USEBEAN_RE.finditer(content)]

    form_actions: list[str] = []
    for m in _FORM_ACTION_RE.finditer(content):
        action = m.group(1).strip()
        if not action or action == "#" or action.lower().startswith("javascript:"):
            continue
        form_actions.append(action)

    includes = [m.group(1) for m in _JSP_INCLUDE_RE.finditer(content)]
    includes += [m.group(1) for m in _DIRECTIVE_INCLUDE_RE.finditer(content)]

    return JspPage(
        file_path=str(path),
        is_tag_file=path.suffix.lower() in _TAG_SUFFIXES,
        title=title,
        bean_classes=bean_classes,
        form_actions=form_actions,
        includes=includes,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_jsp_extractor.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/ai_discovery/jsp_extractor.py tests/fixtures/jsp tests/test_jsp_extractor.py
git commit -m "feat(jsp): extractor — title, useBean, form actions, includes"
```

---

## Task 2: `JspParser` + wiring (file_walker, lang_detector, pipeline)

**Files:**
- Create: `src/ai_discovery/parsers/jsp.py`
- Modify: `src/ai_discovery/repo/file_walker.py`, `src/ai_discovery/repo/lang_detector.py`, `src/ai_discovery/pipeline.py`
- Test: `tests/test_jsp_parser.py`

- [ ] **Step 1: Write the failing test `tests/test_jsp_parser.py`:**

```python
from __future__ import annotations
from pathlib import Path
from ai_discovery.parsers.jsp import JspParser
from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.domain_classifier import classify_domains
from ai_discovery.graph.models import CodeNode

FIX = Path(__file__).parent / "fixtures" / "jsp"


def test_parser_extensions_and_can_parse():
    p = JspParser()
    assert p.extensions == frozenset({".jsp", ".jspx", ".tag", ".tagx"})
    assert p.can_parse(FIX / "login.jsp") is True
    assert p.can_parse(FIX / "x.java") is False


def test_parse_emits_ui_component_with_hints():
    nodes = JspParser().parse_file(FIX / "login.jsp")
    assert len(nodes) == 1
    n = nodes[0]
    assert n.node_type == "ui_component"
    assert n.qualified_name.endswith("login.jsp")
    assert n.framework_hints["framework"] == "jsp"
    assert n.framework_hints["bean_classes"] == ["com.app.UserBean"]
    assert n.framework_hints["form_actions"] == ["/doLogin"]
    assert "com.app.UserBean" in n.calls


def test_page_resolves_edge_to_usebean_class():
    page = JspParser().parse_file(FIX / "login.jsp")[0]
    bean = CodeNode(
        file_path="UserBean.java", language="java", node_type="class",
        name="UserBean", qualified_name="com.app.UserBean",
        source_code="", line_start=1, line_end=2,
    )
    edges = build_call_graph([page, bean])
    assert any(e.callee == "com.app.UserBean" and e.metadata.get("resolved_by") == "exact"
               for e in edges if e.caller == page.qualified_name)


def test_page_colocates_with_bean_domain():
    page = JspParser().parse_file(FIX / "login.jsp")[0]
    bean = CodeNode(
        file_path="UserBean.java", language="java", node_type="class",
        name="UserBean", qualified_name="com.app.UserBean",
        source_code="", line_start=1, line_end=2,
    )
    classify_domains([page, bean])
    assert "/" not in page.domain and "\\" not in page.domain
    assert page.domain == bean.domain


def test_tag_file_flagged():
    n = JspParser().parse_file(FIX / "widget.tag")[0]
    assert n.node_type == "ui_component"
    assert n.framework_hints["is_tag_file"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jsp_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_discovery.parsers.jsp`.

- [ ] **Step 3: Write `src/ai_discovery/parsers/jsp.py`:**

```python
"""JSP parser: .jsp/.jspx/.tag/.tagx -> ui_component CodeNode linked to useBean class."""
from __future__ import annotations

from pathlib import Path

from ..graph.domain_classifier import infer_domain
from ..graph.models import CodeNode
from ..jsp_extractor import extract_jsp_page
from .base import LanguageParser


class JspParser(LanguageParser):
    @property
    def language(self) -> str:
        return "jsp"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".jsp", ".jspx", ".tag", ".tagx"})

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        page = extract_jsp_page(file_path)
        if page is None:
            return []
        text = ""
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        domain = infer_domain(page.bean_classes[0], str(file_path)) if page.bean_classes else None
        return [
            CodeNode(
                file_path=str(file_path),
                language="jsp",
                node_type="ui_component",
                name=file_path.stem,
                qualified_name=str(file_path),
                source_code=text,
                line_start=1,
                line_end=text.count("\n") + 1,
                calls=list(page.bean_classes),
                domain=domain,
                framework_hints={
                    "framework": "jsp",
                    "bean_classes": page.bean_classes,
                    "form_actions": page.form_actions,
                    "includes": page.includes,
                    "title": page.title,
                    "is_tag_file": page.is_tag_file,
                },
            )
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jsp_parser.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Wire into walker, lang_detector, and pipeline**

In `src/ai_discovery/repo/file_walker.py`, add to `_LANG_EXTENSIONS`:
```python
    "jsp": frozenset({".jsp", ".jspx", ".tag", ".tagx"}),
```

In `src/ai_discovery/repo/lang_detector.py`, add to `_EXT_MAP`:
```python
    ".jsp": "jsp",
    ".jspx": "jsp",
    ".tag": "jsp",
    ".tagx": "jsp",
```

In `src/ai_discovery/pipeline.py`: after `from .parsers.webforms import WebFormsParser` add:
```python
from .parsers.jsp import JspParser
```
and change the parsers list to append `JspParser()`:
```python
parsers = [PythonParser(), CSharpParser(), JavaParser(), JavaScriptParser(), WebFormsParser(), JspParser()]
```

- [ ] **Step 6: Verify wiring + no regression**

Run: `python -m pytest tests/test_jsp_parser.py -q`
Then:
```bash
python -c "
from ai_discovery.repo.lang_detector import _EXT_MAP
assert _EXT_MAP['.jsp']=='jsp' and _EXT_MAP['.tagx']=='jsp'
from ai_discovery.repo.file_walker import _LANG_EXTENSIONS
assert '.jsp' in _LANG_EXTENSIONS['jsp']
import ai_discovery.pipeline
print('wiring OK')
"
```
Expected: PASS + `wiring OK`.

- [ ] **Step 7: Commit**

```bash
git add src/ai_discovery/parsers/jsp.py src/ai_discovery/repo/file_walker.py src/ai_discovery/repo/lang_detector.py src/ai_discovery/pipeline.py tests/test_jsp_parser.py
git commit -m "feat(jsp): JspParser + walker/detector/pipeline wiring"
```

---

## Task 3: `JspMenuDetector` + screens + regression

**Files:**
- Modify: `src/ai_discovery/menu_detector.py`
- Test: `tests/test_jsp_screens.py`

- [ ] **Step 1: Write the failing test `tests/test_jsp_screens.py`:**

```python
from __future__ import annotations
from pathlib import Path
from ai_discovery import menu_detector as md

FIX = Path(__file__).parent / "fixtures" / "jsp"


def _make_repo(tmp_path):
    (tmp_path / "login.jsp").write_text((FIX / "login.jsp").read_text())
    cust = tmp_path / "customer"; cust.mkdir()
    (cust / "list.jsp").write_text((FIX / "customer" / "list.jsp").read_text())
    (tmp_path / "widget.tag").write_text((FIX / "widget.tag").read_text())
    return tmp_path


def _leaves(items):
    out = []
    for it in items:
        out.extend(_leaves(it.children)) if it.children else out.append(it)
    return out


def test_jsp_detector_builds_menu_items(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.JspMenuDetector().detect(repo)
    assert items is not None
    labels = {leaf.label for leaf in _leaves(items)}
    assert "Login" in labels and "Customers" in labels
    assert "Widget" not in labels   # .tag excluded


def test_jsp_screens_have_component_and_beans(tmp_path):
    repo = _make_repo(tmp_path)
    items = md.JspMenuDetector().detect(repo)
    screens = md.build_screen_map(items, repo)
    by_label = {s.label: s for s in screens}
    assert "Customers" in by_label
    cust = by_label["Customers"]
    assert cust.fe_component.endswith("list.jsp")
    assert cust.metadata.get("bean_classes") == ["com.app.CustomerService"]
    assert "customer" in cust.menu_path
    assert "Widget" not in by_label


def test_jsp_is_fallback_behind_jsonyaml(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "menu.json").write_text('[{"label":"X","path":"/x"}]')
    items, screens = md.detect_and_build_screens(repo)
    assert {s.label for s in screens} == {"X"}


def test_jsp_endtoend_when_no_menu(tmp_path):
    repo = _make_repo(tmp_path)
    items, screens = md.detect_and_build_screens(repo)
    assert items is not None
    assert "Login" in {s.label for s in screens}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_jsp_screens.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'JspMenuDetector'`.

- [ ] **Step 3: Add `JspMenuDetector` and register it**

In `src/ai_discovery/menu_detector.py`, add the import near the existing `from .webforms_extractor import extract_webforms_page` line:
```python
from .jsp_extractor import extract_jsp_page
```
(`_SKIP_DIRS` is already imported on the WebForms branch.)

Add this class after `WebFormsMenuDetector`, before `HybridMenuDetector`:
```python
class JspMenuDetector(MenuDetector):
    """Build screens from JSP pages (.jsp/.jspx; folder hierarchy = menu).

    Fallback for apps with no JS menu/router. .tag/.tagx tag files are excluded
    (they are reusable components, not top-level screens)."""

    def detect(self, repo_path: Path) -> Optional[list[MenuItem]]:
        repo = Path(repo_path)
        pages = [
            p for p in sorted(list(repo.rglob("*.jsp")) + list(repo.rglob("*.jspx")))
            if not (_SKIP_DIRS & set(p.parts))
        ]
        if not pages:
            return None

        root: dict = {"_dirs": {}, "_pages": []}
        for path in pages:
            rel = path.relative_to(repo)
            node = root
            for seg in rel.parts[:-1]:
                node = node["_dirs"].setdefault(seg, {"_dirs": {}, "_pages": []})
            node["_pages"].append(path)

        def build(node: dict, url_prefix: str) -> list[MenuItem]:
            items: list[MenuItem] = []
            for seg, child in sorted(node["_dirs"].items()):
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(seg),
                    label=_humanize(seg),
                    path=f"{url_prefix}/{seg}",
                    metadata={"is_screen": False},
                    children=build(child, f"{url_prefix}/{seg}"),
                ))
            for path in sorted(node["_pages"]):
                page = extract_jsp_page(path)
                rel = path.relative_to(repo)
                label = (page.title if page and page.title else None) or _humanize(path.stem)
                items.append(MenuItem(
                    id=JsonYamlDetector._slugify(str(rel)),
                    label=label,
                    path=f"{url_prefix}/{path.name}",
                    metadata={
                        "is_screen": True,
                        "component_source": str(rel),
                        "bean_classes": page.bean_classes if page else [],
                        "form_actions": page.form_actions if page else [],
                        "framework": "jsp",
                    },
                ))
            return items

        return build(root, "")
```

In `HybridMenuDetector.__init__`, append `JspMenuDetector()` LAST in `self.detectors` (after `WebFormsMenuDetector()`):
```python
        self.detectors = [
            JsonYamlDetector(),
            TypeScriptConstantDetector(),
            FrameworkRoutingDetector(),
            WebFormsMenuDetector(),
            JspMenuDetector(),
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_jsp_screens.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Full regression**

Run: `python -m pytest -q`
Expected: PASS (entire suite). `JspMenuDetector` is last and returns `None` when no `.jsp` exists, so repos without JSP are unaffected. PASTE the full-suite summary line in the report — it is the acceptance gate.

- [ ] **Step 6: Commit**

```bash
git add src/ai_discovery/menu_detector.py tests/test_jsp_screens.py
git commit -m "feat(jsp): JspMenuDetector — .jsp pages as fallback screens"
```

---

## Self-review notes (addressed)

- **Spec coverage:** title/useBean/form-actions/includes/is_tag_file (T1), `ui_component` node + page→bean edge + domain co-location + wiring (T2), `JspMenuDetector` folder-breadcrumb screens + `.tag` exclusion + fallback precedence + e2e + regression (T3). All spec sections map to a task.
- **Type consistency:** `JspPage` fields and `extract_jsp_page` signature from T1 are used unchanged in T2/T3. `framework_hints` keys (`framework`, `bean_classes`, `form_actions`, `includes`, `title`, `is_tag_file`) and `MenuItem` metadata keys (`is_screen`, `component_source`, `bean_classes`, `form_actions`) are consistent with what `build_screen_map` reads.
- **No placeholders:** every code step is complete and runnable.
- **Dependency note:** relies on `infer_domain`'s path-qn guard + `classify_domains` pre-set-domain honoring + `build_screen_map`'s `is_screen` handling + `_humanize`, all present on the stacked `feat-webforms-parsing` base. `_SKIP_DIRS` is already imported into `menu_detector.py` on that base.
