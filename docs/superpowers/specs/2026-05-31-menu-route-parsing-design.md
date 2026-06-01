# Design: Menu Route-AST Parsing

**Date:** 2026-05-31
**Status:** Approved — ready for implementation plan
**Origin:** Feature track from `docs/assessments/06-architecture-production-readiness-audit.md` (P1 finding: menu detectors for TS-const / Vue / React / Angular are scaffolded but non-functional — `menu_detector.py` `_parse_ts_array` returns `[]`, the framework detectors `pass` → `None`).

## Problem

Screen-centric mode only produces screens from static JSON/YAML menu files today. The detectors for the four common SPA formats — `export const MENU = [...]`, Vue Router, React Router, Angular `RouterModule` — exist as stubs that return nothing. Real apps express their navigable surface in those route/menu configs, so for any SPA without a hand-written `menu.json`, screen generation silently produces zero screens.

## Goal

Make all four formats actually yield screens, via real AST parsing (not regex), reusing the project's existing tree-sitter grammars. A repo with a Vue/React/Angular router or a `MENU` constant should produce a screen per user-facing route, with labels, breadcrumb hierarchy, and a frontend-component pointer.

## Non-goals

- Server-rendered menus (Spring/Thymeleaf/Razor) — separate effort.
- Non-menu screens (popups, wizards, modals, deep-links) — separate effort.
- Cross-referencing a `MENU` constant against route configs to enrich components — noted as a future enhancement, not in this iteration.
- Changes to the pipeline, DB schema, or `Screen`/`MenuItem` models.

## Decisions (locked during brainstorming)

1. **Scope:** all four formats (TS-const, Vue Router, React Router incl. JSX form, Angular `RouterModule`).
2. **What is a screen:** a route is a screen iff it resolves to a real component/element and is not catch-all and not a pure redirect. Pathless/layout wrappers with children become breadcrumb ancestors, not screens. Redirects are recorded as navigation hints (not dropped). Catch-all `*`/404 are dropped.
3. **Label source:** explicit metadata (`meta.title` / `data.title` / `handle.title` / MENU `label`) → humanized component name → path segment.
4. **Parsing approach:** one shared tree-sitter object-literal walker + thin per-framework adapters (Approach A). Rejected: per-framework regex (brittle), reusing `JavaScriptParser` (wrong abstraction — it emits classes/functions, not route arrays).

## Architecture

### Module boundaries

```
route_parser.py   (NEW)             AST -> RouteNode tree. Knows tree-sitter; knows nothing about Screens.
menu_detector.py  (REWRITE stubs)   RouteNode -> MenuItem -> Screen. Orchestration + screen-building rules.
```

`route_parser.py` is the single hard, independently testable unit. The three stub detectors become thin adapters that call it. `JsonYamlDetector`, `MenuItem`, and `Screen` are unchanged.

### RouteNode (intermediate representation)

```python
@dataclass
class RouteNode:
    path: str | None             # raw segment as written: "users", "/users", ":id", ""
    component: str | None        # component identifier, or None (pathless/redirect)
    component_source: str | None # import specifier ("./pages/UserList.vue") when resolvable
    name: str | None             # Vue route.name
    title: str | None            # explicit label: meta.title / data.title / handle.title / MENU label
    roles: list[str]             # meta.roles / data.roles / roles  (carried for future RBAC work)
    redirect_to: str | None      # redirectTo / redirect target
    is_catch_all: bool           # path is "*", "**", "/:pathMatch(.*)*"
    children: list[RouteNode] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # leftover keys -> Screen.metadata
```

### AST extraction (`route_parser.py`)

Public:
- `parse_route_file(path: Path, framework: str) -> RouteNode | None`
  Selects grammar by extension (`.jsx` → JS grammar, `.tsx` → TSX grammar, `.ts`/`.js` → TS/JS), parses, locates the route array via the framework adapter, walks it into a synthetic root `RouteNode` (its `children` are the top-level routes). Any failure → `None` (never raises).

