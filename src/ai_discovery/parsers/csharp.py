from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_c_sharp as tscsharp
from tree_sitter import Language, Parser, Query

from ..graph.models import CodeNode
from .base import LanguageParser, find_enclosing_guard
from ._ts import matches as _matches, extract_call_sites

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

# Phase 1.1: per-call-site records (with receiver). Accepts any receiver shape
# under `expression:` so `a.b.Method()` and `this.Field.Method()` both match.
try:
    _CALL_SITE_QUERY = Query(
        CS_LANGUAGE,
        """(invocation_expression
            function: [
                (member_access_expression
                    expression: (_) @site.receiver
                    name: (identifier) @site.name
                )
                (identifier) @site.name
            ]
        ) @site.call""",
    )
except Exception:
    _CALL_SITE_QUERY = None

_USING_QUERY = Query(
    CS_LANGUAGE,
    "(using_directive) @using.decl",
)

_DB_OPERATIONS = frozenset({"Add", "Update", "Remove", "SaveChanges", "SaveChangesAsync", "ExecuteSqlRaw", "ExecuteSqlRawAsync", "Attach"})
_EXTERNAL_CLIENTS = frozenset({"httpClient", "restClient", "kafkaProducer", "bus", "serviceBus", "blobClient", "tableClient"})

