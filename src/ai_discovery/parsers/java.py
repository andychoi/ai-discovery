from __future__ import annotations

import re

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Query, QueryCursor

from ..graph.models import CodeNode
from .base import LanguageParser, find_enclosing_guard
from ..core.encoding import encoding_hints, read_source

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

# M6.3: every type declaration. Records behave like classes; interfaces keep their declared methods
# (Spring Data repositories are interfaces whose implementation is generated at run time); enums keep
# their methods.
_TYPE_KINDS = {"class_declaration": "class", "record_declaration": "class", "interface_declaration": "interface",
               "enum_declaration": "enum"}
_CLASS_QUERY = Query(
    JAVA_LANGUAGE,
    """[(class_declaration name: (identifier) @class.name)
        (record_declaration name: (identifier) @class.name)
        (interface_declaration name: (identifier) @class.name)
        (enum_declaration name: (identifier) @class.name)] @class.def""",
)
_PRIMITIVES = frozenset({"var", "int", "long", "short", "byte", "char", "boolean", "float", "double", "void"})

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

_ASSIGNMENT_QUERY = Query(
    JAVA_LANGUAGE,
    """(assignment_expression
        left: [
            (field_access
                object: (_) @assign.obj
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

_CALL_QUERY = Query(
    JAVA_LANGUAGE,
    """[
      (method_invocation name: (identifier) @call.name)
    ]""",
)

# Phase 1.1: per-call-site records (with receiver object) for import-scoped resolution.
_CALL_SITE_QUERY = Query(
    JAVA_LANGUAGE,
    """[
      (method_invocation
        object: (_) @site.receiver
        name: (identifier) @site.name
      ) @site.call
      (method_invocation
        name: (identifier) @site.name
      ) @site.call
    ]""",
)

_DB_OPERATIONS = frozenset({"save", "saveAndFlush", "delete", "deleteById", "update", "execute", "persist", "merge"})
_EXTERNAL_CLIENTS = frozenset({"restTemplate", "webClient", "feign", "httpClient", "okHttpClient", "kafkaTemplate", "jmsTemplate", "sqsClient"})

_PACKAGE_QUERY = Query(
    JAVA_LANGUAGE,
    "(package_declaration [(scoped_identifier) (identifier)] @pkg.name)",   # M6.3: `package a;` too
)

_IMPORT_QUERY = Query(
    JAVA_LANGUAGE,
    "(import_declaration) @import.decl",
)


def _matches(query: Query, node) -> list[dict[str, list]]:
    return [caps for _pat_idx, caps in QueryCursor(query).matches(node)]


def _captures(query: Query, node) -> dict[str, list]:
    return QueryCursor(query).captures(node)


_JAVA_STRING_ARG = re.compile(r'^\(\s*(?:value\s*=\s*)?"([^"]+)"')
_JAVA_MOCK_ASSIGN = re.compile(r"\b(\w+)\s*=\s*(?:Mockito\.)?(?:mock|spy)\s*\(")


def _java_annotations(node) -> dict[str, str]:
    """Annotations in a declaration's `modifiers`: name → string argument ('' for markers / no string)."""
    out: dict[str, str] = {}
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    for a in mods.named_children if mods is not None else []:
        if a.type not in ("marker_annotation", "annotation"):
            continue
        name = a.child_by_field_name("name")
        args = a.child_by_field_name("arguments")
        value = _JAVA_STRING_ARG.search(args.text.decode()) if args is not None else None
        if name is not None:
            out[name.text.decode().rsplit(".", 1)[-1]] = value.group(1) if value else ""
    return out


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
        decoded = read_source(file_path)
        source = decoded.data  # UTF-8 bytes, legacy encodings transcoded (M0.4)
        _enc_hints = encoding_hints(decoded)
        parser = Parser(JAVA_LANGUAGE)
        tree = parser.parse(source)
        root = tree.root_node
        fp = str(file_path)

        # Extract package name for qualified names
        pkg = self._extract_package(root)
        qualifier = pkg if pkg else file_path.stem

        # File-level imports — attached to every node in the file.
        imports = self._extract_imports(root)

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
            node_type = _TYPE_KINDS.get(cls_node.type, "class")
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

            class_fields = self._extract_class_fields(cls_node)
            class_bases = self._extract_bases(cls_node)

            # JPA association fields (@ManyToOne/@JoinColumn etc.) → FK edges.
            if node_type == "db_model":
                relationships = self._extract_relationships(cls_node)
                if relationships:
                    framework_hints["relationships"] = relationships

            # Field/ctor-param types for DI/receiver-type call resolution (HIGH-3).
            field_types = self._extract_field_types(cls_node)
            if field_types:
                framework_hints["field_types"] = field_types
            # M6.3: receiver typing keeps the outer type (`List<Item> items` → `List`), unlike the
            # JPA view above (→ `Item`); the resolver prefers this map.
            receiver_types = self._extract_field_types(cls_node, unwrap=self._receiver_type)
            if receiver_types:
                framework_hints["receiver_types"] = receiver_types
            if node_type == "interface":
                framework_hints["declares_members"] = True
            framework_hints.update(self._di_hints(cls_node, class_name, annotations))   # M6.7.a

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
                imports=imports,
                fields=class_fields,
                bases=class_bases,
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
                call_sites = self._extract_call_sites(m_node)
                return_type = self._extract_return_type(m_node)

                # Check for endpoint annotations
                endpoint_info = self._detect_endpoint(m_annotations, class_route)
                # Check for batch job
                is_batch = "Scheduled" in m_ann_names

                # New: Extract behavioral signals
                transitions = self._extract_state_transitions(
                    m_node,
                    class_name,
                    class_qualified=qualified,
                    enclosing_qualified=m_qualified,
                )
                boundaries = self._detect_boundaries(m_node)

                m_hints: dict = {
                    "transitions": transitions, 
                    "boundaries": boundaries
                }
                local_types = self._extract_local_types(m_node)
                if local_types:
                    m_hints["local_types"] = local_types   # params + locals (M6.3)
                if node_type == "interface" and m_node.child_by_field_name("body") is None:
                    m_hints["abstract"] = True
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
                    call_sites=call_sites,
                    imports=imports,
                    return_type=return_type,
                    annotations=m_ann_names,
                    framework_hints=m_hints,
                ))

        for _n in nodes:
            _n.framework_hints.update(_enc_hints)
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
    def _extract_call_sites(node) -> list[dict]:
        """One record per call site, retaining per-site receiver text.

        Java `method_invocation` has an optional `object` field — absent for
        implicit-`this` calls, present for `foo.bar()` / `Class.static()`.
        """
        sites: list[dict] = []
        for match in _matches(_CALL_SITE_QUERY, node):
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
        """Extract each `import ...;` declaration.

        Java imports bind the trailing segment as the local name: `import
        com.x.OrderService;` makes `OrderService` available. Wildcard
        (`import com.x.*;`) leaves no single binding — we record `name=None`
        and `alias=None` so the resolver can still use the module as a
        plausibility check.
        """
        out: list[dict] = []
        for match in _matches(_IMPORT_QUERY, root):
            decl = match["import.decl"][0]
            is_static = False
            target_text = ""
            for child in decl.children:
                if child.type == "static" or child.text == b"static":
                    is_static = True
                elif child.type in ("scoped_identifier", "identifier"):
                    target_text = child.text.decode()
                elif child.type == "asterisk":
                    target_text = (target_text + ".*") if target_text else "*"
            if not target_text:
                continue
            if target_text.endswith(".*"):
                module = target_text[:-2]
                out.append({"module": module, "name": None, "alias": None, "static": is_static})
            elif "." in target_text:
                module, _, name = target_text.rpartition(".")
                out.append({"module": module, "name": name, "alias": None, "static": is_static})
            else:
                out.append({"module": "", "name": target_text, "alias": None, "static": is_static})
        return out

    @staticmethod
    def _extract_return_type(method_node) -> str | None:
        type_node = method_node.child_by_field_name("type")
        if type_node:
            return type_node.text.decode()
        return None

    @staticmethod
    def _extract_bases(cls_node) -> list[str]:
        """Return superclass + interface names for a Java class (Phase 2d).

        Java expresses inheritance via two separate sibling nodes under
        `class_declaration`: `superclass` (at most one) and `super_interfaces`
        (one list of many). Both contribute to the inheritance graph used by
        the consolidator — an interface's contract-implied fields are just as
        much shared ancestry as a superclass's literal fields.
        """
        bases: list[str] = []
        for child in cls_node.children:
            if child.type == "superclass":
                for sub in child.children:
                    if sub.type == "type_identifier":
                        bases.append(sub.text.decode())
                    elif sub.type == "generic_type":
                        ident = JavaParser._find_first_of_type(sub, "type_identifier")
                        if ident is not None:
                            bases.append(ident.text.decode())
                    elif sub.type == "scoped_type_identifier":
                        # `pkg.Base` — keep trailing segment
                        last = None
                        for c in sub.children:
                            if c.type == "type_identifier":
                                last = c
                        if last is not None:
                            bases.append(last.text.decode())
            elif child.type in ("super_interfaces", "extends_interfaces"):
                for sub in child.children:
                    if sub.type == "type_list":
                        for ti in sub.children:
                            if ti.type == "type_identifier":
                                bases.append(ti.text.decode())
                            elif ti.type == "generic_type":
                                ident = JavaParser._find_first_of_type(ti, "type_identifier")
                                if ident is not None:
                                    bases.append(ident.text.decode())
        return bases

    @staticmethod
    def _find_first_of_type(node, type_name: str):
        for child in node.children:
            if child.type == type_name:
                return child
        return None

    @staticmethod
    def _extract_class_fields(cls_node) -> list[str]:
        """Return field names declared directly on this class (Phase 2d).

        Walks the direct `class_body` so nested inner classes' fields aren't
        mixed in. Each `field_declaration` can list several variable names;
        we emit one entry per bound name, preserving source order with dedup.
        """
        names: list[str] = []
        seen: set[str] = set()
        body = None
        for child in cls_node.children:
            if child.type == "class_body":
                body = child
                break
        if body is None:
            return names
        for member in body.children:
            if member.type != "field_declaration":
                continue
            for child in member.children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    if name_node is None:
                        continue
                    name = name_node.text.decode()
                    if name not in seen:
                        names.append(name)
                        seen.add(name)
        return names

    _REL_CARDINALITY = {
        "ManyToOne": "N:1",
        "OneToMany": "1:N",
        "OneToOne": "1:1",
        "ManyToMany": "N:M",
    }

    @staticmethod
    def _unwrap_type(type_text: str) -> str:
        """Reduce a Java type to its bare entity name.

        `List<OrderItem>` → `OrderItem`, `Map<Long, Product>` → `Product`,
        `com.app.Order` → `Order`. Used to resolve a JPA association field's
        target entity from its declared type.
        """
        t = (type_text or "").strip()
        if "<" in t and ">" in t:
            t = t[t.index("<") + 1 : t.rindex(">")]
            t = t.split(",")[-1].strip()  # Map<K,V> → V
        return t.split(".")[-1].strip()

    @staticmethod
    def _di_hints(cls_node, class_name: str, annotations: list[dict]) -> dict:
        """Spring DI facts for the resolver (M6.7.a): the bean name of a stereotype class, `@Primary`,
        `@Qualifier`/`@Named` values on fields and constructor parameters, and test-double fields."""
        out: dict = {}
        names = {a["name"]: a.get("args", "") for a in annotations}
        stereo = next((n for n in ("Component", "Service", "Repository", "Controller", "RestController",
                                   "Configuration") if n in names), None)
        if stereo:
            value = _JAVA_STRING_ARG.search(names[stereo] or "")
            out["bean_name"] = value.group(1) if value else class_name[:1].lower() + class_name[1:]
        if "Primary" in names:
            out["primary"] = True
        qualifiers: dict[str, str] = {}
        mocks: list[str] = []
        body = cls_node.child_by_field_name("body")
        for member in body.named_children if body is not None else []:
            if member.type == "field_declaration":
                anns = _java_annotations(member)
                declared = [d.child_by_field_name("name") for d in member.named_children
                            if d.type == "variable_declarator"]
                for d in [d for d in declared if d is not None]:
                    name = d.text.decode()
                    if anns.get("Qualifier") or anns.get("Named"):
                        qualifiers[name] = anns.get("Qualifier") or anns.get("Named")
                    if any(a in anns for a in ("Mock", "MockBean", "MockitoBean", "Spy", "SpyBean")):
                        mocks.append(name)
            elif member.type == "constructor_declaration":
                params = member.child_by_field_name("parameters")
                for prm in params.named_children if params is not None else []:
                    anns = _java_annotations(prm)
                    pname = prm.child_by_field_name("name")
                    if pname is not None and (anns.get("Qualifier") or anns.get("Named")):
                        # `this.sender = sender` is the usual shape: the field shares the parameter's name
                        qualifiers[pname.text.decode()] = anns.get("Qualifier") or anns.get("Named")
        mocks += _JAVA_MOCK_ASSIGN.findall(cls_node.text.decode(errors="replace"))
        if qualifiers:
            out["qualifiers"] = qualifiers
        if mocks:
            out["mocks"] = sorted(set(mocks))
        return out

    @staticmethod
    def _receiver_type(type_text: str) -> str:
        """Outer, unqualified type for receiver typing: `List<Item>` → `List`, `a.b.Repo` → `Repo`,
        `Item[]` → `Item[]` (arrays are never a repo type). Primitives and `var` give ''."""
        t = (type_text or "").strip()
        t = t.split("<", 1)[0].strip()
        t = t.rsplit(".", 1)[-1].strip()
        return "" if t in _PRIMITIVES else t

    @classmethod
    def _extract_local_types(cls, m_node) -> dict:
        """Parameter and local-variable names → declared outer type; `var x = new Foo()` → `Foo`."""
        out: dict[str, str] = {}

        def walk(n) -> None:
            for c in n.children:
                if c.type in ("class_body", "lambda_expression", "class_declaration"):
                    continue   # inner scopes declare their own names
                if c.type in ("formal_parameter", "spread_parameter", "catch_formal_parameter",
                              "enhanced_for_statement"):   # `for (PropertySource<?> s : list)` (M6.15)
                    tn = c.child_by_field_name("type")
                    nn = c.child_by_field_name("name")
                    if tn is not None and nn is not None and (t := cls._receiver_type(tn.text.decode())):
                        out.setdefault(nn.text.decode(), t)
                elif c.type == "local_variable_declaration":
                    tn = c.child_by_field_name("type")
                    declared = cls._receiver_type(tn.text.decode()) if tn is not None else ""
                    for d in c.children:
                        if d.type != "variable_declarator":
                            continue
                        nn = d.child_by_field_name("name")
                        value = d.child_by_field_name("value")
                        t = declared
                        if not t and value is not None and value.type == "object_creation_expression":
                            vt = value.child_by_field_name("type")
                            t = cls._receiver_type(vt.text.decode()) if vt is not None else ""
                        if nn is not None and t:
                            out.setdefault(nn.text.decode(), t)
                walk(c)

        walk(m_node)
        # M6.15: a type parameter is not a type — `<T extends BaseEntity>` types `T` locals as the bound;
        # an unbounded parameter (`T`, `E`) types nothing (it must not read as a foreign type)
        bounds = {}
        tparams = m_node.child_by_field_name("type_parameters") or next(
            (c for c in m_node.children if c.type == "type_parameters"), None)
        for tp in (tparams.named_children if tparams is not None else []):
            name = next((c for c in tp.named_children if c.type in ("type_identifier", "identifier")), None)
            bound = next((c for c in tp.named_children if c.type == "type_bound"), None)
            first = next((c for c in bound.named_children), None) if bound is not None else None
            if name is not None:
                bounds[name.text.decode()] = cls._receiver_type(first.text.decode()) if first is not None else ""
        for var, t in list(out.items()):
            if t in bounds or (len(t) == 1 and t.isupper()):
                if bounds.get(t):
                    out[var] = bounds[t]
                else:
                    del out[var]
        return out

    @classmethod
    def _extract_field_types(cls, cls_node, unwrap=None) -> dict:
        """Map field / constructor-param names to their declared (bare) type.

        Enables DI/receiver-type call resolution (HIGH-3): a call
        `orderService.process()` whose receiver `orderService` is a field of type
        `OrderService` can be pinned to `OrderService.process` instead of fanning
        out to every `process` in the codebase. Covers both field injection
        (`@Autowired private OrderService orderService;`) and constructor
        injection (`CheckoutController(OrderService orderService)`).
        """
        out: dict[str, str] = {}
        unwrap = unwrap or cls._unwrap_type
        body = next((c for c in cls_node.children if c.type in ("class_body", "enum_body", "interface_body")), None)
        if body is not None:
            members = list(body.children)
            for m in body.children:   # enum fields live under enum_body_declarations
                if m.type == "enum_body_declarations":
                    members.extend(m.children)
            for member in members:
                if member.type != "field_declaration":
                    continue
                type_node = member.child_by_field_name("type")
                if type_node is None:
                    continue
                tname = unwrap(type_node.text.decode())
                if not tname:
                    continue
                for child in member.children:
                    if child.type == "variable_declarator":
                        nn = child.child_by_field_name("name")
                        if nn is not None:
                            out[nn.text.decode()] = tname
        for match in _matches(_CONSTRUCTOR_QUERY, cls_node):
            ctor = match["ctor.def"][0]
            for child in ctor.children:
                if child.type != "formal_parameters":
                    continue
                for p in child.children:
                    if p.type != "formal_parameter":
                        continue
                    tn = p.child_by_field_name("type")
                    nn = p.child_by_field_name("name")
                    if tn is not None and nn is not None and (t := unwrap(tn.text.decode())):
                        out.setdefault(nn.text.decode(), t)
        return out

    @classmethod
    def _extract_relationships(cls, cls_node) -> list[dict]:
        """Extract JPA association fields as relationship descriptors.

        Each entry: {from_field, to_entity, cardinality}. `to_entity` is the
        field's (element) type; `from_field` is the @JoinColumn name when given,
        else the field name. Only fields annotated @ManyToOne/@OneToMany/
        @OneToOne/@ManyToMany produce edges. FK-aware table docs, Phase 1.
        """
        body = next((c for c in cls_node.children if c.type == "class_body"), None)
        if body is None:
            return []
        rels: list[dict] = []
        for member in body.children:
            if member.type != "field_declaration":
                continue
            ann_names: list[str] = []
            join_column = ""
            for child in member.children:
                if child.type != "modifiers":
                    continue
                for ann in child.children:
                    if ann.type not in ("annotation", "marker_annotation"):
                        continue
                    nm_node = ann.child_by_field_name("name")
                    if nm_node is None:
                        continue
                    nm = nm_node.text.decode()
                    ann_names.append(nm)
                    if nm == "JoinColumn":
                        args = ann.child_by_field_name("arguments")
                        if args is not None:
                            jc = cls._extract_string_arg(args.text.decode(), "name")
                            if jc:
                                join_column = jc
            cardinality = next(
                (cls._REL_CARDINALITY[a] for a in ann_names if a in cls._REL_CARDINALITY), ""
            )
            if not cardinality:
                continue
            type_node = member.child_by_field_name("type")
            target = cls._unwrap_type(type_node.text.decode()) if type_node else ""
            if not target:
                continue
            field_name = ""
            for child in member.children:
                if child.type == "variable_declarator":
                    nn = child.child_by_field_name("name")
                    if nn is not None:
                        field_name = nn.text.decode()
                    break
            rels.append({
                "from_field": join_column or field_name,
                "to_entity": target,
                "cardinality": cardinality,
            })
        return rels

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
    def _extract_state_transitions(
        node,
        class_name: str,
        class_qualified: str | None = None,
        enclosing_qualified: str | None = None,
    ) -> list[dict]:
        """Look for assignments like this.status = 'ACTIVE' or status = 'PAID'.

        `entity_id` is the class's qualified_name for `this`/bare receivers
        (so `com.billing.Order` and `com.ecommerce.Order` stay distinct after
        rollup), and `enclosing_method.qualified_name::obj` for duck-typed
        receivers.
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
