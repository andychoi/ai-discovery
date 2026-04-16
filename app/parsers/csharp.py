from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser, Query, QueryCursor

from ..graph.models import CodeNode
from .base import LanguageParser

CS_LANGUAGE = Language(tscsharp.language())

# ── Tree-sitter queries ──────────────────────────────────────────────

_CLASS_QUERY = Query(
    CS_LANGUAGE,
    "(class_declaration name: (identifier) @class.name) @class.def",
)

_METHOD_QUERY = Query(
    CS_LANGUAGE,
    "(method_declaration name: (identifier) @method.name) @method.def",
)

_CONSTRUCTOR_QUERY = Query(
    CS_LANGUAGE,
    "(constructor_declaration name: (identifier) @ctor.name) @ctor.def",
)

_PROPERTY_QUERY = Query(
    CS_LANGUAGE,
    "(property_declaration name: (identifier) @prop.name) @prop.def",
)

_NAMESPACE_QUERY = Query(
    CS_LANGUAGE,
    "(namespace_declaration name: (qualified_name) @ns.name) @ns.def",
)

try:
    _ASSIGNMENT_QUERY = Query(
        CS_LANGUAGE,
        """(assignment_expression
            left: [
                (member_access_expression
                    expression: [
                        (this_expression)
                        (identifier) @assign.obj
                    ]
                    name: (identifier) @assign.attr
                )
                (identifier) @assign.attr
            ]
            right: [
                (string_literal) @assign.val
                (integer_literal) @assign.val
                (identifier) @assign.val
            ]
        )""",
    )
except Exception:
    try:
        _ASSIGNMENT_QUERY = Query(
            CS_LANGUAGE,
            """(assignment_expression
                left: [
                    (member_access_expression
                        name: (identifier) @assign.attr
                    )
                    (identifier) @assign.attr
                ]
                right: [
                    (string_literal) @assign.val
                    (integer_literal) @assign.val
                    (identifier) @assign.val
                ]
            )""",
        )
    except Exception:
        _ASSIGNMENT_QUERY = None

try:
    _CALL_QUERY = Query(
        CS_LANGUAGE,
        """(invocation_expression
            function: [
                (member_access_expression
                    expression: (identifier) @call.obj
                    name: (identifier) @call.name
                )
                (identifier) @call.name
            ]
        )""",
    )
except Exception:
    _CALL_QUERY = None

_DB_OPERATIONS = frozenset({"Add", "Update", "Remove", "SaveChanges", "SaveChangesAsync", "ExecuteSqlRaw", "ExecuteSqlRawAsync", "Attach"})
_EXTERNAL_CLIENTS = frozenset({"httpClient", "restClient", "kafkaProducer", "bus", "serviceBus", "blobClient", "tableClient"})

_HTTP_ATTR_PATTERN = re.compile(r"^Http(Get|Post|Put|Delete|Patch|Head|Options)$")


def _matches(query: Query, node) -> list[dict[str, list]]:
    """Execute a query and return a list of match dicts (paired captures)."""
    return [caps for _pat_idx, caps in QueryCursor(query).matches(node)]


