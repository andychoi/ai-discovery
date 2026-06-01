# Menu Route-AST Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the four stubbed menu/route formats (TS-const `MENU`, Vue Router, React Router incl. JSX, Angular `RouterModule`) actually produce screens via real tree-sitter AST parsing.

**Architecture:** A new `route_parser.py` turns route/menu source into a generic `RouteNode` tree using the project's existing tree-sitter JS/TS grammars; `menu_detector.py`'s stub detectors become thin adapters that call it and convert `RouteNode` → `MenuItem`, and `build_screen_map` gains format-aware screen rules (component-bearing for routes, leaf for menus).

**Tech Stack:** Python 3.10+, `tree_sitter`, `tree_sitter_javascript`, `tree_sitter_typescript`, pytest.

**Spec:** `docs/superpowers/specs/2026-05-31-menu-route-parsing-design.md`

---

## File Structure

- **Create** `src/ai_discovery/route_parser.py` — `RouteNode`, grammar selection, string/import helpers, the object-literal walker, the JSX walker, per-framework adapters, and `parse_route_file`.
- **Modify** `src/ai_discovery/menu_detector.py` — rewrite `TypeScriptConstantDetector` and `FrameworkRoutingDetector` to call `route_parser`; add `_routenode_to_menuitem`; augment `build_screen_map` with the format-aware screen rules.
- **Create** `tests/fixtures/routes/{ts-const-menu.ts, vue-routes.ts, angular-routing.module.ts, react-data-router.tsx, react-jsx.tsx}` — fixtures.
- **Create** `tests/test_route_parser.py` — unit tests for the parser.
- **Create** `tests/test_menu_detector_routes.py` — detector + `build_screen_map` + end-to-end tests.

Canonical types/signatures used throughout (defined in Task 1, referenced later):

```python
@dataclass
class RouteNode:
    path: str | None = None
    component: str | None = None
    component_source: str | None = None
    name: str | None = None
    title: str | None = None
    roles: list[str] = field(default_factory=list)
    redirect_to: str | None = None
    is_catch_all: bool = False
    children: list["RouteNode"] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

# helpers
_str_value(node) -> str | None
_grammar_for_ext(path: Path) -> "Language | None"
_collect_imports(root) -> dict[str, str]
_lookup(obj_node, dotted_key, imports) -> "Node | None"          # descends nested object literals
_array_to_routes(array_node, field_map, imports) -> list[RouteNode]
_object_to_route(object_node, field_map, imports) -> RouteNode
_jsx_to_routes(node, imports) -> list[RouteNode]
parse_route_file(path: Path, framework: str) -> RouteNode | None  # synthetic root; .children are top-level routes

# menu_detector
_routenode_to_menuitem(rn: RouteNode, is_route_format: bool) -> MenuItem
```

`FIELD_MAPS: dict[str, dict[str, list[str]]]` maps framework → canonical field → ordered source keys:

```python
FIELD_MAPS = {
    "vue":     {"path": ["path"], "component": ["component"], "title": ["meta.title", "name"],
                "name": ["name"], "redirect": ["redirect"], "roles": ["meta.roles"], "children": ["children"]},
    "angular": {"path": ["path"], "component": ["component", "loadComponent", "loadChildren"],
                "title": ["data.title"], "redirect": ["redirectTo"], "roles": ["data.roles"], "children": ["children"]},
    "ts-const":{"path": ["path", "route", "to"], "title": ["label", "name", "title"],
                "roles": ["roles", "permissions"], "children": ["children", "submenu"]},
}
```

---

## Task 1: Module scaffold — RouteNode, grammar selection, string + import helpers

**Files:**
- Create: `src/ai_discovery/route_parser.py`
- Test: `tests/test_route_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_route_parser.py
from __future__ import annotations
from pathlib import Path
import pytest
from ai_discovery import route_parser as rp
from ai_discovery.route_parser import RouteNode


def _parse_src(src: bytes, ext: str = ".ts"):
    """Parse a raw source string with the right grammar and return the root node."""
    lang = rp._grammar_for_ext(Path(f"x{ext}"))
    from tree_sitter import Parser
    return Parser(lang).parse(src).root_node


def test_routenode_defaults():
    rn = RouteNode()
    assert rn.path is None and rn.children == [] and rn.roles == [] and rn.is_catch_all is False


def test_grammar_for_ext_selects_grammar():
    assert rp._grammar_for_ext(Path("a.ts")) is not None
    assert rp._grammar_for_ext(Path("a.tsx")) is not None
    assert rp._grammar_for_ext(Path("a.jsx")) is not None
    assert rp._grammar_for_ext(Path("a.js")) is not None
    assert rp._grammar_for_ext(Path("a.txt")) is None


def test_str_value_strips_quotes():
    root = _parse_src(b"const x = '/users'")
    # find the string node
    def find(n, t):
        if n.type == t: return n
        for c in n.children:
            r = find(c, t)
            if r: return r
    s = find(root, "string")
    assert rp._str_value(s) == "/users"


def test_collect_imports_maps_identifier_to_specifier():
    root = _parse_src(b"import UserList from './pages/UserList.vue'\nimport {A,B} from './ab'")
    imports = rp._collect_imports(root)
    assert imports["UserList"] == "./pages/UserList.vue"
    assert imports["A"] == "./ab"
    assert imports["B"] == "./ab"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_discovery.route_parser`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_discovery/route_parser.py
