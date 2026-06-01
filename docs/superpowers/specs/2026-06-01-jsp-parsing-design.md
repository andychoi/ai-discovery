# Design: JSP Parsing

**Date:** 2026-06-01
**Status:** Approved — ready for implementation plan
**Origin:** Feature track from `docs/assessments/06-architecture-production-readiness-audit.md` (P1: legacy server-page UIs are never walked). Follow-up to the ASP.NET WebForms spec (`docs/superpowers/specs/2026-06-01-webforms-parsing-design.md`); this covers **JSP**.

## Problem

`file_walker` recognizes only `.cs/.java/.py/.js/.ts/.tsx/.jsx/.aspx/.ascx`. A JSP app's pages live in `.jsp`/`.jspx` (and reusable tag files `.tag`/`.tagx`), which are never walked. The Java backend classes are parsed by `JavaParser`, but nothing links a JSP page to the beans/actions it uses, treats a page as a screen, or makes the page visible in the graph. For a JSP app — which typically has no JS router or `menu.json` — screen-centric mode produces zero screens, and ASIS/call-graph docs omit the UI layer.

## Goal

Walk `.jsp`/`.jspx`/`.tag`/`.tagx`, and:
1. **Graph:** emit a `ui_component` `CodeNode` per page, linked to its backend bean class via a real call edge (page → `<jsp:useBean class>`), so legacy pages are visible in domain classification, the call graph, and ASIS/ASD docs, and queryable.
2. **Screens:** surface each `.jsp`/`.jspx` page as a `Screen` (the navigable unit), with its bean classes and form actions recorded, via the existing screen pipeline (fallback when no JS menu).

## Non-goals

- `web.xml` servlet-mapping parsing (URL-pattern → servlet-class) — deferred; useBean is the clean exact signal this iteration.
- Auto-resolving `<form action>` URLs to Spring/JAX-RS/Servlet endpoints by path — recorded as URL refs only (path correlation is brittle: context roots, path vars).
- Scriptlet (`<% … %>`) and EL (`${…}`) class/bean reference scanning — brittle and noisy; out of scope.
- `ScreenMapper`/screen-spec-template rewrite — the backend link rides in `Screen.metadata` + the useBean call edge.
- Taglib resolution; deep tag-file (`.tag`) backend analysis (tag files are graph components, not screens).
- Pipeline-phase, DB-schema, or `Screen`/`MenuItem` model changes.

## Decisions (locked during brainstorming)

1. **Backend link:** `<jsp:useBean class="com.x.Foo">` → exact page→class call edge; `<form action>` URLs recorded in page node + screen metadata (not auto-resolved).
2. **Constructs extracted:** useBean class, form actions, includes (`<jsp:include page>` / `<%@ include file>`), and title.
3. **Extensions:** `.jsp`/`.jspx` become screens; `.tag`/`.tagx` (tag files) are graph components, not screens — mirroring `.ascx`.
4. **Architecture:** pure `jsp_extractor` + two thin consumers (`JspParser`, `JspMenuDetector`), mirroring the WebForms feature. Rejected: a shared WebForms+JSP "server-page" core (the dialects/backends diverge too much); scriptlet/web.xml parsing (per non-goals).

## Architecture

### Module boundaries

```
jsp_extractor.py   (NEW)   one .jsp/.jspx/.tag/.tagx -> JspPage. Pure regex; no CodeNode/Screen knowledge.
parsers/jsp.py     (NEW)   JspParser(LanguageParser): JspPage -> ui_component CodeNode (+ useBean call edges).
menu_detector.py   (EDIT)  add JspMenuDetector strategy: .jsp set -> folder-tree MenuItems -> Screens.
```

`JavaParser` already emits the bean class nodes; this feature adds the markup layer and the link. The extractor is the single testable unit feeding both consumers.

### `JspPage` (intermediate representation)

