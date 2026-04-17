from __future__ import annotations

from pathlib import Path

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Query, QueryCursor

from ..graph.models import CodeNode
from .base import LanguageParser

JS_LANGUAGE = Language(tsjs.language())
TS_LANGUAGE = Language(tsts.language_typescript())

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

try:
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
except Exception:
    _ASSIGNMENT_QUERY_JS = None

try:
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
except Exception:
    _CALL_QUERY_JS = None

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
        source = file_path.read_bytes()
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

        # Track class body ranges to avoid double-counting methods
        class_ranges: set[tuple[int, int]] = set()

        # 1. Classes
        for child in root.children:
            cls_node = self._unwrap_export(child)
            if cls_node is None:
                continue
            if cls_node.type == "class_declaration":
                self._process_class(cls_node, file_stem, fp, nodes, class_ranges)

        # 2. Top-level functions, arrow functions, and variable declarations
        for child in root.children:
            node = self._unwrap_export(child)
            if node is None:
                continue
            self._process_top_level(node, file_stem, fp, nodes, class_ranges, root)

        # 3. Express endpoints from call expressions
        self._extract_endpoints(root, file_stem, fp, nodes)

        return nodes

    # ── class processing ──────────────────────────────────────────────

    def _process_class(
        self,
        cls_node,
        file_stem: str,
        fp: str,
        nodes: list[CodeNode],
        class_ranges: set[tuple[int, int]],
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
                    
                    # New: Behavioral signals
                    transitions = self._extract_state_transitions(member, class_name)
                    boundaries = self._detect_boundaries(member)

                    nodes.append(
                        CodeNode(
                            file_path=fp,
                            language="javascript",
                            node_type="method",
                            name=method_name,
                            qualified_name=f"{file_stem}.{class_name}.{method_name}",
                            source_code=member.text.decode(),
                            line_start=member.start_point.row + 1,
                            line_end=member.end_point.row + 1,
                            calls=calls,
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
    ) -> None:
        if node.type == "function_declaration":
            if self._inside_class(node, class_ranges):
                return
            name_node = self._find_child(node, "identifier")
            if name_node is None:
                return
            func_name = name_node.text.decode()
            calls = self._extract_calls(node)
            node_type = "ui_component" if self._returns_jsx(node) else "function"
            
            # New: Behavioral signals
            transitions = self._extract_state_transitions(node, "")
            boundaries = self._detect_boundaries(node)

            nodes.append(
                CodeNode(
                    file_path=fp,
                    language="javascript",
                    node_type=node_type,
                    name=func_name,
                    qualified_name=f"{file_stem}.{func_name}",
                    source_code=node.text.decode(),
                    line_start=node.start_point.row + 1,
                    line_end=node.end_point.row + 1,
                    calls=calls,
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
                        decl, node, file_stem, fp, nodes, class_ranges
                    )

    def _process_variable_declarator(
        self,
        decl,
        parent_node,
        file_stem: str,
        fp: str,
        nodes: list[CodeNode],
        class_ranges: set[tuple[int, int]],
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
        node_type = "ui_component" if self._returns_jsx(value_node) else "function"
        
        # New: Behavioral signals
        transitions = self._extract_state_transitions(value_node, "")
        boundaries = self._detect_boundaries(value_node)

        nodes.append(
            CodeNode(
                file_path=fp,
                language="javascript",
                node_type=node_type,
                name=func_name,
                qualified_name=f"{file_stem}.{func_name}",
                source_code=parent_node.text.decode(),
                line_start=parent_node.start_point.row + 1,
                line_end=parent_node.end_point.row + 1,
                calls=calls,
                framework_hints={
                    "transitions": transitions,
                    "boundaries": boundaries
                }
            )
        )

    # ── Express endpoint extraction ──────────────────────────────────

    def _extract_endpoints(
        self, root, file_stem: str, fp: str, nodes: list[CodeNode]
    ) -> None:
        """Find router.get('/path', handler) or app.post('/path', handler) patterns."""
        self._walk_for_endpoints(root, file_stem, fp, nodes)

    def _walk_for_endpoints(
        self, node, file_stem: str, fp: str, nodes: list[CodeNode]
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
                        framework_hints={
                            "method": method,
                            "route": route,
                            "framework": "express",
                        },
                    )
                )
                return  # Don't recurse into endpoint children

        for child in node.children:
            self._walk_for_endpoints(child, file_stem, fp, nodes)

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

    def _extract_state_transitions(self, node, class_name: str) -> list[dict]:
        """Look for assignments like this.status = 'ACTIVE'."""
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
                    transitions.append({
                        "entity": class_name if obj_text == "this" else obj_text,
                        "field": attr_text,
                        "value": val_text
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