"""Parse SPA route/menu source into a generic RouteNode tree (tree-sitter).

Knows nothing about Screens — menu_detector converts RouteNode -> MenuItem.
Every public failure path returns None / [] rather than raising.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tree_sitter_javascript as tsjs
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser
    _JS = Language(tsjs.language())
    _TS = Language(tsts.language_typescript())
    _TSX = Language(tsts.language_tsx())
    _TS_AVAILABLE = True
except Exception:  # pragma: no cover - environment without grammars
    _TS_AVAILABLE = False

logger = logging.getLogger(__name__)

_MAX_DEPTH = 25  # guard against pathological nesting


@dataclass
class RouteNode:
    path: str | None = None
    component: str | None = None
    component_source: str | None = None
    name: str | None = None
    title: str | None = None
    roles: list[str] = field(default_factory=list)
    redirect_to: str | None = None
    is_catch_all: bool = False
    children: list["RouteNode"] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _grammar_for_ext(path: Path):
    if not _TS_AVAILABLE:
        return None
    suffix = path.suffix.lower()
    if suffix == ".tsx":
        return _TSX
    if suffix == ".jsx":
        return _JS  # JS grammar parses JSX
    if suffix == ".ts":
        return _TS
    if suffix in (".js", ".mjs", ".cjs"):
        return _JS
    return None


def _str_value(node) -> str | None:
    """Return the text of a string node without its quote delimiters."""
    if node is None:
        return None
    if node.type in ("string", "template_string"):
        for c in node.named_children:
            if c.type in ("string_fragment", "template_substitution"):
                return c.text.decode("utf-8", "ignore")
        # empty string '' has no fragment child
        text = node.text.decode("utf-8", "ignore")
        return text[1:-1] if len(text) >= 2 else ""
    return node.text.decode("utf-8", "ignore")


def _collect_imports(root) -> dict[str, str]:
    """Map each imported identifier to its module specifier."""
    imports: dict[str, str] = {}
    for node in _iter(root):
        if node.type != "import_statement":
            continue
        src = node.child_by_field_name("source")
        specifier = _str_value(src) if src is not None else None
        if not specifier:
            continue
        for ident in _iter(node):
            if ident.type == "identifier":
                imports[ident.text.decode("utf-8", "ignore")] = specifier
    return imports


def _iter(node):
    """Depth-first iterator over all descendant nodes (incl. node itself)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_discovery/route_parser.py tests/test_route_parser.py
git commit -m "feat(route-parser): module scaffold + grammar/string/import helpers"
```

---

## Task 2: Object-literal walker (`_object_to_route`, `_array_to_routes`, `_lookup`)

**Files:**
- Modify: `src/ai_discovery/route_parser.py`
- Test: `tests/test_route_parser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_array_to_routes_ts_const_menu():
    src = (b"const MENU=[{path:'/users',label:'Users',roles:['admin'],"
           b"children:[{path:'/users/:id',label:'Detail'}]}]")
    root = _parse_src(src)
    # locate the array node
    def find(n, t):
        if n.type == t: return n
        for c in n.children:
            r = find(c, t)
            if r: return r
    arr = find(root, "array")
    routes = rp._array_to_routes(arr, rp.FIELD_MAPS["ts-const"], {})
    assert len(routes) == 1
    top = routes[0]
    assert top.path == "/users"
    assert top.title == "Users"
    assert top.roles == ["admin"]
    assert len(top.children) == 1
    assert top.children[0].path == "/users/:id"
    assert top.children[0].title == "Detail"


def test_object_to_route_detects_catch_all():
    src = b"const R=[{path:'*',label:'NotFound'}]"
    root = _parse_src(src)
    def find(n, t):
        if n.type == t: return n
        for c in n.children:
            r = find(c, t)
            if r: return r
    routes = rp._array_to_routes(find(root, "array"), rp.FIELD_MAPS["ts-const"], {})
    assert routes[0].is_catch_all is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q -k "array_to_routes or catch_all"`
Expected: FAIL — `AttributeError: module ... has no attribute 'FIELD_MAPS'`.

- [ ] **Step 3: Write minimal implementation**

Append to `route_parser.py`:

```python
FIELD_MAPS: dict[str, dict[str, list[str]]] = {
    "vue":      {"path": ["path"], "component": ["component"], "title": ["meta.title", "name"],
                 "name": ["name"], "redirect": ["redirect"], "roles": ["meta.roles"], "children": ["children"]},
    "angular":  {"path": ["path"], "component": ["component", "loadComponent", "loadChildren"],
                 "title": ["data.title"], "redirect": ["redirectTo"], "roles": ["data.roles"], "children": ["children"]},
    "ts-const": {"path": ["path", "route", "to"], "title": ["label", "name", "title"],
                 "roles": ["roles", "permissions"], "children": ["children", "submenu"]},
}

_CATCH_ALL = {"*", "**"}