```python
@dataclass
class JspPage:
    file_path: str
    is_tag_file: bool                          # .tag/.tagx — component, not a screen
    title: str | None = None                   # <title>...</title>
    bean_classes: list[str] = field(default_factory=list)   # <jsp:useBean class="com.x.Foo">
    form_actions: list[str] = field(default_factory=list)   # <form action="/login">
    includes: list[str] = field(default_factory=list)       # <jsp:include page=> / <%@ include file=>
```

### `jsp_extractor.py`

`extract_jsp_page(path: Path) -> JspPage | None`
- Reads the file (text, errors ignored). Returns `None` only on read failure. A `.jsp`-family file is always a JSP, so (unlike WebForms) there is no directive gate — a readable file always yields a `JspPage` (possibly with empty lists).
- `is_tag_file` = `path.suffix.lower() in {".tag", ".tagx"}`.
- **title:** first `<title>…</title>` text (DOTALL, case-insensitive), stripped, else `None`.
- **bean_classes:** for each `<jsp:useBean …>` tag, capture `class="(…)"` → the FQN (take the bare class name before any whitespace/`<`; ignore `type=`-only beans with no `class`).
- **form_actions:** for each `<form …>` tag, capture `action="(…)"`; skip empty, `#`, and `javascript:` actions.
- **includes:** `<jsp:include … page="(…)">` and `<%@\s*include … file="(…)">` → included page paths.

All helpers pure and None-safe; no exception escapes `extract_jsp_page`.

### `parsers/jsp.py` — `JspParser(LanguageParser)`

- `language = "jsp"`, `extensions = frozenset({".jsp", ".jspx", ".tag", ".tagx"})`.
- `parse_file(path)`: call `extract_jsp_page`; if `None`, return `[]`. Otherwise emit **one** `CodeNode`:
  - `node_type = "ui_component"`, `name` = file stem, `qualified_name = str(file_path)` (unique path qn; pages are call sources, not targets; never collides with a Java class qn).
  - `calls` = the `bean_classes` list — each is an exact class FQN, matching the `JavaParser`-produced class node (`com.x.Foo`), so the existing Stage-1 exact-match resolver creates **page → bean class** edges with no new resolver code.
  - `domain` = `infer_domain(bean_classes[0], str(path))` when a bean class exists (co-locate the page with its backend), else `None`. Relies on the `infer_domain` path-qn guard + `classify_domains` pre-set-domain honoring added on the WebForms branch.
  - `framework_hints` = `{"framework": "jsp", "bean_classes", "form_actions", "includes", "title", "is_tag_file"}`.
  - `source_code` = raw file text (released after chunking by the existing P0-3 mechanism).
- Registered by appending `JspParser()` to the parser list in `pipeline.py` (phase 6).

### `menu_detector.py` — `JspMenuDetector(MenuDetector)`

Appended **last** in `HybridMenuDetector.detectors` (after JSON/YAML, TS-const, framework-routing, WebForms), so a hand-authored menu still wins; JSP is a fallback for apps with no JS menu. (A repo is realistically WebForms *or* JSP, not both, so the WebForms/JSP ordering is immaterial.)

`detect(repo_path) -> Optional[list[MenuItem]]`:
- Glob `**/*.jsp` + `**/*.jspx` under the repo (skip `_SKIP_DIRS`); ignore `.tag`/`.tagx` (tag files are components, not top-level screens). If none, return `None`.
- For each page, read it via `jsp_extractor`.
- Build a **folder-grouped `MenuItem` tree**: directories → wrapper `MenuItem(metadata={"is_screen": False})`; each page → leaf `MenuItem` with `metadata = {"is_screen": True, "component_source": <rel path>, "bean_classes": [...], "form_actions": [...], "framework": "jsp"}`, `label` = page `title` or humanized file stem (reuse `_humanize`), `path` = the page's URL path (folder path + filename, leading `/`).

The existing `build_screen_map` then yields `Screen`s: `fe_component` = the `.jsp`, `menu_path` = folder breadcrumb, `metadata` carrying `bean_classes`/`form_actions`. **No change to `detect_and_build_screens` or `build_screen_map`.**

