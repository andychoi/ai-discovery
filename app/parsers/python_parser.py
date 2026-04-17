from __future__ import annotations

from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query, QueryCursor

from ..graph.models import CodeNode
from .base import LanguageParser

PY_LANGUAGE = Language(tspython.language())

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

# ── Tree-sitter queries ──────────────────────────────────────────────

_CLASS_QUERY = Query(
    PY_LANGUAGE,
    "(class_definition name: (identifier) @class.name) @class.def",
)

_FUNCTION_QUERY = Query(
    PY_LANGUAGE,
    "(function_definition name: (identifier) @func.name) @func.def",
)

_CALL_QUERY = Query(
    PY_LANGUAGE,
    """[
      (call function: (identifier) @call.name)
      (call function: (attribute attribute: (identifier) @call.name))
    ]""",
)

_ASSIGNMENT_QUERY = Query(
    PY_LANGUAGE,
    """(assignment
        left: (attribute
            object: [
                (identifier) @assign.obj
                (attribute) @assign.obj
            ]
            attribute: (identifier) @assign.attr
        )
        right: [
            (string) @assign.val
            (identifier) @assign.val
            (attribute) @assign.val
            (integer) @assign.val
        ]
    )""",
)

_DB_OPERATIONS = frozenset({"save", "add", "delete", "update", "commit", "execute", "merge", "flush", "refresh"})
_EXTERNAL_CLIENTS = frozenset({"requests", "httpx", "aiohttp", "urllib", "boto3", "google", "pika", "kafka"})

_DECORATOR_QUERY = Query(
    PY_LANGUAGE,
    "(decorator) @dec",
)


def _matches(query: Query, node) -> list[dict[str, list]]:
    """Execute a query and return a list of match dicts (paired captures)."""
    return [caps for _pat_idx, caps in QueryCursor(query).matches(node)]


def _captures(query: Query, node) -> dict[str, list]:
    """Execute a query and return captures as {name: [node, ...]}."""
    return QueryCursor(query).captures(node)