def _object_pairs(object_node) -> dict[str, "object"]:
    """Map each key string -> value node for an `object` AST node."""
    pairs: dict[str, object] = {}
    for child in object_node.named_children:
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        val = child.child_by_field_name("value")
        if key is None or val is None:
            continue
        key_name = _str_value(key) if key.type in ("string", "template_string") else key.text.decode("utf-8", "ignore")
        pairs[key_name] = val
    return pairs


def _lookup(pairs: dict, dotted_key: str):
    """Resolve 'meta.title' by descending one level into nested object literals."""
    parts = dotted_key.split(".")
    val = pairs.get(parts[0])
    for part in parts[1:]:
        if val is None or val.type != "object":
            return None
        val = _object_pairs(val).get(part)
    return val


def _first(pairs: dict, keys: list[str]):
    for k in keys:
        v = _lookup(pairs, k) if "." in k else pairs.get(k)
        if v is not None:
            return v
    return None


def _string_list(node) -> list[str]:
    if node is None or node.type != "array":
        return []
    return [_str_value(c) for c in node.named_children if c.type in ("string", "template_string")]


def _array_to_routes(array_node, field_map, imports, depth=0) -> list[RouteNode]:
    if array_node is None or array_node.type != "array" or depth > _MAX_DEPTH:
        return []
    return [
        _object_to_route(obj, field_map, imports, depth)
        for obj in array_node.named_children
        if obj.type == "object"
    ]


def _object_to_route(object_node, field_map, imports, depth=0) -> RouteNode:
    pairs = _object_pairs(object_node)
    rn = RouteNode()
    path_node = _first(pairs, field_map.get("path", []))
    rn.path = _str_value(path_node) if path_node is not None else None
    if rn.path in _CATCH_ALL or (rn.path and "pathMatch(.*)" in rn.path):
        rn.is_catch_all = True
    title_node = _first(pairs, field_map.get("title", []))
    rn.title = _str_value(title_node) if title_node is not None else None
    name_node = _first(pairs, field_map.get("name", []))
    rn.name = _str_value(name_node) if name_node is not None else None
    redirect_node = _first(pairs, field_map.get("redirect", []))
    rn.redirect_to = _str_value(redirect_node) if redirect_node is not None else None
    rn.roles = _string_list(_first(pairs, field_map.get("roles", [])))
    children_node = _first(pairs, field_map.get("children", []))
    rn.children = _array_to_routes(children_node, field_map, imports, depth + 1)
    # component resolution added in Task 3
    return rn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q`
Expected: PASS (all prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/ai_discovery/route_parser.py tests/test_route_parser.py
git commit -m "feat(route-parser): object-literal walker + field maps"
```

---

## Task 3: Component resolution (identifier import + lazy `import()`)

**Files:**
- Modify: `src/ai_discovery/route_parser.py`
- Test: `tests/test_route_parser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_component_resolves_identifier_to_import_source():
    src = (b"import UserList from './pages/UserList.vue'\n"
           b"const R=[{path:'/u',component:UserList}]")
    root = _parse_src(src)
    imports = rp._collect_imports(root)
    def find(n, t):
        if n.type == t: return n
        for c in n.children:
            r = find(c, t)
            if r: return r
    routes = rp._array_to_routes(find(root, "array"), rp.FIELD_MAPS["vue"], imports)
    assert routes[0].component == "UserList"
    assert routes[0].component_source == "./pages/UserList.vue"


def test_component_resolves_lazy_import():
    src = b"const R=[{path:'/u',component:() => import('./pages/UserList.vue')}]"
    root = _parse_src(src)
    def find(n, t):
        if n.type == t: return n
        for c in n.children:
            r = find(c, t)
            if r: return r
    routes = rp._array_to_routes(find(root, "array"), rp.FIELD_MAPS["vue"], {})
    assert routes[0].component_source == "./pages/UserList.vue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q -k component`
Expected: FAIL — `component`/`component_source` are None (resolution not implemented).

- [ ] **Step 3: Write minimal implementation**

In `route_parser.py`, add a resolver and call it from `_object_to_route` (insert before `return rn`):

```python
def _resolve_component(value_node, imports) -> tuple[str | None, str | None]:
    """Return (component_name, component_source) from a component/element/loadChildren value."""
    if value_node is None:
        return None, None
    # lazy: () => import('x')  or  loadChildren: () => import('x')
    for n in _iter(value_node):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.text.decode("utf-8", "ignore") in ("import", "React.lazy", "lazy"):
                args = n.child_by_field_name("arguments")
                if args is not None:
                    for a in args.named_children:
                        spec = _str_value(a)
                        if spec:
                            return None, spec
    # bare identifier -> look up its import
    if value_node.type == "identifier":
        name = value_node.text.decode("utf-8", "ignore")
        return name, imports.get(name)
    return None, None
```

Then in `_object_to_route`, before `return rn`:

```python
    comp_node = _first(pairs, field_map.get("component", []))
    rn.component, rn.component_source = _resolve_component(comp_node, imports)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_discovery/route_parser.py tests/test_route_parser.py
git commit -m "feat(route-parser): resolve component identifiers and lazy imports"
```

---