### Wiring (exact edits)

- `repo/file_walker.py` `_LANG_EXTENSIONS`: add `"jsp": frozenset({".jsp", ".jspx", ".tag", ".tagx"})`.
- `repo/lang_detector.py` `_EXT_MAP`: add `".jsp"`, `".jspx"`, `".tag"`, `".tagx"` → `"jsp"`.
- `pipeline.py`: import and append `JspParser()` to the `parsers` list.
- `menu_detector.py` `HybridMenuDetector.__init__`: append `JspMenuDetector()`.

## Error handling & edge cases

- Unreadable file → `None`; parser emits `[]`; detector skips it. No crash, debug-log only.
- Page with no useBean → page node still emitted with no bean-class calls (still a screen).
- `.tag`/`.tagx` → graph `ui_component` (visible) but excluded from screens.
- Non-literal / `#` / `javascript:` form actions → skipped.
- `<jsp:useBean>` with `type=` but no `class=` (interface-only bean) → no class edge (we only link concrete `class=`).
- Duplicate page filenames in different folders → distinct `qualified_name` (full path) and screen paths; `build_screen_map`'s `unique_id` covers id clashes.
- Includes recorded as flat hints, not followed transitively.

## Test plan

Fixtures under `tests/fixtures/jsp/`:
- `login.jsp` — `<jsp:useBean id="user" class="com.app.UserBean" />`, `<form action="/doLogin" method="post">`, `<title>Login</title>`.
- `customer/list.jsp` — nested folder, `<jsp:include page="header.jsp" />`, `<jsp:useBean id="c" class="com.app.CustomerService" />`, `<title>Customers</title>`.
- `widget.tag` — a tag file (component).
- `plain.jsp` — no beans/forms/title.

`tests/test_jsp_extractor.py`:
- title; bean_classes; form_actions (incl. skipping `#`/`javascript:`); includes; `is_tag_file` for `.tag`; `plain.jsp` → empty lists; unreadable/missing → None.

`tests/test_jsp_parser.py`:
- `login.jsp` → one `ui_component`, `qualified_name.endswith("login.jsp")`, `framework_hints["bean_classes"] == ["com.app.UserBean"]`, `framework_hints["framework"] == "jsp"`, `calls` contains `"com.app.UserBean"`.
- Build a graph with the page node + a Java class node `CodeNode(qualified_name="com.app.UserBean", node_type="class")` and assert `build_call_graph` resolves a page→bean edge (resolved_by "exact").
- Domain co-location: page + a Java node in `com.app` share a domain (no path-singleton).
- `.tag` → `ui_component` with `is_tag_file` True.

`tests/test_jsp_screens.py`:
- `JspMenuDetector.detect` on a tmp repo with `login.jsp` + `customer/list.jsp` returns MenuItems; `build_screen_map` yields screens whose `menu_path` reflects the folder breadcrumb, `fe_component` is the `.jsp`, and `metadata["bean_classes"]`/`["form_actions"]` are set; `.tag` excluded from screens.
- `HybridMenuDetector` uses JSP only as a fallback: a repo with both `menu.json` and `.jsp` resolves via `menu.json`.
- `detect_and_build_screens` end-to-end on a JSP fixture repo.

Full-suite regression must stay green.

## Acceptance criteria

- `.jsp`/`.jspx`/`.tag`/`.tagx` are walked and parsed into `ui_component` nodes.
- A page resolves a call edge to its `<jsp:useBean>` backend class.
- `.jsp`/`.jspx` pages become `Screen`s (folder breadcrumb, `fe_component` = the page, bean classes + form actions in metadata); `.tag`/`.tagx` excluded from screens.
- JSP screen detection is a fallback that never overrides a hand-authored menu.
- Page nodes co-locate with their bean class's domain (no path-singleton domains).
- No regressions; full suite green.
