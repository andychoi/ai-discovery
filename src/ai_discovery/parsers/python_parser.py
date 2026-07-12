from __future__ import annotations

from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query

from ..graph.models import CodeNode
from .base import LanguageParser, find_enclosing_guard
from ._ts import matches as _matches, captures as _captures, extract_call_sites, inside_class

PY_LANGUAGE = Language(tspython.language())

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

# Base classes that mark a class-based view whose HTTP-verb-named methods are
# request handlers (Django CBV/DRF, Flask MethodView, flask-restful Resource).
_VIEW_BASES = frozenset({
    "View", "APIView", "ViewSet", "ModelViewSet", "GenericViewSet", "ReadOnlyModelViewSet",
    "MethodView", "Resource", "TemplateView", "ListView", "DetailView",
    "CreateView", "UpdateView", "DeleteView", "RedirectView",
    "ListAPIView", "RetrieveAPIView", "CreateAPIView", "GenericAPIView",
})

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

# Same-shape query but also captures the attribute's receiver, for call-site
# records. Split from `_CALL_QUERY` because the `short-name only` consumers
# (boundary/state-transition detection) don't need the receiver and benefit
# from simpler matches.
_CALL_SITE_QUERY = Query(
    PY_LANGUAGE,
    """[
      (call function: (identifier) @site.name) @site.call
      (call
        function: (attribute
          object: (_) @site.receiver
          attribute: (identifier) @site.name
        )
      ) @site.call
    ]""",
)