Internal:
- `_collect_imports(tree) -> dict[str, str]` — map each imported identifier to its module specifier, so `component: UserList` or `element={<UserList/>}` resolves to its import source.
- `_array_to_routes(array_node, imports) -> list[RouteNode]`
- `_object_to_route(object_node, imports, field_map) -> RouteNode` — the core walker. Reads canonical keys via the per-framework `field_map`; value resolution by AST node kind:
  - string literal → text;
  - array → recurse into `children`;
  - arrow/`import()` (lazy) → extract the import specifier into `component_source`;
  - identifier → look up in `imports` for `component_source`.
- `_jsx_to_routes(node, imports) -> list[RouteNode]` — React JSX form: locate `<Route>` elements, read `path` + `element`/`Component` attributes, recurse nested `<Route>`. `<Navigate to=…/>` and `index` redirects captured as `redirect_to`.

Guards: recursion depth and array length capped to bound pathological inputs. A missing grammar import is caught and yields `None`.

### Per-framework adapters (entry-point location + field map)

| Framework | Entry point located by | component key(s) | title key | redirect key |
|-----------|------------------------|------------------|-----------|--------------|
| TS-const  | `export const MENU\|NAVIGATION\|ROUTES\|SIDEBAR = [ ]` | (uses `label`; menu items carry no component) | `label` | — |
| Vue Router| `routes:` property array / `createRouter({ routes })` / `const routes = [ ]` | `component` | `meta.title` | `redirect` |
| Angular   | `const routes: Routes = [ ]` / `RouterModule.forRoot([ ])` / `forChild([ ])` | `component` / `loadComponent` / `loadChildren` | `data.title` | `redirectTo` |
| React     | `createBrowserRouter([ ])` / `createHashRouter([ ])` / `useRoutes([ ])` (object form) **or** `<Routes><Route…></Routes>` (JSX form) | `element` / `Component` | `handle.title` | `<Navigate>` / `index` |

