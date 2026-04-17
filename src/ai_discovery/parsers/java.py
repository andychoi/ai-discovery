from __future__ import annotations

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Query, QueryCursor

from ..graph.models import CodeNode
from .base import LanguageParser

JAVA_LANGUAGE = Language(tsjava.language())

# ── Mapping from Spring annotations to HTTP methods ──────────────────

_MAPPING_ANNOTATIONS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "REQUEST",
}

# ── Tree-sitter queries ─────────────────────────────────────────────

_CLASS_QUERY = Query(
    JAVA_LANGUAGE,
    "(class_declaration name: (identifier) @class.name) @class.def",
)

_METHOD_QUERY = Query(
    JAVA_LANGUAGE,
    "(method_declaration name: (identifier) @method.name) @method.def",
)

_CONSTRUCTOR_QUERY = Query(
    JAVA_LANGUAGE,
    "(constructor_declaration name: (identifier) @ctor.name) @ctor.def",
)

_ANNOTATION_QUERY = Query(
    JAVA_LANGUAGE,
    "(marker_annotation name: (identifier) @ann.name) @ann.def",
)

_ANNOTATION_WITH_ARGS_QUERY = Query(
    JAVA_LANGUAGE,
    "(annotation name: (identifier) @ann.name arguments: (annotation_argument_list) @ann.args) @ann.def",
)

try:
    _ASSIGNMENT_QUERY = Query(
        JAVA_LANGUAGE,
        """(assignment
            left: [
                (field_access
                    object: (identifier) @assign.obj
                    field: (identifier) @assign.attr
                )
                (identifier) @assign.attr
            ]
            right: [
                (string_literal) @assign.val
                (decimal_integer_literal) @assign.val
                (identifier) @assign.val
            ]
        )""",
    )
except Exception:
    _ASSIGNMENT_QUERY = None

try:
    _CALL_QUERY = Query(
        JAVA_LANGUAGE,
        """[
          (method_invocation name: (identifier) @call.name)
        ]""",
    )
except Exception:
    # Basic fallback if complex query fails
    _CALL_QUERY = None

_DB_OPERATIONS = frozenset({"save", "saveAndFlush", "delete", "deleteById", "update", "execute", "persist", "merge"})
_EXTERNAL_CLIENTS = frozenset({"restTemplate", "webClient", "feign", "httpClient", "okHttpClient", "kafkaTemplate", "jmsTemplate", "sqsClient"})

_PACKAGE_QUERY = Query(
    JAVA_LANGUAGE,
    "(package_declaration (scoped_identifier) @pkg.name)",
)


def _matches(query: Query, node) -> list[dict[str, list]]:
    return [caps for _pat_idx, caps in QueryCursor(query).matches(node)]


def _captures(query: Query, node) -> dict[str, list]:
    return QueryCursor(query).captures(node)


