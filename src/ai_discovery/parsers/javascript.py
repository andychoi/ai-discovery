from __future__ import annotations

import logging
from pathlib import Path

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Query, QueryCursor

from ..graph.models import CodeNode
from .base import LanguageParser, find_enclosing_guard

logger = logging.getLogger(__name__)

# Minification thresholds. A file is considered minified if it's larger than
# _MIN_SIZE_BYTES AND average line length exceeds _MAX_AVG_LINE_LEN. This
# catches bundler output (Vite, Webpack) where entire modules collapse onto
# one line, without flagging hand-written code (typical lines < 120 chars).
_MIN_SIZE_BYTES = 2048
_MAX_AVG_LINE_LEN = 200

# Path segments that typically contain generated/bundled code. Matched as
# directory components so `distinct/` or `vendors.js` don't false-positive.
_GENERATED_PATH_SEGMENTS = frozenset({"dist", "build", "vendor", ".next", ".nuxt", "out", "bundled"})
_MINIFIED_SUFFIXES = (".min.js", ".min.mjs", ".min.cjs", ".bundle.js", ".chunk.js")


def _is_minified_path(file_path: Path) -> bool:
    name = file_path.name.lower()
    if any(name.endswith(sfx) for sfx in _MINIFIED_SUFFIXES):
        return True
    parts = {p.lower() for p in file_path.parts}
    return bool(parts & _GENERATED_PATH_SEGMENTS)


def _is_minified(source: bytes) -> bool:
    if len(source) < _MIN_SIZE_BYTES:
        return False
    newlines = source.count(b"\n")
    avg = len(source) / (newlines + 1)
    return avg > _MAX_AVG_LINE_LEN

JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tsts.language_typescript())

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

# Queries are compiled at import time. A compile failure here is a real
# environment problem (tree-sitter version drift, grammar mismatch) — we want
# the import to fail loudly rather than degrade silently at parse time, where
# _matches(None, node) would raise a cryptic AttributeError mid-scan.
_ASSIGNMENT_QUERY_JS = Query(
    JS_LANGUAGE,
    """(assignment_expression
        left: [
            (member_expression
                object: [
                    (this)
                    (identifier) @assign.obj
                ]
                property: (property_identifier) @assign.attr
            )
            (identifier) @assign.attr
        ]
        right: [
            (string) @assign.val
            (number) @assign.val
            (identifier) @assign.val
        ]
    )""",
)

_CALL_QUERY_JS = Query(
    JS_LANGUAGE,
    """(call_expression
        function: [
            (member_expression
                object: (identifier) @call.obj
                property: (property_identifier) @call.name
            )
            (identifier) @call.name
        ]
    )""",
)

# Phase 1.1: per-call-site records (with receiver). `(_)` accepts any shape
# under `object:` so `this.svc.save()` and `a.b.c()` both match, not just the
# narrow `obj.method()` form captured by _CALL_QUERY_JS.
_CALL_SITE_QUERY_JS = Query(
    JS_LANGUAGE,
    """(call_expression
        function: [
            (member_expression
                object: (_) @site.receiver
                property: (property_identifier) @site.name
            )
            (identifier) @site.name
        ]
    ) @site.call""",
)

_IMPORT_QUERY_JS = Query(
    JS_LANGUAGE,
    "(import_statement) @import.decl",
)

_DB_OPERATIONS = frozenset({"save", "insert", "update", "delete", "remove", "execute", "query", "persist", "merge", "commit"})
_EXTERNAL_CLIENTS = frozenset({"axios", "fetch", "got", "request", "superagent", "prisma", "sequelize", "typeorm", "mongoose", "knex", "pg", "kafka", "amqp"})


def _matches(query: Query, node) -> list[dict[str, list]]:
    return [caps for _pat_idx, caps in QueryCursor(query).matches(node)]