`HybridMenuDetector` stays first-match-wins, with order **JSON/YAML → TS-const → framework-routing**. Rationale: an explicit `MENU`/`NAVIGATION` constant is authored *as the menu* (curated labels and hierarchy — the user's mental model), so it is a better entry-point map than raw routes; routes are the fallback when no curated menu exists. This matches the screen-centric philosophy ("users navigate by the menu they see").

### RouteNode → MenuItem → Screen

The RouteNode tree is converted to the existing `MenuItem` tree, carrying through `metadata`: `is_screen` (bool), `component_source`, `redirect_to`, `is_catch_all`, `title`, `roles`. `build_screen_map` then applies:

- **Full path** = join of ancestor `path`s, normalizing leading `/`, relative segments, and preserving `:params`.
- **Two screen rules, by format family.** TS-const `MENU` items are *menu entries* (label + path + children, no component) — exactly like JSON/YAML menus — so they use the **leaf rule** (every leaf is a screen). Route configs (Vue/React/Angular) carry components, so they use the **component-bearing rule** below. The adapter encodes this by which `is_screen` signal it emits (see below).
- **Component-bearing rule (route formats):** a route is a screen iff `component` or `component_source` is present, and not `is_catch_all`, and not a pure redirect. A route with a component *and* children is both a screen and a breadcrumb ancestor.
- **Pathless / component-less wrapper with children** → breadcrumb ancestor only; its `title`/path joins into descendants' `menu_path`; it is not emitted as a screen.
- **Redirects** → collected as `(from_full_path, to)`; not screens. After screens are built, a redirect whose target resolves to a screen's full path adds the source path to that screen's `metadata["redirect_aliases"]`.
- **Catch-all / 404** → dropped.
- **label** = `title` → humanized component name (`CustomerDetailPage` → "Customer Detail") → last non-param path segment.
- **screen_id** = slug of full path (params stripped), else component name; de-duplicated with a numeric suffix on collision.
- **fe_component** = `component_source` (resolved relative to the route file's directory when the specifier is relative), else the component name.
- **menu_path** = ancestor labels + own label.
- **permissions** = `roles`.

**How the rules are selected.** `build_screen_map` uses `metadata["is_screen"]` when present; when absent it falls back to the leaf rule. The adapters set this accordingly:
- **JSON/YAML** (unchanged) and **TS-const MENU**: omit `is_screen` → leaf rule applies (menu entries, no component).
- **Vue / React / Angular** route adapters: set `metadata["is_screen"]` explicitly per the component-bearing rule.

`JsonYamlDetector` behavior is therefore unchanged, and TS-const menus still produce a screen per leaf even though they carry no component.

### Integration

- New module `src/ai_discovery/route_parser.py`.
- Rewrite `TypeScriptConstantDetector._parse_file`/`_parse_ts_array` and `FrameworkRoutingDetector._detect_vue_router` / `_detect_react_router` / `_detect_angular_routing` to call `route_parser.parse_route_file` and convert the returned `RouteNode` tree to `MenuItem`s (a shared `_routenode_to_menuitem` helper in `menu_detector.py`).
- Augment `build_screen_map` with the `is_screen` / breadcrumb / redirect-alias logic above.
- No changes to `pipeline.py`, `cli.py`, the DB schema, or the `Screen`/`MenuItem` dataclasses.

## Error handling & edge cases

- Unparseable file, missing grammar, or unresolvable dynamic component → that route's `component` is `None` (it becomes a breadcrumb if it has children, else is dropped); a file-level failure returns `None` so the detector falls through to the next strategy.
- No `except: pass`. Failures log at debug (consistent with the P1-f change); `menu_detector` already prints a warning on detector failure.
- Recursion depth and array size capped.
- Lazy imports (`() => import('./X.vue')`, `loadComponent: () => import('./x')`, `React.lazy(() => import('./X'))`) → specifier captured as `component_source`.

## Test plan

Fixtures under `tests/fixtures/routes/`:
- `vue-routes.ts` — nested routes, `meta.title`, a pathless layout wrapper, a lazy `component`, a `redirect`.
- `react-jsx.tsx` — `<Routes>`/`<Route>` with `element`, nested routes, `<Navigate>` redirect, a catch-all `path="*"`.
- `react-data-router.tsx` — `createBrowserRouter([...])` object form with `lazy`/`Component` and `handle.title`.
- `angular-routing.module.ts` — `RouterModule.forRoot`, `loadChildren`, `data.title`, `redirectTo`.
- `ts-const-menu.ts` — `export const MENU = [...]` with nested `children`, `label`, `roles`.

`tests/test_route_parser.py`:
- Each fixture → expected screens (full paths, labels, components, nesting).
- Lazy import → `fe_component` captured.
- `meta.title` / `data.title` / `handle.title` / `label` → screen label.
- Pathless wrapper → contributes to `menu_path` but is not a screen.
- Redirect → not a screen; recorded in the target screen's `metadata["redirect_aliases"]`.
- Catch-all `*` → dropped.
- Malformed / non-route file → `None`, no crash.

`tests/test_menu_detector.py` (additions):
- `HybridMenuDetector` selects the correct detector per fixture repo layout and honors the documented precedence.
- `detect_and_build_screens` end-to-end on a fixture repo directory.
- Existing `JsonYamlDetector` tests remain green (backward-compat: the leaf rule still applies when `is_screen` is absent).

## Acceptance criteria

- All four formats produce screens from representative fixtures.
- Component-bearing routes become screens; pathless wrappers become breadcrumbs; redirects become aliases; catch-alls are dropped.
- Labels follow the metadata → component → path precedence.
- `fe_component` is populated from (lazy) import specifiers where resolvable.
- JSON/YAML detection is unchanged; full suite green.
