# Design: ASP.NET WebForms Parsing

**Date:** 2026-06-01
**Status:** Approved — ready for implementation plan
**Origin:** Feature track from `docs/assessments/06-architecture-production-readiness-audit.md` (P1 finding: legacy server-page UIs — JSP, ASP.NET WebForms — are never walked by `file_walker`, so they are invisible to the parser, the call graph, domain docs, and screen-centric mode). This spec covers **ASP.NET WebForms** only; **JSP is a separate follow-up spec**.

## Problem

`file_walker._LANG_EXTENSIONS` recognizes only `.cs/.java/.py/.js/.ts/.tsx/.jsx`. A WebForms app's pages live in `.aspx`/`.ascx` markup files, which are never walked. The code-behind classes (`.aspx.cs`) *are* parsed by `CSharpParser` (event handlers like `Page_Load`/`btnSave_Click` already become method nodes), but nothing links a page to its code-behind, treats a page as a screen, or makes the page visible in the graph. For a WebForms app — which has no JS router or `menu.json` — screen-centric mode therefore produces zero screens, and ASIS/call-graph docs omit the UI layer entirely.

## Goal

Walk `.aspx`/`.ascx`, and:
1. **Graph:** emit a `ui_component` `CodeNode` per page, linked to its code-behind class via real call-graph edges (page → event handler), so legacy pages are visible in domain classification, the call graph, and ASIS/ASD docs, and are queryable.
2. **Screens:** surface each `.aspx` page as a `Screen` (the navigable unit in a WebForms app), with its code-behind class and master page recorded, wired through the existing screen pipeline.

## Non-goals

- **JSP** — separate follow-up spec.
- Enumerating every server-control type as structured fields/data (GridView/Repeater columns, TextBox lists). We extract event wiring and the directive; full control-vocabulary extraction is out of scope.
- Rewriting `ScreenMapper` to be WebForms-aware. The code-behind link rides in `Screen.metadata` and as a real page→handler call edge; `ScreenMapper`'s Spring-only globbing is left unchanged.
- RBAC/authorization from `Web.config <location>` / `<authorization>` — that is the separate RBAC P1 item.
- Following master-page or user-control includes transitively; transitive screen dependency for drift is out of scope here.
- Pipeline-phase, DB-schema, or `Screen`/`MenuItem` model changes.

## Decisions (locked during brainstorming)

1. **Scope:** ASP.NET WebForms (`.aspx`/`.ascx`) only; JSP deferred to its own spec.
2. **Depth:** Graph **and** Screens.
3. **Extraction per page:** Page/Control directive (`Inherits` → code-behind class, `CodeBehind`, `MasterPageFile`, `Title`), server-control event wiring (`<asp:* On<Event>="Handler">`), and master page. Not full control-vocabulary field extraction.
4. **Architecture:** a pure `webforms_extractor` + two thin consumers (`WebFormsParser` for graph nodes, `WebFormsMenuDetector` for screens). Rejected: parser-only (screens run at phase 2, before parse at phase 6, so the screen detector must scan files itself); extending `CSharpParser` to handle `.aspx` (mixes markup into the AST parser).

## Architecture

### Module boundaries

```
webforms_extractor.py   (NEW)   one .aspx/.ascx file -> WebFormsPage. Pure regex; no CodeNode/Screen knowledge.
parsers/webforms.py     (NEW)   WebFormsParser(LanguageParser): WebFormsPage -> graph CodeNodes (+ page->handler calls).
menu_detector.py        (EDIT)  add WebFormsMenuDetector strategy: .aspx set -> folder-tree MenuItems -> Screens.
```

`CSharpParser` already emits the code-behind method nodes; this feature adds only the markup layer and the link. The extractor is the single testable unit feeding both consumers.

### `WebFormsPage` (intermediate representation)

```python
@dataclass
class WebFormsPage:
    file_path: str                 # repo-relative .aspx/.ascx path
    is_user_control: bool          # .ascx (Control directive) vs .aspx (Page directive)
    code_behind_class: str | None  # from Inherits="MyApp.Default" (assembly suffix stripped)
    master_page: str | None        # from MasterPageFile="~/Site.Master"
    title: str | None              # Page directive Title="..." else <title>...</title>
    events: list[dict] = field(default_factory=list)  # [{"control_id": str|None, "event": str, "handler": str}]
```

### `webforms_extractor.py`