## Task 4: Framework adapters + `parse_route_file` (Vue / Angular / TS-const / React data-router)

**Files:**
- Modify: `src/ai_discovery/route_parser.py`
- Create: `tests/fixtures/routes/{ts-const-menu.ts, vue-routes.ts, angular-routing.module.ts, react-data-router.tsx}`
- Test: `tests/test_route_parser.py`

- [ ] **Step 1: Create fixtures**

`tests/fixtures/routes/ts-const-menu.ts`:
```typescript
export const MENU = [
  { path: '/dashboard', label: 'Dashboard', roles: ['user'] },
  { path: '/admin', label: 'Admin', children: [
    { path: '/admin/users', label: 'Users' },
    { path: '/admin/roles', label: 'Roles' },
  ]},
];
```

`tests/fixtures/routes/vue-routes.ts`:
```typescript
import Layout from './Layout.vue';
const routes = [
  { path: '/', component: Layout, children: [
    { path: 'customers', name: 'customers', component: () => import('./pages/CustomerList.vue'),
      meta: { title: 'Customers', roles: ['sales'] } },
    { path: 'customers/:id', component: () => import('./pages/CustomerDetail.vue') },
  ]},
  { path: '/old', redirect: '/customers' },
  { path: '/:pathMatch(.*)*', component: () => import('./pages/NotFound.vue') },
];
export default routes;
```

`tests/fixtures/routes/angular-routing.module.ts`:
```typescript
import { CustomerListComponent } from './customer-list.component';
const routes: Routes = [
  { path: 'customers', component: CustomerListComponent, data: { title: 'Customers' } },
  { path: 'orders', loadChildren: () => import('./orders/orders.module') },
  { path: '', redirectTo: 'customers', pathMatch: 'full' },
];
```

`tests/fixtures/routes/react-data-router.tsx`:
```tsx
import { CustomerList } from './CustomerList';
const router = createBrowserRouter([
  { path: '/customers', element: <CustomerList />, handle: { title: 'Customers' } },
  { path: '/orders', lazy: () => import('./Orders') },
]);
```

- [ ] **Step 2: Write the failing test**

```python
FIX = Path(__file__).parent / "fixtures" / "routes"


def test_parse_ts_const_menu():
    root = rp.parse_route_file(FIX / "ts-const-menu.ts", "ts-const")
    assert root is not None
    assert {c.path for c in root.children} == {"/dashboard", "/admin"}
    admin = next(c for c in root.children if c.path == "/admin")
    assert {c.title for c in admin.children} == {"Users", "Roles"}


def test_parse_vue_routes():
    root = rp.parse_route_file(FIX / "vue-routes.ts", "vue")
    assert root is not None
    top = {c.path: c for c in root.children}
    assert top["/"].component == "Layout"
    cust = next(c for c in top["/"].children if c.path == "customers")
    assert cust.title == "Customers" and cust.roles == ["sales"]
    assert cust.component_source == "./pages/CustomerList.vue"
    assert top["/old"].redirect_to == "/customers"
    assert any(c.is_catch_all for c in root.children)


def test_parse_angular_routes():
    root = rp.parse_route_file(FIX / "angular-routing.module.ts", "angular")
    assert root is not None
    paths = {c.path for c in root.children}
    assert "customers" in paths and "orders" in paths
    orders = next(c for c in root.children if c.path == "orders")
    assert orders.component_source == "./orders/orders.module"


def test_parse_react_data_router():
    root = rp.parse_route_file(FIX / "react-data-router.tsx", "react")
    assert root is not None
    cust = next(c for c in root.children if c.path == "/customers")
    assert cust.title == "Customers"
    assert cust.component == "CustomerList"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q -k parse_`
Expected: FAIL — `parse_route_file` not implemented.

- [ ] **Step 4: Write minimal implementation**

Append to `route_parser.py`. Add `element` to the react field map and the entry-point locator:

```python
FIELD_MAPS["react"] = {
    "path": ["path"], "component": ["element", "Component", "lazy"],
    "title": ["handle.title"], "children": ["children"], "redirect": [],
}


def _find_named_array(root, var_names: set[str]):
    """Find an array assigned to a `const <name> = [...]` / `<name>: [...]`."""
    for n in _iter(root):
        if n.type == "variable_declarator":
            name = n.child_by_field_name("name")
            val = n.child_by_field_name("value")
            if name is not None and val is not None and val.type == "array" \
               and name.text.decode("utf-8", "ignore") in var_names:
                return val
        if n.type == "pair":
            key = n.child_by_field_name("key")
            val = n.child_by_field_name("value")
            if key is not None and val is not None and val.type == "array" \
               and key.text.decode("utf-8", "ignore").strip("'\"") in var_names:
                return val
    return None


def _find_call_array(root, fn_names: set[str]):
    """Find the first array argument of a call like createBrowserRouter([...])."""
    for n in _iter(root):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is None:
                continue
            fn_text = fn.text.decode("utf-8", "ignore").split(".")[-1]
            if fn_text in fn_names:
                args = n.child_by_field_name("arguments")
                if args is not None:
                    for a in args.named_children:
                        if a.type == "array":
                            return a
    return None


def parse_route_file(path: Path, framework: str) -> RouteNode | None:
    lang = _grammar_for_ext(path)
    if lang is None:
        return None
    try:
        source = path.read_bytes()
        root = Parser(lang).parse(source).root_node
    except Exception:
        logger.debug("route_parser: failed to read/parse %s", path)
        return None

    imports = _collect_imports(root)
    field_map = FIELD_MAPS.get(framework, FIELD_MAPS["ts-const"])
    array = None
    if framework == "ts-const":
        array = _find_named_array(root, {"MENU", "NAVIGATION", "ROUTES", "SIDEBAR", "menu", "navigation"})
    elif framework == "vue":
        array = _find_named_array(root, {"routes"}) or _find_call_array(root, {"createRouter"})
    elif framework == "angular":
        array = _find_named_array(root, {"routes"}) or _find_call_array(root, {"forRoot", "forChild"})
    elif framework == "react":
        array = _find_call_array(root, {"createBrowserRouter", "createHashRouter", "useRoutes"})
        if array is None:
            jsx_routes = _jsx_to_routes(root, imports)  # Task 5
            if jsx_routes:
                return RouteNode(children=jsx_routes)

    if array is None:
        return None
    return RouteNode(children=_array_to_routes(array, field_map, imports))
```