class JavaParser(LanguageParser):
    """Parses Java files using tree-sitter and emits CodeNode instances."""

    @property
    def language(self) -> str:
        return "java"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".java"})

    # ── public API ────────────────────────────────────────────────────

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        source = file_path.read_bytes()
        parser = Parser(JAVA_LANGUAGE)
        tree = parser.parse(source)
        root = tree.root_node
        fp = str(file_path)

        # Extract package name for qualified names
        pkg = self._extract_package(root)
        qualifier = pkg if pkg else file_path.stem

        nodes: list[CodeNode] = []

        # 1. Classes
        for match in _matches(_CLASS_QUERY, root):
            cls_node = match["class.def"][0]
            name_node = match["class.name"][0]
            class_name = name_node.text.decode()
            qualified = f"{qualifier}.{class_name}"

            annotations = self._extract_annotations(cls_node)
            ann_names = [a["name"] for a in annotations]

            # Determine node_type
            node_type = "class"
            framework_hints: dict = {}

            if any(n in ("Entity", "Table") for n in ann_names):
                node_type = "db_model"
                # Extract table name from @Table annotation
                for a in annotations:
                    if a["name"] == "Table" and a.get("args"):
                        table_name = self._extract_string_arg(a["args"], "name")
                        if table_name:
                            framework_hints["table"] = table_name

            # Get class-level route from @RequestMapping
            class_route = ""
            for a in annotations:
                if a["name"] == "RequestMapping" and a.get("args"):
                    class_route = self._extract_first_string(a["args"])

            # DI references from @Autowired constructors
            di_calls = self._extract_autowired_deps(cls_node)

            nodes.append(CodeNode(
                file_path=fp,
                language="java",
                node_type=node_type,
                name=class_name,
                qualified_name=qualified,
                source_code=cls_node.text.decode(),
                line_start=cls_node.start_point.row + 1,
                line_end=cls_node.end_point.row + 1,
                annotations=ann_names,
                calls=di_calls,
                framework_hints=framework_hints if framework_hints else {},
            ))

            # 2. Methods inside this class
            for m_match in _matches(_METHOD_QUERY, cls_node):
                m_node = m_match["method.def"][0]
                m_name_node = m_match["method.name"][0]
                method_name = m_name_node.text.decode()
                m_qualified = f"{qualifier}.{class_name}.{method_name}"

                m_annotations = self._extract_annotations(m_node)
                m_ann_names = [a["name"] for a in m_annotations]
                params = self._extract_params(m_node)
                calls = self._extract_calls(m_node)
                return_type = self._extract_return_type(m_node)

                # Check for endpoint annotations
                endpoint_info = self._detect_endpoint(m_annotations, class_route)
                # Check for batch job
                is_batch = "Scheduled" in m_ann_names

                # New: Extract behavioral signals
                transitions = self._extract_state_transitions(m_node, class_name)
                boundaries = self._detect_boundaries(m_node)

                m_hints: dict = {
                    "transitions": transitions, 
                    "boundaries": boundaries
                }
                if endpoint_info:
                    m_node_type = "endpoint"
                    m_hints["method"] = endpoint_info["method"]
                    m_hints["route"] = endpoint_info["route"]
                elif is_batch:
                    m_node_type = "batch_job"
                else:
                    m_node_type = "method"

                nodes.append(CodeNode(
                    file_path=fp,
                    language="java",
                    node_type=m_node_type,
                    name=method_name,
                    qualified_name=m_qualified,
                    source_code=m_node.text.decode(),
                    line_start=m_node.start_point.row + 1,
                    line_end=m_node.end_point.row + 1,
                    params=params,
                    calls=calls,
                    return_type=return_type,
                    annotations=m_ann_names,
                    framework_hints=m_hints,
                ))

        return nodes

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_package(root) -> str:
        matches = _matches(_PACKAGE_QUERY, root)
        if matches:
            return matches[0]["pkg.name"][0].text.decode()
        return ""

    @staticmethod
    def _extract_annotations(node) -> list[dict]:
        """Return annotation dicts for a class or method node.

        Looks at the parent or preceding siblings for annotation nodes.
        """
        annotations: list[dict] = []

        # Annotations appear as siblings before the node, or children of the
        # parent program/class_body.  In tree-sitter-java, class and method
        # declarations can have modifiers children that include annotations.
        # We also need to check preceding siblings of the node.
        # Strategy: walk all children of the node's parent that come before node.
        parent = node.parent
        if parent is None:
            return annotations

        found_self = False
        for child in parent.children:
            if child.id == node.id:
                found_self = True
                break

        # Collect annotations that are siblings before our node
        if found_self:
            for child in parent.children:
                if child.id == node.id:
                    break
                # Skip other class/method declarations
                if child.type in ("class_declaration", "method_declaration",
                                  "constructor_declaration", "field_declaration"):
                    annotations.clear()
                    continue
                if child.type == "marker_annotation":
                    name_child = child.child_by_field_name("name")
                    if name_child:
                        annotations.append({"name": name_child.text.decode()})
                elif child.type == "annotation":
                    name_child = child.child_by_field_name("name")
                    args_child = child.child_by_field_name("arguments")
                    if name_child:
                        entry: dict = {"name": name_child.text.decode()}
                        if args_child:
                            entry["args"] = args_child.text.decode()
                        annotations.append(entry)

        # Also check modifiers child of the node itself
        for child in node.children:
            if child.type == "modifiers":
                for mod_child in child.children:
                    if mod_child.type == "marker_annotation":
                        name_child = mod_child.child_by_field_name("name")
                        if name_child:
                            annotations.append({"name": name_child.text.decode()})
                    elif mod_child.type == "annotation":
                        name_child = mod_child.child_by_field_name("name")
                        args_child = mod_child.child_by_field_name("arguments")
                        if name_child:
                            entry = {"name": name_child.text.decode()}
                            if args_child:
                                entry["args"] = args_child.text.decode()
                            annotations.append(entry)

        return annotations

    @staticmethod
    def _extract_string_arg(args_text: str, key: str) -> str:
        """Extract a named string argument like name = \"payments\" from annotation args."""
        # args_text looks like: (name = "payments") or ("payments")
        import re
        pattern = rf'{key}\s*=\s*"([^"]*)"'
        m = re.search(pattern, args_text)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _extract_first_string(args_text: str) -> str:
        """Extract the first quoted string from annotation arguments."""
        import re
        m = re.search(r'"([^"]*)"', args_text)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _extract_params(method_node) -> list[str]:
        params: list[str] = []
        for child in method_node.children:
            if child.type == "formal_parameters":
                for p in child.children:
                    if p.type == "formal_parameter" or p.type == "spread_parameter":
                        name_node = p.child_by_field_name("name")
                        if name_node:
                            params.append(name_node.text.decode())
        return params

    @staticmethod
    def _extract_calls(node) -> list[str]:
        caps = _captures(_CALL_QUERY, node)
        call_names = caps.get("call.name", [])
        return list(dict.fromkeys(n.text.decode() for n in call_names))

    @staticmethod
    def _extract_return_type(method_node) -> str | None:
        type_node = method_node.child_by_field_name("type")
        if type_node:
            return type_node.text.decode()
        return None

    def _extract_autowired_deps(self, cls_node) -> list[str]:
        """Find @Autowired constructor parameters and return their type names."""
        deps: list[str] = []
        for match in _matches(_CONSTRUCTOR_QUERY, cls_node):
            ctor_node = match["ctor.def"][0]
            annotations = self._extract_annotations(ctor_node)
            ann_names = [a["name"] for a in annotations]
            if "Autowired" in ann_names:
                for child in ctor_node.children:
                    if child.type == "formal_parameters":
                        for p in child.children:
                            if p.type == "formal_parameter":
                                type_node = p.child_by_field_name("type")
                                if type_node:
                                    deps.append(type_node.text.decode())
        return deps

    @staticmethod
    def _extract_state_transitions(node, class_name: str) -> list[dict]:
        """Look for assignments like this.status = 'ACTIVE' or status = 'PAID'."""
        transitions = []
        for match in _matches(_ASSIGNMENT_QUERY, node):
            obj_node = match.get("assign.obj", [None])[0]
            attr_node = match.get("assign.attr", [None])[0]
            val_node = match.get("assign.val", [None])[0]
            
            if attr_node and val_node:
                obj_text = obj_node.text.decode() if obj_node else "this"
                attr_text = attr_node.text.decode()
                val_text = val_node.text.decode().strip("\"'")
                
                # Check if attribute name is a status-like field
                if any(kw in attr_text.lower() for kw in ("status", "state", "stage", "phase")):
                    transitions.append({
                        "entity": class_name if obj_text == "this" else obj_text,
                        "field": attr_text,
                        "value": val_text
                    })
        return transitions

    @staticmethod
    def _detect_boundaries(node) -> list[dict]:
        """Detect DB operations (Spring Data) or external API calls."""
        boundaries = []
        for match in _matches(_CALL_QUERY, node):
            call_node = match.get("call.name", [None])[0]
            if not call_node:
                continue
                
            call_text = call_node.text.decode()
            
            # Check for DB operations
            if call_text in _DB_OPERATIONS:
                boundaries.append({"type": "DB", "operation": call_text})
                continue
            
            # Check for external client usage via object name
            parent = call_node.parent
            if parent and parent.type == "method_invocation":
                obj_node = parent.child_by_field_name("object")
                if obj_node:
                    obj_text = obj_node.text.decode().lower()
                    if any(client in obj_text for client in _EXTERNAL_CLIENTS):
                        boundaries.append({
                            "type": "EXTERNAL_API", 
                            "client": obj_text,
                            "method": call_text
                        })
                    
        return boundaries

    def _detect_endpoint(self, annotations: list[dict], class_route: str) -> dict | None:
        """Check annotations for Spring endpoint mappings."""
        for ann in annotations:
            name = ann.get("name", "")
            if name not in _MAPPING_ANNOTATIONS:
                continue
            method = _MAPPING_ANNOTATIONS[name]

            # For @RequestMapping, try to extract method from annotation args
            if name == "RequestMapping" and ann.get("args"):
                args = ann["args"]
                import re
                m = re.search(r'method\s*=\s*RequestMethod\.(\w+)', args)
                if m:
                    method = m.group(1).upper()

            # Extract route from annotation args
            route = ""
            if ann.get("args"):
                route = self._extract_first_string(ann["args"])

            full_route = class_route + route
            return {"method": method, "route": full_route}

        return None