`extract_webforms_page(path: Path) -> WebFormsPage | None`
- Reads the file (text, errors ignored). Returns `None` if no `<%@ Page %>` or `<%@ Control %>` directive is found (not a server page) or on read failure.
- **Directive:** match `<%@\s*(?P<kind>Page|Control)\b ... %>` (DOTALL). From the directive body, pull attributes order-independently: `Inherits`, `CodeBehind`, `MasterPageFile`, `Title`. `is_user_control = (kind == "Control")`.
- **`code_behind_class`:** the `Inherits` value, with any assembly suffix stripped (`"MyApp.Default, MyApp"` → `"MyApp.Default"`); `None` if absent.
- **`title`:** `Title=` attribute if present, else first `<title>...</title>` text, else `None`.
- **events:** scan `<asp:...>` tags carrying `runat="server"`; for each, capture `ID="..."` (optional) and every `On(?P<event>[A-Z]\w*)="(?P<handler>\w+)"` attribute → one `{control_id, event, handler}` per handler. The generic `On<Event>` pattern covers `OnClick`/`OnCommand`/`OnRowCommand`/`OnSelectedIndexChanged`/etc. without enumerating control types.

Helpers are pure and return safe defaults; no exception escapes `extract_webforms_page`.

### `parsers/webforms.py` — `WebFormsParser(LanguageParser)`

- `language = "webforms"`, `extensions = frozenset({".aspx", ".ascx"})`.
- `parse_file(path) -> list[CodeNode]`: call `extract_webforms_page`; if `None`, return `[]`. Otherwise emit **one** `CodeNode`:
  - `node_type = "ui_component"`
  - `name` = file stem (`"Default"`)
  - `qualified_name` = the file path as received by `parse_file` (`str(file_path)`) — deliberately a path, not a dotted name, so it is unique and never collides with or shadows the code-behind class node (whose qn is the `Inherits` value). `parse_file` only receives the file path (not the repo root), so we use it verbatim; pages are call *sources*, not targets, so a path-style qn is fine.
  - `calls` = `[f"{code_behind_class}.{handler}" for each event]` when `code_behind_class` is set — these match the `CSharpParser`-produced code-behind method nodes (`Namespace.Class.Handler`), so the existing Stage-1 exact-match resolver creates **page → handler** edges with no new resolver code.
  - `framework_hints` = `{"framework": "webforms", "code_behind_class", "master_page", "title", "is_user_control", "events"}`.
  - `source_code` = the raw file text (consistent with other parsers; released after chunking by the existing P0-3 mechanism).
- Registered by appending `WebFormsParser()` to the parser list in `pipeline.py` (phase 6).

### `menu_detector.py` — `WebFormsMenuDetector(MenuDetector)`

Appended **last** in `HybridMenuDetector.detectors` (after JSON/YAML, TS-const, framework-routing), so a hand-authored menu still wins and WebForms is the fallback for apps with no JS menu.

`detect(repo_path) -> Optional[list[MenuItem]]`:
- Glob `**/*.aspx` under the repo (skip `_SKIP_DIRS`); ignore `.ascx` (partial user controls are components, not top-level screens). If none found, return `None`.
- For each `.aspx`, read it via `webforms_extractor`.
- Build a **folder-grouped `MenuItem` tree** rooted at the common page root:
  - Each intermediate directory → wrapper `MenuItem(metadata={"is_screen": False})` (breadcrumb ancestor).
  - Each `.aspx` → leaf `MenuItem` with `metadata = {"is_screen": True, "component_source": <aspx path>, "code_behind_class": <inherits>, "master_page": <master>, "framework": "webforms"}`, `label` = page `title` or humanized file stem (reuse `_humanize`), `path` = the page's URL path (folder path + filename, leading `/`).
- Return the root MenuItems.

The existing `build_screen_map` (Task 7) then yields `Screen`s: `fe_component` = the `.aspx` path (from `component_source`), `menu_path` = folder breadcrumb, `metadata` carrying `code_behind_class`/`master_page` for the spec to surface the backend link. **No change to `detect_and_build_screens` or `build_screen_map`.**

### Wiring (exact edits)

- `repo/file_walker.py` `_LANG_EXTENSIONS`: add `"webforms": frozenset({".aspx", ".ascx"})`.
- `repo/lang_detector.py`: `_EXT_MAP` add `".aspx": "webforms"`, `".ascx": "webforms"`. Do **not** add a `_MANIFEST_MAP` entry — `Web.config` is too generic a name and extension counting already detects the language reliably.
- `pipeline.py`: import and append `WebFormsParser()` to the `parsers` list.
- `menu_detector.py` `HybridMenuDetector.__init__`: append `WebFormsMenuDetector()`.