Add a temporary stub so this task's tests don't import-error before Task 5:

```python
def _jsx_to_routes(node, imports) -> list[RouteNode]:
    return []  # implemented in Task 5
```

- [ ] **Step 5: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_discovery/route_parser.py tests/fixtures/routes tests/test_route_parser.py
git commit -m "feat(route-parser): framework adapters + parse_route_file (vue/angular/ts-const/react config)"
```

---

## Task 5: React JSX walker (`_jsx_to_routes`)

**Files:**
- Modify: `src/ai_discovery/route_parser.py` (replace the Task 4 stub)
- Create: `tests/fixtures/routes/react-jsx.tsx`
- Test: `tests/test_route_parser.py`

- [ ] **Step 1: Create fixture**

`tests/fixtures/routes/react-jsx.tsx`:
```tsx
import { CustomerList } from './CustomerList';
import { CustomerDetail } from './CustomerDetail';
function App() {
  return (
    <Routes>
      <Route path="/customers" element={<CustomerList />}>
        <Route path=":id" element={<CustomerDetail />} />
      </Route>
      <Route path="/old" element={<Navigate to="/customers" />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
```

- [ ] **Step 2: Write the failing test**

```python
def test_parse_react_jsx():
    root = rp.parse_route_file(FIX / "react-jsx.tsx", "react")
    assert root is not None
    top = {c.path: c for c in root.children}
    assert "/customers" in top
    cust = top["/customers"]
    assert cust.component == "CustomerList"
    assert cust.component_source == "./CustomerList"
    assert any(ch.path == ":id" and ch.component == "CustomerDetail" for ch in cust.children)
    assert any(c.is_catch_all for c in root.children)
    old = top.get("/old")
    assert old is not None and old.redirect_to == "/customers"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q -k react_jsx`
Expected: FAIL — JSX returns `[]` (stub).

- [ ] **Step 4: Replace the stub with the real implementation**

```python
def _jsx_attr(open_node, name: str):
    """Return the value node of a JSX attribute `name=...` on an opening/self-closing element."""
    for attr in open_node.children:
        if attr.type != "jsx_attribute":
            continue
        attr_name = attr.children[0]
        if attr_name.text.decode("utf-8", "ignore") != name:
            continue
        # value is the node after '='
        for c in attr.children[1:]:
            if c.type in ("string",):
                return c
            if c.type == "jsx_expression":
                for inner in c.named_children:
                    return inner
    return None


def _jsx_component(element_value):
    """From an element={<Foo .../>} value, return (name, redirect_to)."""
    if element_value is None:
        return None, None
    name = None
    redirect_to = None
    for n in _iter(element_value):
        if n.type in ("jsx_self_closing_element", "jsx_opening_element"):
            ident = n.child_by_field_name("name")
            if ident is not None:
                name = ident.text.decode("utf-8", "ignore")
                if name == "Navigate":
                    to = _jsx_attr(n, "to")
                    redirect_to = _str_value(to) if to is not None else None
            break
    return name, redirect_to


def _route_elements(node):
    """Yield <Route> elements (both forms) that are direct route declarations."""
    for n in _iter(node):
        if n.type == "jsx_self_closing_element":
            ident = n.child_by_field_name("name")
            if ident is not None and ident.text.decode("utf-8", "ignore") == "Route":
                yield n
        elif n.type == "jsx_element":
            open_el = n.children[0]
            ident = open_el.child_by_field_name("name")
            if ident is not None and ident.text.decode("utf-8", "ignore") == "Route":
                yield n


def _jsx_route_to_node(element, imports) -> RouteNode:
    open_el = element if element.type == "jsx_self_closing_element" else element.children[0]
    rn = RouteNode()
    path_node = _jsx_attr(open_el, "path")
    rn.path = _str_value(path_node) if path_node is not None else None
    if rn.path in _CATCH_ALL:
        rn.is_catch_all = True
    element_val = _jsx_attr(open_el, "element") or _jsx_attr(open_el, "Component")
    name, redirect_to = _jsx_component(element_val)
    rn.component = name
    rn.component_source = imports.get(name) if name else None
    rn.redirect_to = redirect_to
    if redirect_to:
        rn.component = None  # a <Navigate> is a redirect, not a screen component
        rn.component_source = None
    # nested <Route> children (only for jsx_element, which has a closing tag)
    if element.type == "jsx_element":
        for child in element.children:
            if child.type in ("jsx_element", "jsx_self_closing_element"):
                ci = child if child.type == "jsx_self_closing_element" else child.children[0]
                cname = ci.child_by_field_name("name")
                if cname is not None and cname.text.decode("utf-8", "ignore") == "Route":
                    rn.children.append(_jsx_route_to_node(child, imports))
    return rn


def _jsx_to_routes(node, imports) -> list[RouteNode]:
    # Find the top-level <Routes> (or <Switch>) container, then its direct <Route> children.
    for n in _iter(node):
        if n.type == "jsx_element":
            open_el = n.children[0]
            ident = open_el.child_by_field_name("name")
            if ident is not None and ident.text.decode("utf-8", "ignore") in ("Routes", "Switch"):
                routes = []
                for child in n.children:
                    if child.type in ("jsx_element", "jsx_self_closing_element"):
                        ci = child if child.type == "jsx_self_closing_element" else child.children[0]
                        cname = ci.child_by_field_name("name")
                        if cname is not None and cname.text.decode("utf-8", "ignore") == "Route":
                            routes.append(_jsx_route_to_node(child, imports))
                if routes:
                    return routes
    return []
```

- [ ] **Step 5: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_route_parser.py -q`
Expected: PASS (all parser tests).

- [ ] **Step 6: Commit**

```bash
git add src/ai_discovery/route_parser.py tests/fixtures/routes/react-jsx.tsx tests/test_route_parser.py
git commit -m "feat(route-parser): React JSX <Route> walker"
```

---

## Task 6: menu_detector integration — `_routenode_to_menuitem` + detector rewrites

**Files:**
- Modify: `src/ai_discovery/menu_detector.py`
- Test: `tests/test_menu_detector_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_menu_detector_routes.py
from __future__ import annotations
from pathlib import Path
from ai_discovery import menu_detector as md
from ai_discovery.route_parser import RouteNode

FIX = Path(__file__).parent / "fixtures" / "routes"


def test_routenode_to_menuitem_sets_route_metadata():
    rn = RouteNode(path="/c", component="CustomerList", component_source="./C.vue", title="Customers")
    item = md._routenode_to_menuitem(rn, is_route_format=True)
    assert item.label == "Customers"
    assert item.path == "/c"
    assert item.metadata["is_screen"] is True
    assert item.metadata["component_source"] == "./C.vue"


def test_routenode_to_menuitem_wrapper_is_not_screen():
    rn = RouteNode(path="/", component=None, children=[RouteNode(path="x", component="X")])
    item = md._routenode_to_menuitem(rn, is_route_format=True)
    assert item.metadata["is_screen"] is False  # no component + has children -> breadcrumb


def test_ts_const_detector_parses_fixture(tmp_path):
    # lay the fixture into a repo structure the detector searches
    src = (tmp_path / "src"); src.mkdir()
    (src / "menu.ts").write_text((FIX / "ts-const-menu.ts").read_text())
    items = md.TypeScriptConstantDetector().detect(tmp_path)
    assert items is not None
    assert {i.label for i in items} == {"Dashboard", "Admin"}


def test_vue_detector_parses_fixture(tmp_path):
    router = (tmp_path / "src" / "router"); router.mkdir(parents=True)
    (router / "index.ts").write_text((FIX / "vue-routes.ts").read_text())
    items = md.FrameworkRoutingDetector().detect(tmp_path)
    assert items is not None
    assert any(i.path == "/" for i in items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_menu_detector_routes.py -q`
Expected: FAIL — `_routenode_to_menuitem` missing; detectors return None.

- [ ] **Step 3: Write minimal implementation**

In `menu_detector.py`, add a logger and the converter near the top (after imports):

```python
import logging
from .route_parser import parse_route_file, RouteNode

logger = logging.getLogger(__name__)


def _humanize(name: str) -> str:
    import re
    s = re.sub(r"(Page|View|Screen|Component)$", "", name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = s.replace("-", " ").replace("_", " ")
    return s.strip().title() or name


def _routenode_to_menuitem(rn: RouteNode, is_route_format: bool) -> MenuItem:
    label = rn.title or (_humanize(rn.component) if rn.component else None) \
        or (rn.path.strip("/").split("/")[-1].replace(":", "") if rn.path else "") or "Untitled"
    metadata = dict(rn.raw)
    metadata["component_source"] = rn.component_source
    metadata["redirect_to"] = rn.redirect_to
    metadata["is_catch_all"] = rn.is_catch_all
    if is_route_format:
        has_component = bool(rn.component or rn.component_source)
        metadata["is_screen"] = bool(has_component and not rn.is_catch_all and not rn.redirect_to)
    children = [_routenode_to_menuitem(c, is_route_format) for c in rn.children]
    return MenuItem(
        id=JsonYamlDetector._slugify(rn.path or label),
        label=label, path=rn.path or "", roles=rn.roles, children=children, metadata=metadata,
    )
```

Replace `TypeScriptConstantDetector._parse_file`:

```python
    def _parse_file(self, filepath: Path) -> Optional[list[MenuItem]]:
        root = parse_route_file(filepath, "ts-const")
        if root is None or not root.children:
            return None
        return [_routenode_to_menuitem(c, is_route_format=False) for c in root.children]
```

(Delete `_parse_ts_array`.) Replace the three `FrameworkRoutingDetector` methods so each parses the first existing file with the right framework:

```python
    def _detect_vue_router(self, repo: Path) -> Optional[list[MenuItem]]:
        for fp in [repo/"src"/"router"/"routes.ts", repo/"src"/"router"/"index.ts", repo/"src"/"router"/"routes.js"]:
            if fp.exists():
                root = parse_route_file(fp, "vue")
                if root and root.children:
                    return [_routenode_to_menuitem(c, is_route_format=True) for c in root.children]
        return None

    def _detect_react_router(self, repo: Path) -> Optional[list[MenuItem]]:
        for fp in [repo/"src"/"router.tsx", repo/"src"/"routes.tsx", repo/"src"/"App.tsx", repo/"src"/"router"/"index.tsx"]:
            if fp.exists():
                root = parse_route_file(fp, "react")
                if root and root.children:
                    return [_routenode_to_menuitem(c, is_route_format=True) for c in root.children]
        return None

    def _detect_angular_routing(self, repo: Path) -> Optional[list[MenuItem]]:
        for fp in [repo/"src"/"app"/"app-routing.module.ts", repo/"src"/"app"/"app.routes.ts"]:
            if fp.exists():
                root = parse_route_file(fp, "angular")
                if root and root.children:
                    return [_routenode_to_menuitem(c, is_route_format=True) for c in root.children]
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_menu_detector_routes.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_discovery/menu_detector.py tests/test_menu_detector_routes.py
git commit -m "feat(menu-detector): wire route_parser into TS-const and framework detectors"
```

---

## Task 7: Format-aware `build_screen_map` (breadcrumbs, redirects, full paths, labels)

**Files:**
- Modify: `src/ai_discovery/menu_detector.py` (`build_screen_map`)
- Test: `tests/test_menu_detector_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def _mi(path, label, children=None, is_screen=None, **meta):
    m = {"component_source": None, "redirect_to": None, "is_catch_all": False, **meta}
    if is_screen is not None:
        m["is_screen"] = is_screen
    return md.MenuItem(id=label.lower(), label=label, path=path, children=children or [], metadata=m)


def test_build_screen_map_route_rules():
    # pathless wrapper (is_screen False) with two component children (is_screen True)
    tree = [_mi("/", "Root", is_screen=False, children=[
        _mi("customers", "Customers", is_screen=True, component_source="./C.vue"),
        _mi("orders", "Orders", is_screen=True),
    ])]
    screens = md.build_screen_map(tree, Path("."))
    labels = {s.label for s in screens}
    assert labels == {"Customers", "Orders"}      # wrapper is NOT a screen
    cust = next(s for s in screens if s.label == "Customers")
    assert cust.path == "/customers"               # full path joined
    assert cust.menu_path == ["Root", "Customers"] # breadcrumb includes wrapper
    assert cust.fe_component == "./C.vue"


def test_build_screen_map_leaf_rule_backward_compat():
    # no is_screen metadata -> old leaf rule (JSON/YAML path)
    tree = [md.MenuItem(id="a", label="A", path="/a", children=[
        md.MenuItem(id="b", label="B", path="/a/b"),
    ])]
    screens = md.build_screen_map(tree, Path("."))
    assert {s.label for s in screens} == {"B"}


def test_build_screen_map_redirect_alias():
    tree = [
        _mi("/customers", "Customers", is_screen=True),
        _mi("/old", "Old", is_screen=False, redirect_to="/customers"),
    ]
    screens = md.build_screen_map(tree, Path("."))
    assert {s.label for s in screens} == {"Customers"}  # redirect is not a screen
    cust = screens[0]
    assert "/old" in cust.metadata.get("redirect_aliases", [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_menu_detector_routes.py -q -k build_screen_map`
Expected: FAIL — current `build_screen_map` uses leaf-only rule, no path joining / breadcrumb / redirect handling.

- [ ] **Step 3: Replace `build_screen_map`**

```python
def _join_path(parent: str, child: str) -> str:
    if not child:
        return parent or ""
    if child.startswith("/"):
        return child
    if not parent or parent == "/":
        return "/" + child.lstrip("/")
    return parent.rstrip("/") + "/" + child.lstrip("/")


def build_screen_map(menu_items: list[MenuItem], repo_path: Path) -> list[Screen]:
    """Build Screen objects from a MenuItem tree.

    Format-aware: a MenuItem with metadata['is_screen'] honors that flag (route
    formats); without it, the leaf rule applies (JSON/YAML + TS-const menus).
    Pathless/component-less wrappers become breadcrumb ancestors. Redirect items
    are not screens; their source path is attached to the target screen's
    metadata['redirect_aliases'].
    """
    screens: list[Screen] = []
    redirects: list[tuple[str, str]] = []
    by_full_path: dict[str, Screen] = {}
    seen_ids: set[str] = set()

    def is_screen(item: MenuItem) -> bool:
        meta = item.metadata or {}
        if "is_screen" in meta:
            return bool(meta["is_screen"])
        return not item.children  # leaf rule (backward compat)

    def unique_id(base: str) -> str:
        sid = base or "screen"
        i = 2
        while sid in seen_ids:
            sid = f"{base}-{i}"; i += 1
        seen_ids.add(sid)
        return sid

    def traverse(items: list[MenuItem], crumb: list[str], parent_path: str):
        for item in items:
            meta = item.metadata or {}
            full_path = _join_path(parent_path, item.path)
            current_crumb = crumb + [item.label]
            if meta.get("redirect_to"):
                redirects.append((full_path, meta["redirect_to"]))
            if is_screen(item):
                sid = unique_id(item.id or JsonYamlDetector._slugify(full_path))
                screen = Screen(
                    screen_id=sid, menu_path=current_crumb, label=item.label, path=full_path,
                    fe_component=meta.get("component_source") or None,
                    permissions=item.roles,
                    metadata={k: v for k, v in meta.items() if k not in ("is_screen",)},
                )
                screens.append(screen)
                by_full_path[full_path] = screen
            if item.children:
                traverse(item.children, current_crumb, full_path)

    traverse(menu_items, [], "")

    # Attach redirect sources to their target screen.
    for src_path, target in redirects:
        target_screen = by_full_path.get(target) or by_full_path.get(_join_path("", target))
        if target_screen is not None:
            target_screen.metadata.setdefault("redirect_aliases", []).append(src_path)
    return screens
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk proxy python -m pytest tests/test_menu_detector_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_discovery/menu_detector.py tests/test_menu_detector_routes.py
git commit -m "feat(menu-detector): format-aware build_screen_map (breadcrumbs, redirects, full paths)"
```

---

## Task 8: End-to-end + backward-compat

**Files:**
- Test: `tests/test_menu_detector_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_detect_and_build_screens_vue_endtoend(tmp_path):
    router = (tmp_path / "src" / "router"); router.mkdir(parents=True)
    (router / "index.ts").write_text((FIX / "vue-routes.ts").read_text())
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert menu_items is not None
    labels = {s.label for s in screens}
    assert "Customers" in labels
    assert "Not Found" not in labels            # catch-all dropped
    cust = next(s for s in screens if s.label == "Customers")
    assert cust.menu_path[0] == "Root" or cust.menu_path[-1] == "Customers"
    assert cust.fe_component == "./pages/CustomerList.vue"


def test_jsonyaml_detection_unchanged(tmp_path):
    (tmp_path / "menu.json").write_text(
        '[{"label":"A","path":"/a","children":[{"label":"B","path":"/a/b"}]}]'
    )
    menu_items, screens = md.detect_and_build_screens(tmp_path)
    assert {s.label for s in screens} == {"B"}   # leaf rule preserved
```

Note: the Vue fixture's root has `label "/"` → `_humanize` yields a fallback; the test accepts either crumb position. Adjust the fixture's root to `{ path: '/', component: Layout, meta: { title: 'Root' }, children: [...] }` if a stable "Root" label is wanted — update `tests/fixtures/routes/vue-routes.ts` accordingly in this step.

- [ ] **Step 2: Run test to verify it fails (then pass)**

Run: `rtk proxy python -m pytest tests/test_menu_detector_routes.py -q`
Expected: initially FAIL if the fixture root lacks `meta.title: 'Root'`; add it, then PASS.

- [ ] **Step 3: Full regression**

Run: `rtk proxy python -m pytest -q`
Expected: PASS (all existing tests + new ones). In particular, any existing tests that call `build_screen_map` or `detect_and_build_screens` on JSON/YAML menus must still pass (leaf rule preserved).

- [ ] **Step 4: Commit**

```bash
git add tests/test_menu_detector_routes.py tests/fixtures/routes/vue-routes.ts
git commit -m "test(menu-detector): end-to-end route->screen + JSON/YAML backward-compat"
```

---

## Self-review notes (addressed)

- **Spec coverage:** TS-const (T4/T6), Vue (T4), Angular (T4), React data-router (T4), React JSX (T5), component/lazy resolution (T3), breadcrumb/redirect/catch-all/label rules (T7), backward-compat (T7/T8). All spec sections map to a task.
- **Type consistency:** `RouteNode` fields and helper signatures from Task 1 are used unchanged in Tasks 2–7. `_routenode_to_menuitem(rn, is_route_format)` and `build_screen_map(menu_items, repo_path)` signatures are consistent across Tasks 6–8.
- **`is_screen` precedence:** route formats set the flag (T6); TS-const/JSON-YAML omit it → leaf rule in `build_screen_map` (T7). Consistent with the spec fix.
- **No placeholders:** every code step contains complete, runnable code.