class JavaScriptParser(LanguageParser):
    """Parses JavaScript and TypeScript files using tree-sitter."""

    @property
    def language(self) -> str:
        return "javascript"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".js", ".ts", ".tsx", ".jsx"})

    # ── public API ────────────────────────────────────────────────────

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        if _is_minified_path(file_path):
            logger.debug("Skipping bundled/minified path: %s", file_path)
            return []
        source = file_path.read_bytes()
        if _is_minified(source):
            logger.debug("Skipping minified file: %s", file_path)
            return []
        lang = TS_LANGUAGE if file_path.suffix in (".ts", ".tsx") else JS_LANGUAGE
        parser = Parser(lang)
        tree = parser.parse(source)
        root = tree.root_node
        file_stem = file_path.stem
        fp = str(file_path)

        nodes: list[CodeNode] = []
        exported_names: set[str] = set()

        # Collect exports first so we can annotate nodes
        self._collect_exports(root, exported_names)

        # File-level ES6 imports — attached to every node in the file.
        imports = self._extract_imports(root)

        # Track class body ranges to avoid double-counting methods
        class_ranges: set[tuple[int, int]] = set()

        # 1. Classes
        for child in root.children:
            cls_node = self._unwrap_export(child)
            if cls_node is None:
                continue
            if cls_node.type == "class_declaration":
                self._process_class(cls_node, file_stem, fp, nodes, class_ranges, imports)

        # 2. Top-level functions, arrow functions, and variable declarations
        for child in root.children:
            node = self._unwrap_export(child)
            if node is None:
                continue
            self._process_top_level(node, file_stem, fp, nodes, class_ranges, root, imports)

        # 3. Express endpoints from call expressions
        self._extract_endpoints(root, file_stem, fp, nodes, imports)

        return nodes

    # ── class processing ──────────────────────────────────────────────

    def _process_class(
        self,
        cls_node,
        file_stem: str,
        fp: str,
        nodes: list[CodeNode],
        class_ranges: set[tuple[int, int]],
        imports: list[dict],
    ) -> None:
        name_node = self._find_child(cls_node, "type_identifier") or self._find_child(
            cls_node, "identifier"
        )
        if name_node is None:
            return
        class_name = name_node.text.decode()
        qualified = f"{file_stem}.{class_name}"
        class_ranges.add((cls_node.start_point.row, cls_node.end_point.row))

        # Check if it extends React.Component
        node_type = "class"
        heritage = self._find_child(cls_node, "class_heritage")
        if heritage and b"Component" in heritage.text:
            node_type = "ui_component"

        class_fields = self._extract_class_fields(cls_node)
        class_bases = self._extract_bases(cls_node)

        nodes.append(
            CodeNode(
                file_path=fp,
                language="javascript",
                node_type=node_type,
                name=class_name,
                qualified_name=qualified,
                source_code=cls_node.text.decode(),
                line_start=cls_node.start_point.row + 1,
                line_end=cls_node.end_point.row + 1,
                imports=imports,
                fields=class_fields,
                bases=class_bases,
            )
        )

        # Methods inside the class body
        body = self._find_child(cls_node, "class_body")
        if body:
            for member in body.children:
                if member.type == "method_definition":
                    m_name = self._find_child(member, "property_identifier")
                    if m_name is None:
                        continue
                    method_name = m_name.text.decode()
                    calls = self._extract_calls(member)
                    call_sites = self._extract_call_sites(member)
                    m_qualified = f"{file_stem}.{class_name}.{method_name}"

                    # New: Behavioral signals
                    transitions = self._extract_state_transitions(
                        member,
                        class_name,
                        class_qualified=qualified,
                        enclosing_qualified=m_qualified,
                    )
                    boundaries = self._detect_boundaries(member)

                    nodes.append(
                        CodeNode(
                            file_path=fp,
                            language="javascript",
                            node_type="method",
                            name=method_name,
                            qualified_name=m_qualified,
                            source_code=member.text.decode(),
                            line_start=member.start_point.row + 1,
                            line_end=member.end_point.row + 1,
                            calls=calls,
                            call_sites=call_sites,
                            imports=imports,
                            framework_hints={
                                "transitions": transitions,
                                "boundaries": boundaries
                            }
                        )
                    )

    # ── top-level functions / arrow functions ─────────────────────────

    def _process_top_level(
        self,
        node,
        file_stem: str,
        fp: str,
        nodes: list[CodeNode],
        class_ranges: set[tuple[int, int]],
        root,
        imports: list[dict],
    ) -> None:
        if node.type == "function_declaration":
            if self._inside_class(node, class_ranges):
                return
            name_node = self._find_child(node, "identifier")
            if name_node is None:
                return
            func_name = name_node.text.decode()
            calls = self._extract_calls(node)
            call_sites = self._extract_call_sites(node)
            node_type = "ui_component" if self._returns_jsx(node) else "function"
            fn_qualified = f"{file_stem}.{func_name}"

            # New: Behavioral signals
            transitions = self._extract_state_transitions(
                node, "", enclosing_qualified=fn_qualified,
            )
            boundaries = self._detect_boundaries(node)

            nodes.append(
                CodeNode(
                    file_path=fp,
                    language="javascript",
                    node_type=node_type,
                    name=func_name,
                    qualified_name=fn_qualified,
                    source_code=node.text.decode(),
                    line_start=node.start_point.row + 1,
                    line_end=node.end_point.row + 1,
                    calls=calls,
                    call_sites=call_sites,
                    imports=imports,
                    framework_hints={
                        "transitions": transitions,
                        "boundaries": boundaries
                    }
                )
            )
        elif node.type in ("lexical_declaration", "variable_declaration"):
            # const Foo = () => {} or const Foo = function() {}
            for decl in node.children:
                if decl.type == "variable_declarator":
                    self._process_variable_declarator(
                        decl, node, file_stem, fp, nodes, class_ranges, imports
                    )

    def _process_variable_declarator(
        self,
        decl,
        parent_node,
        file_stem: str,
        fp: str,
        nodes: list[CodeNode],
        class_ranges: set[tuple[int, int]],
        imports: list[dict],
    ) -> None:
        name_node = self._find_child(decl, "identifier")
        value_node = self._find_child(decl, "arrow_function") or self._find_child(
            decl, "function"
        )
        if name_node is None or value_node is None:
            return
        if self._inside_class(parent_node, class_ranges):
            return

        func_name = name_node.text.decode()
        calls = self._extract_calls(value_node)
        call_sites = self._extract_call_sites(value_node)
        node_type = "ui_component" if self._returns_jsx(value_node) else "function"
        fn_qualified = f"{file_stem}.{func_name}"

        # New: Behavioral signals
        transitions = self._extract_state_transitions(
            value_node, "", enclosing_qualified=fn_qualified,
        )
        boundaries = self._detect_boundaries(value_node)

        nodes.append(
            CodeNode(
                file_path=fp,
                language="javascript",
                node_type=node_type,
                name=func_name,
                qualified_name=fn_qualified,
                source_code=parent_node.text.decode(),
                line_start=parent_node.start_point.row + 1,
                line_end=parent_node.end_point.row + 1,
                calls=calls,
                call_sites=call_sites,
                imports=imports,
                framework_hints={
                    "transitions": transitions,
                    "boundaries": boundaries
                }
            )
        )

    # ── Express endpoint extraction ──────────────────────────────────

    def _extract_endpoints(
        self, root, file_stem: str, fp: str, nodes: list[CodeNode], imports: list[dict]
    ) -> None:
        """Find router.get('/path', handler) or app.post('/path', handler) patterns."""
        self._walk_for_endpoints(root, file_stem, fp, nodes, imports)

    def _walk_for_endpoints(
        self, node, file_stem: str, fp: str, nodes: list[CodeNode], imports: list[dict]
    ) -> None:
        if node.type == "call_expression":
            endpoint = self._parse_endpoint_call(node)
            if endpoint:
                method, route = endpoint
                # Find the expression_statement parent for full source
                stmt = node.parent if node.parent and node.parent.type == "expression_statement" else node
                nodes.append(
                    CodeNode(
                        file_path=fp,
                        language="javascript",
                        node_type="endpoint",
                        name=f"{method} {route}",
                        qualified_name=f"{file_stem}.{method} {route}",
                        source_code=stmt.text.decode(),
                        line_start=stmt.start_point.row + 1,
                        line_end=stmt.end_point.row + 1,
                        calls=self._extract_calls(node),
                        call_sites=self._extract_call_sites(node),
                        imports=imports,
                        framework_hints={
                            "method": method,
                            "route": route,
                            "framework": "express",
                        },
                    )
                )
                return  # Don't recurse into endpoint children

        for child in node.children:
            self._walk_for_endpoints(child, file_stem, fp, nodes, imports)

    def _parse_endpoint_call(self, call_node) -> tuple[str, str] | None:
        """Check if a call_expression is router.get('/path', ...) or app.post(...)."""
        func = self._find_child(call_node, "member_expression")
        if func is None:
            return None

        prop = self._find_child(func, "property_identifier")
        if prop is None:
            return None

        method = prop.text.decode().lower()
        if method not in _HTTP_METHODS:
            return None

        args = self._find_child(call_node, "arguments")
        if args is None:
            return None

        # First argument should be a string (the route)
        for arg_child in args.children:
            if arg_child.type in ("string", "template_string"):
                route = arg_child.text.decode().strip("\"'`")
                return (method.upper(), route)

        return None

    @staticmethod
    def _extract_bases(cls_node) -> list[str]:
        """Return base class + implemented interface names (Phase 2d).

        JS has only `extends` (one parent); TypeScript adds `implements`
        (many interfaces). Both live under `class_heritage`. We collect
        identifiers conservatively — call-form bases like `extends mixin(X)`
        keep only the outermost identifier (`mixin`), which is usually the
        right thing for the consolidator's inheritance graph.
        """
        bases: list[str] = []
        heritage = JavaScriptParser._find_child(cls_node, "class_heritage")
        if heritage is None:
            return bases
        for child in heritage.children:
            if child.type in ("identifier", "type_identifier"):
                bases.append(child.text.decode())
            elif child.type == "extends_clause":
                for sub in child.children:
                    if sub.type in ("identifier", "type_identifier"):
                        bases.append(sub.text.decode())
                    elif sub.type == "member_expression":
                        prop = sub.child_by_field_name("property")
                        if prop is not None:
                            bases.append(prop.text.decode())
                    elif sub.type == "call_expression":
                        fn = sub.child_by_field_name("function")
                        if fn is not None and fn.type == "identifier":
                            bases.append(fn.text.decode())
            elif child.type == "implements_clause":
                for sub in child.children:
                    if sub.type in ("identifier", "type_identifier"):
                        bases.append(sub.text.decode())
                    elif sub.type == "generic_type":
                        ident = JavaScriptParser._find_child(sub, "type_identifier") \
                            or JavaScriptParser._find_child(sub, "identifier")
                        if ident is not None:
                            bases.append(ident.text.decode())
        return bases

    @staticmethod
    def _extract_class_fields(cls_node) -> list[str]:
        """Return names of fields declared on this class (Phase 2d).

        Captures two shapes:
          - ES2022 class field syntax: `status = 'CREATED'` or `status;`
            parses as `field_definition` (JS) / `public_field_definition` (TS)
            inside `class_body`.
          - Legacy `this.X = ...` inside the `constructor` method.

        Scoped to the class's own `class_body` so inner classes don't leak.
        """
        names: list[str] = []
        seen: set[str] = set()
        body = JavaScriptParser._find_child(cls_node, "class_body")
        if body is None:
            return names

        for member in body.children:
            if member.type in ("field_definition", "public_field_definition"):
                name_node = member.child_by_field_name("name") or member.child_by_field_name("property")
                if name_node is None:
                    # Fallback: first property_identifier child
                    for c in member.children:
                        if c.type in ("property_identifier", "identifier"):
                            name_node = c
                            break
                if name_node is None:
                    continue
                name = name_node.text.decode().lstrip("#")  # strip private `#`
                if name not in seen:
                    names.append(name)
                    seen.add(name)
            elif member.type == "method_definition":
                # Only the constructor contributes `this.X` fields.
                m_name = JavaScriptParser._find_child(member, "property_identifier")
                if m_name is None or m_name.text.decode() != "constructor":
                    continue
                for this_name in JavaScriptParser._walk_this_assignments(member):
                    if this_name not in seen:
                        names.append(this_name)
                        seen.add(this_name)
        return names

    @staticmethod
    def _walk_this_assignments(node) -> list[str]:
        """Collect field names from `this.X = <any>` assignments within node."""
        found: list[str] = []
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "member_expression":
                obj = left.child_by_field_name("object")
                prop = left.child_by_field_name("property")
                if obj is not None and obj.type == "this" and prop is not None:
                    found.append(prop.text.decode())
        for child in node.children:
            found.extend(JavaScriptParser._walk_this_assignments(child))
        return found

    def _extract_state_transitions(
        self,
        node,
        class_name: str,
        class_qualified: str | None = None,
        enclosing_qualified: str | None = None,
    ) -> list[dict]:
        """Look for assignments like this.status = 'ACTIVE'.

        `entity_id` uses the class's qualified_name for `this` receivers,
        `enclosing_function.qualified_name::obj` otherwise — giving a unique
        trace key across modules even when short names collide.
        """
        transitions = []
        for match in _matches(_ASSIGNMENT_QUERY_JS, node):
            obj_node = match.get("assign.obj", [None])[0]
            attr_node = match.get("assign.attr", [None])[0]
            val_node = match.get("assign.val", [None])[0]

            if attr_node and val_node:
                obj_text = obj_node.text.decode() if obj_node else "this"
                attr_text = attr_node.text.decode()
                val_text = val_node.text.decode().strip("\"'`")

                if any(kw in attr_text.lower() for kw in ("status", "state", "stage", "phase")):
                    is_self = obj_text == "this"
                    if is_self:
                        entity = class_name
                        entity_id = class_qualified or class_name
                    else:
                        entity = obj_text
                        entity_id = (
                            f"{enclosing_qualified}::{obj_text}"
                            if enclosing_qualified else obj_text
                        )
                    transitions.append({
                        "entity": entity,
                        "entity_id": entity_id,
                        "field": attr_text,
                        "value": val_text,
                        "guard": find_enclosing_guard(attr_node),
                    })
        return transitions

    def _detect_boundaries(self, node) -> list[dict]:
        """Detect DB operations or external API calls."""
        boundaries = []
        for match in _matches(_CALL_QUERY_JS, node):
            call_node = match.get("call.name", [None])[0]
            if not call_node:
                continue
                
            call_text = call_node.text.decode()
            
            if call_text.lower() in _DB_OPERATIONS:
                boundaries.append({"type": "DB", "operation": call_text})
                continue
            
            obj_node = match.get("call.obj", [None])[0]
            if obj_node:
                obj_text = obj_node.text.decode().lower()
                if any(client in obj_text for client in _EXTERNAL_CLIENTS):
                    boundaries.append({
                        "type": "EXTERNAL_API", 
                        "client": obj_text,
                        "method": call_text
                    })
        return boundaries

    # ── JSX detection ────────────────────────────────────────────────

    def _returns_jsx(self, node) -> bool:
        """Check if a function/arrow function returns JSX."""
        return self._has_jsx_descendant(node)

    def _has_jsx_descendant(self, node) -> bool:
        if node.type in (
            "jsx_element",
            "jsx_self_closing_element",
            "jsx_fragment",
        ):
            return True
        for child in node.children:
            if self._has_jsx_descendant(child):
                return True
        return False

    # ── shared helpers ───────────────────────────────────────────────

    @staticmethod
    def _find_child(node, child_type: str):
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    @staticmethod
    def _inside_class(node, class_ranges: set[tuple[int, int]]) -> bool:
        row = node.start_point.row
        for start, end in class_ranges:
            if start <= row <= end:
                return True
        return False

    def _extract_calls(self, node) -> list[str]:
        """Walk tree and collect function call names."""
        calls: list[str] = []
        self._walk_calls(node, calls)
        return list(dict.fromkeys(calls))

    def _walk_calls(self, node, calls: list[str]) -> None:
        if node.type == "call_expression":
            func = node.children[0] if node.children else None
            if func:
                if func.type == "identifier":
                    calls.append(func.text.decode())
                elif func.type == "member_expression":
                    prop = self._find_child(func, "property_identifier")
                    if prop:
                        calls.append(prop.text.decode())
        for child in node.children:
            self._walk_calls(child, calls)

    @staticmethod
    def _extract_call_sites(node) -> list[dict]:
        """One record per call site with receiver text.

        JS `call_expression` splits into `member_expression` (with `object:`
        receiver) and bare `identifier` (free-standing call). Receiver captures
        the raw text of the object expression — so `this.svc.save()` yields
        receiver=`this.svc`.
        """
        sites: list[dict] = []
        if _CALL_SITE_QUERY_JS is None:
            return sites
        for match in _matches(_CALL_SITE_QUERY_JS, node):
            name_nodes = match.get("site.name", [])
            if not name_nodes:
                continue
            receiver_nodes = match.get("site.receiver", [])
            sites.append({
                "name": name_nodes[0].text.decode(),
                "receiver": receiver_nodes[0].text.decode() if receiver_nodes else None,
            })
        return sites

    @staticmethod
    def _extract_imports(root) -> list[dict]:
        """Extract ES6 import_statement declarations.

        Encoding matches the resolver's `_build_import_index` precedence
        (alias → name → first-module-segment for the local binding):

          `import X from 'mod'`          → name=X, alias=None
            (pragmatic: the local binding name is usually the module's primary
            export; we record it in `name` so resolver lookups for
            `mod.X.method` have the right shape)
          `import { X } from 'mod'`      → name=X, alias=None
          `import { X as Y } from 'mod'` → name=X, alias=Y
          `import * as X from 'mod'`     → name=None, alias=X  (namespace)
          `import 'mod'`                 → no record (side effect only)
        """
        out: list[dict] = []
        if _IMPORT_QUERY_JS is None:
            return out
        for match in _matches(_IMPORT_QUERY_JS, root):
            decl = match["import.decl"][0]
            module_node = JavaScriptParser._find_child(decl, "string")
            if module_node is None:
                continue
            module = module_node.text.decode().strip("\"'`")
            clause = JavaScriptParser._find_child(decl, "import_clause")
            if clause is None:
                continue  # side-effect-only import

            for child in clause.children:
                if child.type == "identifier":
                    # default binding: `import X from 'mod'`
                    out.append({
                        "module": module,
                        "name": child.text.decode(),
                        "alias": None,
                    })
                elif child.type == "namespace_import":
                    # `import * as X from 'mod'`
                    ident = JavaScriptParser._find_child(child, "identifier")
                    if ident is not None:
                        out.append({
                            "module": module,
                            "name": None,
                            "alias": ident.text.decode(),
                        })
                elif child.type == "named_imports":
                    # `import { X, Y as Z } from 'mod'`
                    for spec in child.children:
                        if spec.type != "import_specifier":
                            continue
                        name_ident = spec.child_by_field_name("name")
                        alias_ident = spec.child_by_field_name("alias")
                        if name_ident is None:
                            # Fallback: first identifier child
                            for c in spec.children:
                                if c.type == "identifier":
                                    name_ident = c
                                    break
                        if name_ident is None:
                            continue
                        out.append({
                            "module": module,
                            "name": name_ident.text.decode(),
                            "alias": alias_ident.text.decode() if alias_ident else None,
                        })
        return out

    def _collect_exports(self, root, exported_names: set[str]) -> None:
        """Collect names that are exported."""
        for child in root.children:
            if child.type == "export_statement":
                decl = self._find_child(child, "function_declaration") or self._find_child(
                    child, "class_declaration"
                ) or self._find_child(child, "lexical_declaration")
                if decl:
                    name = self._find_child(decl, "identifier")
                    if name:
                        exported_names.add(name.text.decode())

    @staticmethod
    def _unwrap_export(node):
        """If node is an export_statement, return the inner declaration; else return node."""
        if node.type == "export_statement":
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "class_declaration",
                    "lexical_declaration",
                    "variable_declaration",
                ):
                    return child
            return None
        return node