## Error handling & edge cases

- `.aspx` with no `Inherits` (inline `<script runat="server">` or pure markup): page node still emitted with `code_behind_class=None` and no handler calls; still becomes a screen.
- Unreadable/non-server-page file: extractor returns `None`; parser emits `[]`; detector skips it. No crash, debug-log only — no `except: pass`.
- `.ascx`: emitted as a graph `ui_component` (visible) but excluded from screens.
- `Inherits` with assembly suffix (`"MyApp.Default, MyApp, Version=..."`): strip at the first comma.
- Duplicate page filenames in different folders: distinct `qualified_name` (full relative path) and distinct screen paths prevent collision; `build_screen_map`'s `unique_id` covers id clashes.
- Master pages / user-control includes are recorded as flat hints, not followed transitively.
- The page node's `qualified_name` is the file path as received by `parse_file` (absolute when `walk_repo` yields absolute paths). This is cosmetically long in `call_edge`/viewer output but keeps the qn unique and never collides with the code-behind class qn. The page's *domain* is set explicitly from the code-behind class (so the page co-locates with its handler) rather than inferred from this path; `infer_domain` skips path-like qns. The code-behind link is carried in `Screen.metadata` and as a real call edge — surfacing it in the screen-spec template is a follow-up (ScreenMapper/template rewrite is out of scope here).

## Test plan

Fixtures under `tests/fixtures/webforms/`:
- `Default.aspx` — `<%@ Page Inherits="MyApp.Default" MasterPageFile="~/Site.Master" Title="Home" %>` + `<asp:Button ID="btnSave" runat="server" OnClick="btnSave_Click" />`.
- `Pages/Customer.aspx` — nested folder, `Title="Customer"`, `Inherits="MyApp.Pages.Customer"`, an `<asp:GridView OnRowCommand="grid_RowCommand">`.
- `Widget.ascx` — `<%@ Control Inherits="MyApp.Widget" %>` (user control).
- `NoCodeBehind.aspx` — `<%@ Page %>` with no `Inherits`, pure markup.
- `notaspage.txt` — sanity: not a server page.

`tests/test_webforms_extractor.py`:
- Directive parsing: `code_behind_class` (incl. assembly-suffix stripping), `master_page`, `title` (attribute and `<title>` fallback).
- Event extraction: `OnClick`/`OnRowCommand` → `{control_id, event, handler}`.
- `.ascx` → `is_user_control=True`.
- `NoCodeBehind.aspx` → `code_behind_class=None`, no events, still a `WebFormsPage`.
- `notaspage.txt` → `None`.

`tests/test_webforms_parser.py`:
- `Default.aspx` → one `ui_component` node, `qualified_name.endswith("Default.aspx")`, `framework_hints["code_behind_class"] == "MyApp.Default"`.
- `calls` contains `"MyApp.Default.btnSave_Click"`.
- Build a graph from the page node + a code-behind method node `CodeNode(qualified_name="MyApp.Default.btnSave_Click", node_type="method")` and assert `build_call_graph` resolves a page→handler edge (resolved_by "exact").
- `.ascx` → `ui_component`, `is_user_control` hint True.

`tests/test_menu_detector_routes.py` (extend):
- `WebFormsMenuDetector.detect` on a tmp repo with `Default.aspx` + `Pages/Customer.aspx` returns MenuItems; `build_screen_map` yields screens whose `menu_path` reflects the folder breadcrumb, `fe_component` is the `.aspx` path, and `metadata["code_behind_class"]` is set; `.ascx` is excluded from screens.
- `HybridMenuDetector` uses WebForms only as a fallback: a repo with both `menu.json` and `.aspx` resolves via `menu.json`.
- `detect_and_build_screens` end-to-end on a WebForms fixture repo.

Full-suite regression must stay green (the new extension must not disturb existing language detection/parsing).

## Acceptance criteria

- `.aspx`/`.ascx` are walked and parsed into `ui_component` nodes.
- A page resolves a call edge to its code-behind event handler.
- `.aspx` pages become `Screen`s (folder breadcrumb, `fe_component` = the page, code-behind class in metadata); `.ascx` excluded from screens.
- WebForms screen detection is a fallback that never overrides a hand-authored menu.
- No regressions; full suite green.