_HTTP_ATTR_PATTERN = re.compile(r"^Http(Get|Post|Put|Delete|Patch|Head|Options)$")



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

        # File-level usings — attached to every node in the file.
        imports = self._extract_imports(root)

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

            # Class-level [Route("api/[controller]")] prefix — composed into
            # each action's route below (HIGH-4: previously dropped, so the
            # "verified" path was a truncated method fragment stamped ✓1.00).
            route_attr = next((a for a in attrs if a["name"] == "Route"), None)
            class_route = route_attr.get("arg", "") if route_attr else ""

            # Constructor DI: extract parameter type names as calls
            ctor_calls = self._extract_constructor_di(cls_node)

            class_fields = self._extract_class_fields(cls_node)
            class_bases = self._extract_bases(cls_node)

            # Field/property/ctor-param types for DI/receiver-type resolution (HIGH-3).
            field_types = self._extract_field_types(cls_node)
            if field_types:
                framework_hints["field_types"] = field_types

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
                imports=imports,
                fields=class_fields,
                bases=class_bases,
            )

            # For DB models, populate `params` with the same field set — this
            # preserves the pre-Phase-2d shape that downstream db_model
            # consumers depend on (they look at `params` for column names).
            if is_db_model:
                class_code_node.params = list(class_fields)

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
                call_sites = self._extract_call_sites(m_node)
                return_type = self._extract_return_type(m_node)

                # Detect HTTP endpoint, composing the controller-level route
                # prefix and resolving [controller]/[action] tokens.
                endpoint_info = self._detect_endpoint(m_attrs)
                if endpoint_info:
                    endpoint_info["route"] = self._compose_route(
                        class_route, endpoint_info["route"], class_name, method_name,
                    )
                m_node_type = "endpoint" if endpoint_info else "method"
                
                # New: Extract behavioral signals
                transitions = self._extract_state_transitions(
                    m_node,
                    class_name,
                    class_qualified=qualified,
                    enclosing_qualified=m_qualified,
                )
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
                    call_sites=call_sites,
                    imports=imports,
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
    def _extract_call_sites(node) -> list[dict]:
        # C# invocation_expression splits into member_access_expression (with
        # receiver) and bare identifier (implicit this/local). The query may be
        # None on older grammars — extract_call_sites handles that.
        return extract_call_sites(_CALL_SITE_QUERY, node)

    @staticmethod
    def _extract_imports(root) -> list[dict]:
        """Extract each `using ...;` directive.

        C# `using X.Y.Z;` makes every type in the namespace short-name
        accessible — there is no single bound name, so `name=None` marks it
        as namespace-level (behaves like Python `from X import *`).
        `using Alias = X.Y.Z;` binds `Alias` locally.
        """
        out: list[dict] = []
        for match in _matches(_USING_QUERY, root):
            decl = match["using.decl"][0]
            alias: str | None = None
            target: str = ""
            is_static = False
            for child in decl.children:
                if child.type == "static" or child.text == b"static":
                    is_static = True
                elif child.type == "name_equals":
                    for c in child.children:
                        if c.type == "identifier":
                            alias = c.text.decode()
                            break
                elif child.type in ("qualified_name", "identifier", "alias_qualified_name"):
                    target = child.text.decode()
            if not target:
                continue
            if alias:
                module, _, name = target.rpartition(".")
                out.append({"module": module, "name": name or None, "alias": alias, "static": is_static})
            elif is_static:
                # `using static X.Y.Z;` makes Z's static members short-name accessible.
                module, _, name = target.rpartition(".")
                out.append({"module": module, "name": name, "alias": None, "static": True})
            else:
                out.append({"module": target, "name": None, "alias": None, "static": False})
        return out

    @staticmethod
    def _extract_state_transitions(
        node,
        class_name: str,
        class_qualified: str | None = None,
        enclosing_qualified: str | None = None,
    ) -> list[dict]:
        """Look for assignments like this.Status = \"Active\".

        `entity_id` disambiguates across modules: the class's qualified_name
        for `this` receivers, and `enclosing_method.qualified_name::obj` for
        duck-typed receivers.
        """
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
    def _bare_type(text: str) -> str:
        """Reduce a C# type to its bare name: `List<Product>`→`List`,
        `App.Services.OrderService`→`OrderService`, `OrderService?`→`OrderService`."""
        t = (text or "").strip().rstrip("?")
        t = t.split("<", 1)[0]
        return t.rsplit(".", 1)[-1].strip()

    @classmethod
    def _extract_field_types(cls, cls_node) -> dict:
        """Map field / property / constructor-param names to their declared type
        for DI/receiver-type call resolution (HIGH-3). Covers field injection
        (`private readonly OrderService _svc;`), auto-properties, and constructor
        injection (`Handler(OrderService svc)`)."""
        out: dict[str, str] = {}
        body = next((c for c in cls_node.children if c.type == "declaration_list"), None)
        if body is not None:
            for member in body.children:
                if member.type == "property_declaration":
                    tnode = member.child_by_field_name("type")
                    nnode = member.child_by_field_name("name")
                    if tnode is not None and nnode is not None:
                        out[nnode.text.decode()] = cls._bare_type(tnode.text.decode())
                elif member.type == "field_declaration":
                    for sub in member.children:
                        if sub.type != "variable_declaration":
                            continue
                        tnode = sub.child_by_field_name("type")
                        tname = cls._bare_type(tnode.text.decode()) if tnode is not None else ""
                        if not tname:
                            continue
                        for decl in sub.children:
                            if decl.type != "variable_declarator":
                                continue
                            nn = decl.child_by_field_name("name") or next(
                                (c for c in decl.children if c.type == "identifier"), None
                            )
                            if nn is not None:
                                out[nn.text.decode()] = tname
        for match in _matches(_CONSTRUCTOR_QUERY, cls_node):
            ctor = match["ctor.def"][0]
            for child in ctor.children:
                if child.type != "parameter_list":
                    continue
                for p in child.children:
                    if p.type != "parameter":
                        continue
                    tnode = p.child_by_field_name("type")
                    nnode = p.child_by_field_name("name")
                    if tnode is not None and nnode is not None:
                        out.setdefault(nnode.text.decode(), cls._bare_type(tnode.text.decode()))
        return out

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
    def _extract_bases(cls_node) -> list[str]:
        """Return base class + interface names for a C# class (Phase 2d).

        C# groups both the base class and all interfaces into a single
        `base_list` node — the language grammar doesn't distinguish them
        syntactically (convention: base class first if present). For the
        consolidator's purposes this is fine: both are inheritance edges.
        """
        bases: list[str] = []
        for child in cls_node.children:
            if child.type != "base_list":
                continue
            for sub in child.children:
                if sub.type == "identifier":
                    bases.append(sub.text.decode())
                elif sub.type == "qualified_name":
                    # `System.IDisposable` → record `IDisposable`
                    last = None
                    for c in sub.children:
                        if c.type == "identifier":
                            last = c
                    if last is not None:
                        bases.append(last.text.decode())
                elif sub.type == "generic_name":
                    name_child = None
                    for c in sub.children:
                        if c.type == "identifier":
                            name_child = c
                            break
                    if name_child is not None:
                        bases.append(name_child.text.decode())
        return bases

    @staticmethod
    def _extract_class_fields(cls_node) -> list[str]:
        """Return names of both `property_declaration`s and `field_declaration`s
        declared directly on this class (Phase 2d).

        Scoped to the direct `declaration_list` child so inner-class members
        don't pollute the outer class's fingerprint. Properties and fields
        are unified — both represent entity state and matter equally to FSM
        identity consolidation.
        """
        names: list[str] = []
        seen: set[str] = set()
        body = None
        for child in cls_node.children:
            if child.type == "declaration_list":
                body = child
                break
        if body is None:
            return names
        for member in body.children:
            if member.type == "property_declaration":
                name_node = member.child_by_field_name("name")
                if name_node is not None:
                    name = name_node.text.decode()
                    if name not in seen:
                        names.append(name)
                        seen.add(name)
            elif member.type == "field_declaration":
                # `field_declaration` wraps a `variable_declaration` whose
                # children are `variable_declarator`s. A single `private int a, b;`
                # expands to multiple declarators.
                for sub in member.children:
                    if sub.type != "variable_declaration":
                        continue
                    for decl in sub.children:
                        if decl.type != "variable_declarator":
                            continue
                        name_node = decl.child_by_field_name("name")
                        if name_node is None:
                            # Fallback: first identifier child
                            for c in decl.children:
                                if c.type == "identifier":
                                    name_node = c
                                    break
                        if name_node is None:
                            continue
                        name = name_node.text.decode()
                        if name not in seen:
                            names.append(name)
                            seen.add(name)
        return names

    @staticmethod
    def _detect_endpoint(attrs: list[dict]) -> dict | None:
        """If any attribute is an HTTP method attribute, return method + route.

        The route here is the method-level fragment only; the caller composes it
        with the controller-level [Route] prefix via _compose_route.
        """
        for attr in attrs:
            m = _HTTP_ATTR_PATTERN.match(attr["name"])
            if m:
                method = m.group(1).upper()
                route = attr.get("arg", "")
                return {"method": method, "route": route}
        return None

    @staticmethod
    def _compose_route(
        class_route: str, method_route: str, controller_name: str, action_name: str
    ) -> str:
        """Join the controller [Route] prefix with the action route and resolve
        ASP.NET tokens. `[controller]` → controller name minus 'Controller'
        suffix; `[action]` → method name. Produces a leading-slash path.
        """
        ctrl = controller_name[:-10] if controller_name.endswith("Controller") else controller_name

        def _sub(t: str) -> str:
            return (t or "").replace("[controller]", ctrl).replace("[action]", action_name)

        base = _sub(class_route).strip("/")
        leaf = _sub(method_route).strip("/")
        parts = [p for p in (base, leaf) if p]
        return "/" + "/".join(parts) if parts else ""
