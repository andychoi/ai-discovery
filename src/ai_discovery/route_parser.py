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


def _grammar_for_ext(path: Path) -> "Language | None":
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
    if node is None or node.text is None:
        return None
    if node.type in ("string", "template_string"):
        for c in node.named_children:
            if c.type in ("string_fragment", "template_substitution"):
                return c.text.decode("utf-8", "ignore")
        text = node.text.decode("utf-8", "ignore")
        return text[1:-1] if len(text) >= 2 else ""
    return node.text.decode("utf-8", "ignore")


def _iter(node):
    """Depth-first iterator over all descendant nodes (incl. node itself)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _collect_imports(root) -> dict[str, str]:
    """Map each imported local binding to its module specifier.

    Handles:
      ``import Def from './d'``         → key ``Def``
      ``import * as NS from './n'``     → key ``NS``
      ``import { A, B as C } from 'm'`` → keys ``A``, ``C``
    """
    imports: dict[str, str] = {}
    for node in _iter(root):
        if node.type != "import_statement":
            continue
        src = node.child_by_field_name("source")
        specifier = _str_value(src) if src is not None else None
        if not specifier:
            continue
        clause = node.child_by_field_name("import") or _find_first_child(node, "import_clause")
        if clause is None:
            continue
        for child in clause.children:
            if child.type == "identifier":
                # default binding: import Foo from 'x'
                imports[child.text.decode("utf-8", "ignore")] = specifier
            elif child.type == "namespace_import":
                # import * as NS from 'x'
                for c in child.children:
                    if c.type == "identifier":
                        imports[c.text.decode("utf-8", "ignore")] = specifier
                        break
            elif child.type == "named_imports":
                # import { A, B as C } from 'x'
                for spec in child.children:
                    if spec.type != "import_specifier":
                        continue
                    alias_node = spec.child_by_field_name("alias")
                    name_node = spec.child_by_field_name("name")
                    # local binding is the alias when present, else the name
                    local = alias_node if alias_node is not None else name_node
                    if local is None:
                        # fallback: first identifier child
                        for c in spec.children:
                            if c.type == "identifier":
                                local = c
                                break
                    if local is not None:
                        imports[local.text.decode("utf-8", "ignore")] = specifier
    return imports


def _find_first_child(node, child_type: str):
    """Return the first child of ``node`` with the given type, or None."""
    for child in node.children:
        if child.type == child_type:
            return child
    return None


# ---------------------------------------------------------------------------
# Task 2: Object-literal walker
# ---------------------------------------------------------------------------

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
        # Keys may be quoted strings (e.g. "path") or bare identifiers (path).
        # _str_value handles both string nodes and identifiers safely.
        key_name = _str_value(key)
        if not key_name:
            continue
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
    out: list[str] = []
    for c in node.named_children:
        if c.type in ("string", "template_string"):
            v = _str_value(c)
            if v is not None:
                out.append(v)
    return out


def _resolve_component(value_node, imports) -> tuple[str | None, str | None]:
    """Return (component_name, component_source) from a component/element/loadChildren value."""
    if value_node is None:
        return None, None
    # lazy: any inner dynamic import('x') — covers () => import(),
    # React.lazy(() => import()), lazy(() => import()), loadChildren: () => import().
    for n in _iter(value_node):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.text is not None and fn.text.decode("utf-8", "ignore") == "import":
                args = n.child_by_field_name("arguments")
                if args is not None:
                    for a in args.named_children:
                        spec = _str_value(a)
                        if spec:
                            return None, spec
    # bare identifier -> look up its import
    if value_node.type == "identifier":
        name = value_node.text.decode("utf-8", "ignore") if value_node.text is not None else None
        return name, (imports.get(name) if name else None)
    # JSX element: <ComponentName /> or <ComponentName>...</ComponentName>
    if value_node.type in ("jsx_self_closing_element", "jsx_element", "jsx_opening_element"):
        # For jsx_self_closing_element and jsx_element the identifier child is the tag name
        for c in value_node.children:
            if c.type == "identifier" and c.text is not None:
                name = c.text.decode("utf-8", "ignore")
                return name, imports.get(name)
    return None, None


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
    comp_node = _first(pairs, field_map.get("component", []))
    rn.component, rn.component_source = _resolve_component(comp_node, imports)
    return rn


# ---------------------------------------------------------------------------
# Task 4: Framework adapters + parse_route_file
# ---------------------------------------------------------------------------

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
               and name.text is not None and name.text.decode("utf-8", "ignore") in var_names:
                return val
        if n.type == "pair":
            key = n.child_by_field_name("key")
            val = n.child_by_field_name("value")
            if key is not None and val is not None and val.type == "array" \
               and key.text is not None and key.text.decode("utf-8", "ignore").strip("'\"") in var_names:
                return val
    return None


def _find_call_array(root, fn_names: set[str]):
    """Find the first array argument of a call like createBrowserRouter([...])."""
    for n in _iter(root):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is None or fn.text is None:
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


def _jsx_attr(open_node, name: str):
    """Return the value node of a JSX attribute `name=...` on an opening/self-closing element."""
    for attr in open_node.children:
        if attr.type != "jsx_attribute":
            continue
        attr_name = attr.children[0]
        if attr_name.text is None or attr_name.text.decode("utf-8", "ignore") != name:
            continue
        for c in attr.children[1:]:
            if c.type == "string":
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
            if ident is not None and ident.text is not None:
                name = ident.text.decode("utf-8", "ignore")
                if name == "Navigate":
                    to = _jsx_attr(n, "to")
                    redirect_to = _str_value(to) if to is not None else None
            break
    return name, redirect_to


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
    if element.type == "jsx_element":
        for child in element.children:
            if child.type in ("jsx_element", "jsx_self_closing_element"):
                ci = child if child.type == "jsx_self_closing_element" else child.children[0]
                cname = ci.child_by_field_name("name")
                if cname is not None and cname.text is not None and cname.text.decode("utf-8", "ignore") == "Route":
                    rn.children.append(_jsx_route_to_node(child, imports))
    return rn


def _jsx_to_routes(node, imports) -> list[RouteNode]:
    for n in _iter(node):
        if n.type == "jsx_element":
            open_el = n.children[0]
            ident = open_el.child_by_field_name("name")
            if ident is not None and ident.text is not None and ident.text.decode("utf-8", "ignore") in ("Routes", "Switch"):
                routes = []
                for child in n.children:
                    if child.type in ("jsx_element", "jsx_self_closing_element"):
                        ci = child if child.type == "jsx_self_closing_element" else child.children[0]
                        cname = ci.child_by_field_name("name")
                        if cname is not None and cname.text is not None and cname.text.decode("utf-8", "ignore") == "Route":
                            routes.append(_jsx_route_to_node(child, imports))
                if routes:
                    return routes
    return []