class PythonParser(LanguageParser):
    """Parses Python files using tree-sitter and emits CodeNode instances."""

    @property
    def language(self) -> str:
        return "python"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".py", ".pyw"})

    # ── public API ────────────────────────────────────────────────────

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        source = file_path.read_bytes()
        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(source)
        root = tree.root_node
        file_stem = file_path.stem
        fp = str(file_path)

        nodes: list[CodeNode] = []

        # 1. classes + their methods
        # Track class body ranges so we can skip nested functions later
        class_ranges: set[tuple[int, int]] = set()

        for match in _matches(_CLASS_QUERY, root):
            cls_node = match["class.def"][0]
            name_node = match["class.name"][0]
            class_name = name_node.text.decode()
            qualified = f"{file_stem}.{class_name}"
            class_ranges.add((cls_node.start_point.row, cls_node.end_point.row))

            nodes.append(CodeNode(
                file_path=fp,
                language="python",
                node_type="class",
                name=class_name,
                qualified_name=qualified,
                source_code=cls_node.text.decode(),
                line_start=cls_node.start_point.row + 1,
                line_end=cls_node.end_point.row + 1,
            ))

            # methods inside this class
            for m_match in _matches(_FUNCTION_QUERY, cls_node):
                m_node = m_match["func.def"][0]
                m_name_node = m_match["func.name"][0]
                method_name = m_name_node.text.decode()
                m_qualified = f"{file_stem}.{class_name}.{method_name}"
                params = self._extract_params(m_node, skip_self=True)
                calls = self._extract_calls(m_node)
                return_type = self._extract_return_type(m_node)
                
                # New: Extract behavioral signals
                transitions = self._extract_state_transitions(m_node)
                boundaries = self._detect_boundaries(m_node)
                is_async = self._is_async_function(m_node)

                f_hints = {"transitions": transitions, "boundaries": boundaries}
                if is_async:
                    f_hints["async_boundary"] = True

                nodes.append(CodeNode(
                    file_path=fp,
                    language="python",
                    node_type="method",
                    name=method_name,
                    qualified_name=m_qualified,
                    source_code=m_node.text.decode(),
                    line_start=m_node.start_point.row + 1,
                    line_end=m_node.end_point.row + 1,
                    params=params,
                    calls=calls,
                    return_type=return_type,
                    framework_hints=f_hints,
                ))

        # 2. top-level functions (skip those inside classes)
        for f_match in _matches(_FUNCTION_QUERY, root):
            f_node = f_match["func.def"][0]
            f_name_node = f_match["func.name"][0]

            if self._inside_class(f_node, class_ranges):
                continue

            func_name = f_name_node.text.decode()
            qualified = f"{file_stem}.{func_name}"
            params = self._extract_params(f_node, skip_self=False)
            calls = self._extract_calls(f_node)
            return_type = self._extract_return_type(f_node)
            decorators = self._extract_decorators(f_node)
            annotations = [d["text"] for d in decorators]

            # Check if this is an HTTP endpoint
            endpoint_info = self._detect_endpoint(decorators)
            node_type = "endpoint" if endpoint_info else "function"
            
            # New: Extract behavioral signals
            transitions = self._extract_state_transitions(f_node)
            boundaries = self._detect_boundaries(f_node)
            is_async = self._is_async_function(f_node)

            framework_hints: dict = {
                "transitions": transitions,
                "boundaries": boundaries
            }
            if is_async:
                framework_hints["async_boundary"] = True
            if endpoint_info:
                framework_hints["method"] = endpoint_info["method"]
                framework_hints["route"] = endpoint_info["route"]

            nodes.append(CodeNode(
                file_path=fp,
                language="python",
                node_type=node_type,
                name=func_name,
                qualified_name=qualified,
                source_code=f_node.text.decode(),
                line_start=f_node.start_point.row + 1,
                line_end=f_node.end_point.row + 1,
                params=params,
                calls=calls,
                return_type=return_type,
                annotations=annotations,
                framework_hints=framework_hints,
            ))

        return nodes

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_state_transitions(node) -> list[dict]:
        """Look for assignments like self.status = 'ACTIVE'."""
        transitions = []
        caps = _captures(_ASSIGNMENT_QUERY, node)
        
        objects = caps.get("assign.obj", [])
        attrs = caps.get("assign.attr", [])
        values = caps.get("assign.val", [])
        
        # matches are grouped by pattern, but captures is a flat dict.
        # We need to use QueryCursor.matches to keep them paired.
        for match in _matches(_ASSIGNMENT_QUERY, node):
            obj_node = match.get("assign.obj", [None])[0]
            attr_node = match.get("assign.attr", [None])[0]
            val_node = match.get("assign.val", [None])[0]
            
            if obj_node and attr_node and val_node:
                obj_text = obj_node.text.decode()
                attr_text = attr_node.text.decode()
                val_text = val_node.text.decode().strip("\"'")
                
                # Check if attribute name is a status-like field
                if any(kw in attr_text.lower() for kw in ("status", "state", "stage", "phase")):
                    transitions.append({
                        "entity": obj_text,
                        "field": attr_text,
                        "value": val_text
                    })
        return transitions

    @staticmethod
    def _detect_boundaries(node) -> list[dict]:
        """Detect DB operations or external API calls."""
        boundaries = []
        # Reuse _CALL_QUERY logic but look for specific patterns
        for match in _matches(_CALL_QUERY, node):
            call_node = match.get("call.name", [None])[0]
            if not call_node:
                continue

            call_text = call_node.text.decode()

            # Check for DB operations
            if call_text.lower() in _DB_OPERATIONS:
                boundaries.append({"type": "DB", "operation": call_text})
                continue

            # Check for external client usage
            parent = call_node.parent
            if parent and parent.type == "attribute":
                obj_node = parent.child_by_field_name("object")
                if obj_node and obj_node.text.decode().lower() in _EXTERNAL_CLIENTS:
                    boundaries.append({
                        "type": "EXTERNAL_API",
                        "client": obj_node.text.decode(),
                        "method": call_text
                    })

        return boundaries

    @staticmethod
    def _is_async_function(func_node) -> bool:
        """Check if a function is declared as async (async def)."""
        # In tree-sitter, async functions have parent type "decorated_definition"
        # with an async keyword, or the function_definition itself may have async.
        # Check node text for "async" prefix
        text = func_node.text.decode()
        return text.strip().startswith("async def")

    @staticmethod
    def _inside_class(node, class_ranges: set[tuple[int, int]]) -> bool:
        """Return True if *node* sits within any recorded class range."""
        row = node.start_point.row
        for start, end in class_ranges:
            if start <= row <= end:
                return True
        return False

    @staticmethod
    def _extract_params(func_node, *, skip_self: bool) -> list[str]:
        params: list[str] = []
        for child in func_node.children:
            if child.type == "parameters":
                for p in child.children:
                    if p.type in ("identifier", "typed_parameter", "default_parameter",
                                  "typed_default_parameter"):
                        name = p.text.decode().split(":")[0].split("=")[0].strip()
                        if skip_self and name == "self":
                            continue
                        params.append(name)
        return params

    @staticmethod
    def _extract_calls(node) -> list[str]:
        caps = _captures(_CALL_QUERY, node)
        call_names = caps.get("call.name", [])
        return list(dict.fromkeys(n.text.decode() for n in call_names))

    @staticmethod
    def _extract_return_type(func_node) -> str | None:
        for child in func_node.children:
            if child.type == "type":
                return child.text.decode()
        return None

    @staticmethod
    def _extract_decorators(func_node) -> list[dict]:
        """Return decorator info for the function.

        Walk *backwards* from the function_definition node through its
        preceding siblings to collect any decorator nodes.  tree-sitter
        wraps decorated definitions in a ``decorated_definition`` node,
        so we also check the parent.
        """
        decorators: list[dict] = []
        parent = func_node.parent
        if parent is not None and parent.type == "decorated_definition":
            for child in parent.children:
                if child.type == "decorator":
                    decorators.append({"text": child.text.decode().lstrip("@").strip()})
        return decorators

    @staticmethod
    def _detect_endpoint(decorators: list[dict]) -> dict | None:
        """If any decorator looks like ``@obj.get("/path")``, return method + route."""
        for dec in decorators:
            text = dec["text"]
            # Pattern: <obj>.<http_method>("<route>")
            if "(" not in text or "." not in text:
                continue
            # Split on first '(' to get the callable part
            callable_part, args_part = text.split("(", 1)
            parts = callable_part.rsplit(".", 1)
            if len(parts) != 2:
                continue
            method = parts[1].lower()
            if method not in _HTTP_METHODS:
                continue
            # Extract route from first string argument
            route = ""
            for ch in ('"', "'"):
                if ch in args_part:
                    start = args_part.index(ch) + 1
                    end = args_part.index(ch, start)
                    route = args_part[start:end]
                    break
            return {"method": method.upper(), "route": route}
        return None