class CSharpParser(LanguageParser):
    """Parses C# files using tree-sitter and emits CodeNode instances."""

    @property
    def language(self) -> str:
        return "csharp"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".cs"})

    # ── public API ────────────────────────────────────────────────────

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        source = file_path.read_bytes()
        parser = Parser(CS_LANGUAGE)
        tree = parser.parse(source)
        root = tree.root_node
        file_stem = file_path.stem
        fp = str(file_path)

        # Extract namespace prefix (if present)
        namespace = self._extract_namespace(root)
        prefix = namespace if namespace else file_stem

        nodes: list[CodeNode] = []

        for cls_match in _matches(_CLASS_QUERY, root):
            cls_node = cls_match["class.def"][0]
            class_name = cls_match["class.name"][0].text.decode()
            qualified = f"{prefix}.{class_name}"

            # Gather class-level attributes
            attrs = self._extract_attributes(cls_node)
            attr_names = [a["name"] for a in attrs]

            # Determine if this is a DB model ([Table("...")])
            table_attr = next((a for a in attrs if a["name"] == "Table"), None)
            is_db_model = table_attr is not None

            node_type = "db_model" if is_db_model else "class"
            framework_hints: dict = {}
            if is_db_model and table_attr and table_attr.get("arg"):
                framework_hints["table"] = table_attr["arg"]

            # Constructor DI: extract parameter type names as calls
            ctor_calls = self._extract_constructor_di(cls_node)

            class_code_node = CodeNode(
                file_path=fp,
                language="csharp",
                node_type=node_type,
                name=class_name,
                qualified_name=qualified,
                source_code=cls_node.text.decode(),
                line_start=cls_node.start_point.row + 1,
                line_end=cls_node.end_point.row + 1,
                annotations=attr_names,
                framework_hints=framework_hints,
                calls=ctor_calls,
            )

            # For DB models, extract properties as params
            if is_db_model:
                class_code_node.params = self._extract_properties(cls_node)

            nodes.append(class_code_node)

            # Methods inside this class
            for m_match in _matches(_METHOD_QUERY, cls_node):
                m_node = m_match["method.def"][0]
                method_name = m_match["method.name"][0].text.decode()
                m_qualified = f"{prefix}.{class_name}.{method_name}"
                m_attrs = self._extract_attributes(m_node)
                m_attr_names = [a["name"] for a in m_attrs]
                params = self._extract_params(m_node)
                calls = self._extract_calls(m_node)
                return_type = self._extract_return_type(m_node)

                # Detect HTTP endpoint
                endpoint_info = self._detect_endpoint(m_attrs)
                m_node_type = "endpoint" if endpoint_info else "method"
                
                # New: Extract behavioral signals
                transitions = self._extract_state_transitions(m_node, class_name)
                boundaries = self._detect_boundaries(m_node)
                
                m_framework_hints: dict = {
                    "transitions": transitions,
                    "boundaries": boundaries
                }
                if endpoint_info:
                    m_framework_hints["method"] = endpoint_info["method"]
                    m_framework_hints["route"] = endpoint_info["route"]

                nodes.append(CodeNode(
                    file_path=fp,
                    language="csharp",
                    node_type=m_node_type,
                    name=method_name,
                    qualified_name=m_qualified,
                    source_code=m_node.text.decode(),
                    line_start=m_node.start_point.row + 1,
                    line_end=m_node.end_point.row + 1,
                    annotations=m_attr_names,
                    params=params,
                    calls=calls,
                    return_type=return_type,
                    framework_hints=m_framework_hints,
                ))

        return nodes

    # ── helpers ────────────────────────────────────────────────────────

    def _extract_calls(self, node) -> list[str]:
        """Extract method call names."""
        calls = []
        for match in _matches(_CALL_QUERY, node):
            name_node = match.get("call.name", [None])[0]
            if name_node:
                calls.append(name_node.text.decode())
        return list(set(calls))

    @staticmethod
    def _extract_state_transitions(node, class_name: str) -> list[dict]:
        """Look for assignments like this.Status = \"Active\"."""
        transitions = []
        for match in _matches(_ASSIGNMENT_QUERY, node):
            obj_node = match.get("assign.obj", [None])[0]
            attr_node = match.get("assign.attr", [None])[0]
            val_node = match.get("assign.val", [None])[0]
            
            if attr_node and val_node:
                obj_text = obj_node.text.decode() if obj_node else "this"
                attr_text = attr_node.text.decode()
                val_text = val_node.text.decode().strip("\"'")
                
                if any(kw in attr_text.lower() for kw in ("status", "state", "stage", "phase")):
                    transitions.append({
                        "entity": class_name if obj_text == "this" else obj_text,
                        "field": attr_text,
                        "value": val_text
                    })
        return transitions

    @staticmethod
    def _detect_boundaries(node) -> list[dict]:
        """Detect DB operations (EF Core) or external API calls."""
        boundaries = []
        for match in _matches(_CALL_QUERY, node):
            call_node = match.get("call.name", [None])[0]
            if not call_node:
                continue
                
            call_text = call_node.text.decode()
            
            if call_text in _DB_OPERATIONS:
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

    @staticmethod
    def _extract_namespace(root) -> str | None:
        """Extract the namespace qualified name from the root node."""
        for match in _matches(_NAMESPACE_QUERY, root):
            return match["ns.name"][0].text.decode()
        return None

    @staticmethod
    def _extract_attributes(node) -> list[dict]:
        """Extract attributes (annotations) from a node's attribute_list children.

        Returns a list of dicts with 'name' and optional 'arg' (first string argument).
        """
        attrs: list[dict] = []
        for child in node.children:
            if child.type == "attribute_list":
                for attr_child in child.children:
                    if attr_child.type == "attribute":
                        name = None
                        arg = None
                        for ac in attr_child.children:
                            if ac.type == "identifier":
                                name = ac.text.decode()
                            elif ac.type == "attribute_argument_list":
                                # Extract first string literal argument
                                for aa in ac.children:
                                    if aa.type == "attribute_argument":
                                        for sl in aa.children:
                                            if sl.type == "string_literal":
                                                # Get string_literal_content
                                                for sc in sl.children:
                                                    if sc.type == "string_literal_content":
                                                        arg = sc.text.decode()
                                                        break
                                                break
                                        break
                        if name:
                            entry: dict = {"name": name}
                            if arg is not None:
                                entry["arg"] = arg
                            attrs.append(entry)
        return attrs

    @staticmethod
    def _extract_params(method_node) -> list[str]:
        """Extract parameter names from a method's parameter_list."""
        params: list[str] = []
        for child in method_node.children:
            if child.type == "parameter_list":
                for p in child.children:
                    if p.type == "parameter":
                        # The last identifier child is the parameter name
                        identifiers = [c for c in p.children if c.type == "identifier"]
                        if identifiers:
                            params.append(identifiers[-1].text.decode())
        return params

    @staticmethod
    def _extract_return_type(method_node) -> str | None:
        """Extract the return type from a method declaration.

        The return type appears before the method name identifier.
        """
        for child in method_node.children:
            if child.type in (
                "identifier",
                "predefined_type",
                "generic_name",
                "nullable_type",
                "array_type",
                "void_keyword",
            ):
                # Check if the next sibling is the method name identifier
                next_sib = child.next_named_sibling
                if next_sib is not None and next_sib.type == "identifier":
                    return child.text.decode()
        return None

    @staticmethod
    def _extract_constructor_di(cls_node) -> list[str]:
        """Extract constructor parameter type names as DI references."""
        calls: list[str] = []
        for match in _matches(_CONSTRUCTOR_QUERY, cls_node):
            ctor_node = match["ctor.def"][0]
            for child in ctor_node.children:
                if child.type == "parameter_list":
                    for p in child.children:
                        if p.type == "parameter":
                            # First identifier is the type name
                            for pc in p.children:
                                if pc.type == "identifier":
                                    calls.append(pc.text.decode())
                                    break
        return calls

    @staticmethod
    def _extract_properties(cls_node) -> list[str]:
        """Extract property names from a class declaration."""
        props: list[str] = []
        for match in _matches(_PROPERTY_QUERY, cls_node):
            name = match["prop.name"][0].text.decode()
            props.append(name)
        return props

    @staticmethod
    def _detect_endpoint(attrs: list[dict]) -> dict | None:
        """If any attribute is an HTTP method attribute, return method + route."""
        for attr in attrs:
            m = _HTTP_ATTR_PATTERN.match(attr["name"])
            if m:
                method = m.group(1).upper()
                route = attr.get("arg", "")
                return {"method": method, "route": route}
        return None