_IMPORT_QUERY = Query(
    PY_LANGUAGE,
    """[
      (import_statement) @import.plain
      (import_from_statement) @import.from
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

# `self.X = <any>` inside a method body. Unlike `_ASSIGNMENT_QUERY` (which
# restricts RHS for state-transition detection), this query matches any RHS
# because field *existence*, not its value, is what Phase 2d needs.
_SELF_FIELD_QUERY = Query(
    PY_LANGUAGE,
    """(assignment
        left: (attribute
            object: (identifier) @self.obj
            attribute: (identifier) @self.attr
        )
    )""",
)


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

        # File-level imports — attached to every node in the file so the
        # resolver can scope calls regardless of which function it's examining.
        imports = self._extract_imports(root)

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

            class_fields = self._extract_class_fields(cls_node)
            class_bases = self._extract_bases(cls_node)

            # Class-based view? (Django/DRF/Flask MethodView/flask-restful) —
            # HTTP-verb-named methods are endpoints even without a decorator
            # (HIGH-5). Route comes from URL config, not the class, so it's left
            # empty here (and thus excluded from the verified-API table, which
            # requires both verb and path — we don't stamp an empty route).
            is_view = any(
                b.split(".")[-1] in _VIEW_BASES or b.endswith(("View", "ViewSet", "APIView"))
                for b in class_bases
            )

            # Field/ctor-param types for DI/receiver-type resolution (HIGH-3).
            field_types = self._extract_field_types(cls_node)

            nodes.append(CodeNode(
                file_path=fp,
                language="python",
                node_type="class",
                name=class_name,
                qualified_name=qualified,
                source_code=cls_node.text.decode(),
                line_start=cls_node.start_point.row + 1,
                line_end=cls_node.end_point.row + 1,
                imports=imports,
                fields=class_fields,
                bases=class_bases,
                framework_hints={"field_types": field_types} if field_types else {},
            ))

            # methods inside this class
            for m_match in _matches(_FUNCTION_QUERY, cls_node):
                m_node = m_match["func.def"][0]
                m_name_node = m_match["func.name"][0]
                method_name = m_name_node.text.decode()
                m_qualified = f"{file_stem}.{class_name}.{method_name}"
                params = self._extract_params(m_node, skip_self=True)
                calls = self._extract_calls(m_node)
                call_sites = self._extract_call_sites(m_node)
                return_type = self._extract_return_type(m_node)
                
                # New: Extract behavioral signals
                transitions = self._extract_state_transitions(
                    m_node,
                    class_name=class_name,
                    class_qualified=qualified,
                    enclosing_qualified=m_qualified,
                )
                boundaries = self._detect_boundaries(m_node)
                is_async = self._is_async_function(m_node)

                f_hints = {"transitions": transitions, "boundaries": boundaries}
                if is_async:
                    f_hints["async_boundary"] = True

                # Endpoint? Decorated class method (@router.get) or a verb-named
                # method on a view class (CBV). HIGH-5.
                m_decorators = self._extract_decorators(m_node)
                endpoint_info = self._detect_endpoint(m_decorators)
                if endpoint_info is None and is_view and method_name.lower() in _HTTP_METHODS:
                    endpoint_info = {"method": method_name.upper(), "route": ""}
                m_node_type = "method"
                if endpoint_info:
                    m_node_type = "endpoint"
                    f_hints["method"] = endpoint_info["method"]
                    f_hints["route"] = endpoint_info["route"]

                nodes.append(CodeNode(
                    file_path=fp,
                    language="python",
                    node_type=m_node_type,
                    name=method_name,
                    qualified_name=m_qualified,
                    source_code=m_node.text.decode(),
                    line_start=m_node.start_point.row + 1,
                    line_end=m_node.end_point.row + 1,
                    params=params,
                    calls=calls,
                    call_sites=call_sites,
                    imports=imports,
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
            call_sites = self._extract_call_sites(f_node)
            return_type = self._extract_return_type(f_node)
            decorators = self._extract_decorators(f_node)
            annotations = [d["text"] for d in decorators]

            # Check if this is an HTTP endpoint
            endpoint_info = self._detect_endpoint(decorators)
            node_type = "endpoint" if endpoint_info else "function"
            
            # New: Extract behavioral signals
            transitions = self._extract_state_transitions(
                f_node,
                class_name=None,
                class_qualified=None,
                enclosing_qualified=qualified,
            )
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
                call_sites=call_sites,
                imports=imports,
                return_type=return_type,
                annotations=annotations,
                framework_hints=framework_hints,
            ))

        return nodes

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_state_transitions(
        node,
        class_name: str | None = None,
        class_qualified: str | None = None,
        enclosing_qualified: str | None = None,
    ) -> list[dict]:
        """Look for assignments like self.status = 'ACTIVE'.

        When `class_name` is provided and the receiver is `self` (or `cls`),
        canonicalize `entity` to the enclosing class name and `entity_id` to
        the class's qualified_name — so rollup can distinguish two classes
        with the same short name across modules.

        For duck-typed receivers (`obj.status = ...` where `obj` is a local
        or parameter), `entity_id` becomes `{enclosing_qualified}::{obj}` —
        scoping the entity to the function where the variable lives. Phase 3
        type inference may later upgrade these to real class references.
        """
        transitions = []
        for match in _matches(_ASSIGNMENT_QUERY, node):
            obj_node = match.get("assign.obj", [None])[0]
            attr_node = match.get("assign.attr", [None])[0]
            val_node = match.get("assign.val", [None])[0]

            if obj_node and attr_node and val_node:
                obj_text = obj_node.text.decode()
                attr_text = attr_node.text.decode()
                val_text = val_node.text.decode().strip("\"'")

                if any(kw in attr_text.lower() for kw in ("status", "state", "stage", "phase")):
                    is_self = class_name is not None and obj_text in ("self", "cls")
                    if is_self:
                        entity = class_name
                        entity_id = class_qualified or class_name or ""
                    else:
                        entity = obj_text
                        if enclosing_qualified:
                            entity_id = f"{enclosing_qualified}::{obj_text}"
                        else:
                            entity_id = obj_text
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
        return inside_class(node, class_ranges)

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
    def _extract_call_sites(node) -> list[dict]:
        return extract_call_sites(_CALL_SITE_QUERY, node)

    @staticmethod
    def _extract_bases(cls_node) -> list[str]:
        """Return bare superclass names from `class Foo(Bar, Baz):`.

        Strategy: read the `superclasses` field (an `argument_list`); collect
        `identifier` and `attribute` children, keeping only the final segment
        of dotted names (`abc.ABC` → `ABC`). Call-form bases (`type(...)`),
        keyword args like `metaclass=`, and generic forms (`Foo[Bar]`) are
        ignored — this is a best-effort signal, not a type system.
        """
        bases: list[str] = []
        supers = cls_node.child_by_field_name("superclasses")
        if supers is None:
            return bases
        for child in supers.children:
            if child.type == "identifier":
                bases.append(child.text.decode())
            elif child.type == "attribute":
                # Keep the trailing attribute name: `abc.ABC` → `ABC`.
                last = child.child_by_field_name("attribute")
                if last is not None:
                    bases.append(last.text.decode())
            elif child.type == "subscript":
                # `Generic[T]` → record `Generic`.
                value = child.child_by_field_name("value")
                if value is not None:
                    bases.append(value.text.decode())
        return bases

    @staticmethod
    def _extract_class_fields(cls_node) -> list[str]:
        """Return the attribute names defined on this class (Phase 2d).

        Combines two sources so FSM identity consolidation has the same set of
        fields regardless of whether the codebase uses dataclass-style class
        attributes or `__init__`-style instance attributes:

          - class-level assignments (`status: str` / `COUNT = 0`)
          - `self.X = ...` inside `__init__`

        Order is insertion order with dedup; downstream consumers normalize.
        """
        names: list[str] = []
        seen: set[str] = set()

        # Walk the direct class body only — avoid picking up nested class
        # (e.g. Django `class Meta:`) attributes, which would pollute the
        # fingerprint for the outer entity.
        body = None
        for child in cls_node.children:
            if child.type == "block":
                body = child
                break
        if body is not None:
            for stmt in body.children:
                if stmt.type != "expression_statement":
                    continue
                for inner in stmt.children:
                    if inner.type != "assignment":
                        continue
                    left = inner.child_by_field_name("left")
                    if left is None or left.type != "identifier":
                        continue
                    name = left.text.decode()
                    if name not in seen:
                        names.append(name)
                        seen.add(name)

        for name in PythonParser._extract_self_fields(cls_node):
            if name not in seen:
                names.append(name)
                seen.add(name)
        return names

    @staticmethod
    def _extract_self_fields(cls_node) -> list[str]:
        """Return attribute names assigned via `self.X = ...` inside `__init__`.

        We scope to `__init__` rather than every method because later methods
        often mutate — not define — state. Using only `__init__` keeps the
        fingerprint stable across code that moves mutation logic around.
        """
        names: list[str] = []
        for m_match in _matches(_FUNCTION_QUERY, cls_node):
            m_name = m_match["func.name"][0].text.decode()
            if m_name != "__init__":
                continue
            m_node = m_match["func.def"][0]
            for a_match in _matches(_SELF_FIELD_QUERY, m_node):
                obj_nodes = a_match.get("self.obj", [])
                attr_nodes = a_match.get("self.attr", [])
                if not obj_nodes or not attr_nodes:
                    continue
                if obj_nodes[0].text.decode() != "self":
                    continue
                names.append(attr_nodes[0].text.decode())
        return names

    @staticmethod
    def _bare_type(text: str) -> str:
        """Reduce a Python type annotation to a bare class name:
        `Optional[OrderService]`→`OrderService`, `svc.OrderService`→`OrderService`."""
        t = (text or "").strip()
        if "[" in t and "]" in t:  # Optional[X] / List[X] → innermost arg
            t = t[t.index("[") + 1 : t.rindex("]")].split(",")[-1].strip()
        return t.split(".")[-1].strip()

    @classmethod
    def _extract_field_types(cls, cls_node) -> dict:
        """Map instance-attribute names to their declared type for DI/receiver-type
        call resolution (HIGH-3). Sources, both inside ``__init__``:

          - ``self.x: OrderService = ...``  (annotated attribute)
          - ``self.x = order_service``  where the ctor param ``order_service`` is
            annotated ``OrderService`` (the common typed-DI pattern)
        """
        out: dict[str, str] = {}
        for m_match in _matches(_FUNCTION_QUERY, cls_node):
            if m_match["func.name"][0].text.decode() != "__init__":
                continue
            init = m_match["func.def"][0]
            # Constructor param annotations: {param_name: bare_type}.
            ann: dict[str, str] = {}
            params = init.child_by_field_name("parameters")
            if params is not None:
                for p in params.children:
                    if p.type != "typed_parameter":
                        continue
                    nm = next((c for c in p.children if c.type == "identifier"), None)
                    ty = p.child_by_field_name("type")
                    if nm is not None and ty is not None:
                        ann[nm.text.decode()] = cls._bare_type(ty.text.decode())
            # self.X assignments inside __init__.
            body = init.child_by_field_name("body")
            if body is not None:
                for stmt in body.children:
                    if stmt.type != "expression_statement":
                        continue
                    for a in stmt.children:
                        if a.type != "assignment":
                            continue
                        left = a.child_by_field_name("left")
                        if left is None or left.type != "attribute":
                            continue
                        obj = left.child_by_field_name("object")
                        attr = left.child_by_field_name("attribute")
                        if obj is None or obj.text.decode() != "self" or attr is None:
                            continue
                        fname = attr.text.decode()
                        tnode = a.child_by_field_name("type")
                        if tnode is not None:  # self.x: T = ...
                            out[fname] = cls._bare_type(tnode.text.decode())
                            continue
                        right = a.child_by_field_name("right")
                        if right is not None and right.type == "identifier":
                            rv = right.text.decode()
                            if rv in ann:
                                out[fname] = ann[rv]
        return out

    @staticmethod
    def _extract_imports(root) -> list[dict]:
        """Return one record per imported name with its module, original name, and alias.

        Handles both `import X [as Z]` and `from M import Y [as Z], W [as Q]`.
        Emits one dict per imported binding so the resolver can look up by
        either the local alias (`Z`) or the original short name (`Y`).
        """
        out: list[dict] = []
        for match in _matches(_IMPORT_QUERY, root):
            plain = match.get("import.plain")
            from_ = match.get("import.from")
            if plain:
                out.extend(PythonParser._parse_import_statement(plain[0]))
            elif from_:
                out.extend(PythonParser._parse_import_from_statement(from_[0]))
        return out

    @staticmethod
    def _parse_import_statement(node) -> list[dict]:
        """`import X [as Z], Y [as Q]` — produces one dict per bound name."""
        results: list[dict] = []
        for child in node.children:
            if child.type == "dotted_name":
                mod = child.text.decode()
                results.append({"module": mod, "name": None, "alias": None})
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None:
                    results.append({
                        "module": name_node.text.decode(),
                        "name": None,
                        "alias": alias_node.text.decode() if alias_node is not None else None,
                    })
        return results

    @staticmethod
    def _parse_import_from_statement(node) -> list[dict]:
        """`from M import Y [as Z], W [as Q]` — produces one dict per imported name."""
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return []
        module = module_node.text.decode()
        results: list[dict] = []
        for name_node in node.children_by_field_name("name"):
            if name_node.type == "dotted_name":
                results.append({
                    "module": module,
                    "name": name_node.text.decode(),
                    "alias": None,
                })
            elif name_node.type == "aliased_import":
                inner_name = name_node.child_by_field_name("name")
                inner_alias = name_node.child_by_field_name("alias")
                if inner_name is not None:
                    results.append({
                        "module": module,
                        "name": inner_name.text.decode(),
                        "alias": inner_alias.text.decode() if inner_alias is not None else None,
                    })
        return results

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
        """Detect an HTTP endpoint decorator.

        Handles both idioms:
        - ``@obj.get("/path")`` / ``@router.post("/p")`` (FastAPI, Flask 2.x) —
          the HTTP verb is the attribute name.
        - ``@app.route("/path", methods=["POST"])`` (Flask/Blueprint, the
          dominant Flask idiom, HIGH-5) — verb(s) live in ``methods=[...]``,
          defaulting to GET. Multiple methods are joined with ",".
        """
        import re

        def _first_string(s: str) -> str:
            for ch in ('"', "'"):
                if ch in s:
                    start = s.index(ch) + 1
                    end = s.index(ch, start)
                    if end > start:
                        return s[start:end]
            return ""

        for dec in decorators:
            text = dec["text"]
            if "(" not in text or "." not in text:
                continue
            callable_part, args_part = text.split("(", 1)
            parts = callable_part.rsplit(".", 1)
            if len(parts) != 2:
                continue
            verb = parts[1].lower()
            route = _first_string(args_part)
            if verb == "route":
                # Flask: @app.route("/p", methods=["GET", "POST"]) — default GET.
                mm = re.search(r"methods\s*=\s*[\[\(]([^\]\)]*)[\]\)]", args_part)
                methods = (
                    [s.strip().strip("'\"").upper() for s in mm.group(1).split(",") if s.strip()]
                    if mm else []
                ) or ["GET"]
                return {"method": ",".join(methods), "route": route}
            if verb in _HTTP_METHODS:
                return {"method": verb.upper(), "route": route}
        return None
