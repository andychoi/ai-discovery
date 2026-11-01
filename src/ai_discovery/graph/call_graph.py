"""Build call graph from CodeNode.calls / call_sites references.

Resolution follows the closed origin vocabulary of ADR-0004
(``graph/origins.py``): every edge has exactly one origin, and the origin
fixes its confidence, edge kind and evidence kind. A call that is ambiguous or
cannot be bound gets **no edge**; it is returned as an ``UnresolvedCall`` with
a reason from ``origins.UNRESOLVED_REASONS``.
"""

from __future__ import annotations

from pathlib import Path

import posixpath
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import origins as O
from .models import CallEdge, CodeNode, ExecutionEdge, ExecutionNode, Scenario, StateTransition

_CLASS_TYPES = ("class", "db_model")
_TYPE_DECLS = _CLASS_TYPES + ("interface", "enum")   # any repo type (M6.2: C# interface/enum nodes)
# Languages whose parser emits every type kind (interfaces too), so "no repo type of that name" really
# means "a library type". Java joins with M6.3 (interface nodes); until then its interfaces look foreign.
_TYPED_DECLS_LANGS = frozenset({"csharp", "java"})   # java since M6.3 (interface/enum/record nodes)
_SELF_RECEIVERS = frozenset({"self", "this", "cls", "Me", "base", "super"})
# Languages where a receiver-less call inside a method means `this.name()`.
_IMPLICIT_THIS = frozenset({"java", "csharp", "vbnet"})
# Languages where a receiver-less call can never reach a class method.
_NO_IMPLICIT_THIS = frozenset({"python", "javascript", "typescript"})
_MAX_INHERIT_DEPTH = 4
_EDGE_TYPE = {"calls": "direct_call", "dispatches_to": "dispatch", "framework_binds": "di_binding"}


@dataclass
class UnresolvedCall:
    caller: str
    callee_name: str
    reason: str
    candidate_count: int = 0
    file: str = ""
    line: int | None = None


@dataclass
class Resolution:
    edges: list[CallEdge] = field(default_factory=list)
    unresolved: list[UnresolvedCall] = field(default_factory=list)


def make_edge(caller: str, callee: str, origin_name: str, **meta) -> CallEdge:
    """A call edge whose confidence/kinds come only from its origin."""
    o = O.origin(origin_name)
    return CallEdge(caller=caller, callee=callee, edge_type=_EDGE_TYPE.get(o.edge_kind, o.edge_kind),
                    confidence=o.confidence,
                    metadata={"resolved_by": o.name, "edge_kind": o.edge_kind,
                              "evidence_kind": o.evidence_kind, **meta})


def _family(language: str) -> str:
    return "javascript" if language in ("javascript", "typescript") else language


def build_call_graph(nodes: list[CodeNode], symbol_index=None) -> list[CallEdge]:
    """Resolved call edges only (see ``resolve_calls`` for unresolved calls)."""
    return resolve_calls(nodes, symbol_index).edges


def resolve_calls(nodes: list[CodeNode], symbol_index=None) -> Resolution:
    """Resolve every call site to at most one edge per target, or an unresolved call.

    Tiers, first match wins (origin → confidence in ``graph/origins.py``):
      scip            symbol index (LSP/SCIP/compiler) when one is supplied
      import_exact    fully-qualified call name, or imported symbol whose module matches its file
      import_module   module/alias import, member resolved
      self_scope      ``self``/``this`` (or receiver-less in Java/C#) method on the enclosing class
      inherited       same, found on an ancestor (≤4 levels, one defining branch)
      receiver_typed  receiver's declared type (field/param, or a type name) known locally
      receiver_global receiver's type found only repo-wide by unique name
      framework       DI: interface-typed receiver, exactly one implementation defines the method
      dispatch        interface-typed receiver, 2..24 implementations (one edge each)
      same_file       receiver-less call, unique callee in the caller's file
      same_package    receiver-less call, unique callee in the caller's package/module
      global_unique   unique name repo-wide, same language, callable, not a builtin
    More than one candidate in a tier → no edge, reason ``ambiguous``. Plus a
    graph-wide ``dispatch`` pass: base-class/interface method → each override.
    """
    r = _Resolver(nodes, symbol_index)
    for node in nodes:
        sites = node.call_sites if node.call_sites else [{"name": c, "receiver": None} for c in node.calls]
        if not sites:
            continue
        imports = _build_import_index(node, extra=r.global_imports(node))
        for site in sites:
            if site.get("name"):
                r.resolve_site(node, site, imports)
    r.dispatch_pass()

    # Dedup (MED-3): one edge per (caller, callee, edge_type), keeping the
    # highest confidence; one unresolved record per (caller, name).
    deduped: dict[tuple[str, str, str], CallEdge] = {}
    for e in r.edges:
        k = (e.caller, e.callee, e.edge_type)
        cur = deduped.get(k)
        if cur is None or e.confidence > cur.confidence:
            deduped[k] = e
    resolved_pairs = {(e.caller, e.metadata.get("call")) for e in deduped.values()}
    seen: set[tuple[str, str]] = set()
    unresolved: list[UnresolvedCall] = []
    for u in r.unresolved:
        k = (u.caller, u.callee_name)
        if k in seen or k in resolved_pairs:
            continue
        seen.add(k)
        unresolved.append(u)
    return Resolution(list(deduped.values()), unresolved)


class _Resolver:
    def __init__(self, nodes: list[CodeNode], symbol_index=None):
        self.symbol_index = symbol_index
        self.qualified: dict[str, CodeNode] = {}
        self.by_name: dict[str, list[CodeNode]] = defaultdict(list)
        # HIGH-3: class qualified_name → {field/param name: declared type}.
        self.field_types: dict[str, dict[str, str]] = {}
        self.receiver_types: dict[str, dict[str, str]] = {}
        # P0-2: interface/base short name → implementing/extending class nodes.
        self.impls: dict[str, list[CodeNode]] = defaultdict(list)
        self.members: dict[str, list[CodeNode]] = defaultdict(list)   # owner qn → direct members
        # M6.2: partial classes — fragments sharing a full name merge field types and bases.
        self.bases_of: dict[str, list[str]] = {}
        for node in nodes:
            fragment = node.node_type in _CLASS_TYPES and node.qualified_name in self.qualified \
                and self.qualified[node.qualified_name].node_type in _CLASS_TYPES
            if not fragment:
                self.qualified[node.qualified_name] = node
                if "." in node.qualified_name:
                    self.members[node.qualified_name.rsplit(".", 1)[0]].append(node)
                self.by_name[node.name].append(node)
            rtypes = (node.framework_hints or {}).get("receiver_types")
            if rtypes:   # M6.3: outer declared types for receiver typing (Java)
                self.receiver_types.setdefault(node.qualified_name, {}).update(
                    {k: v for k, v in rtypes.items() if k not in self.receiver_types.get(node.qualified_name, {})})
            ftypes = (node.framework_hints or {}).get("field_types")
            if ftypes:
                self.field_types.setdefault(node.qualified_name, {}).update(
                    {k: v for k, v in ftypes.items() if k not in self.field_types.get(node.qualified_name, {})})
            if node.node_type in _CLASS_TYPES and node.bases:
                merged = self.bases_of.setdefault(node.qualified_name, [])
                for base in node.bases:
                    if base not in merged:
                        merged.append(base)
                        self.impls[base.rsplit(".", 1)[-1]].append(self.qualified[node.qualified_name])
        self._project_of: dict[str, str] = {}
        self.global_usings: dict[str, list[dict]] = defaultdict(list)   # C# project dir → `global using`s
        for node in nodes:
            if node.language == "csharp":
                for imp in node.imports or []:
                    if imp.get("global") and imp not in self.global_usings[self.project_dir(node.file_path)]:
                        self.global_usings[self.project_dir(node.file_path)].append(imp)
        # Files holding only `global using`s (GlobalUsings.cs) emit no nodes, and `.csproj` <Using> items
        # are not C# at all: read both once per project directory (M6.2).
        for proj in sorted({self.project_dir(n.file_path) for n in nodes if n.language == "csharp"} - {""}):
            for imp in _project_usings(Path(proj)):
                if imp not in self.global_usings[proj]:
                    self.global_usings[proj].append(imp)
        self.files = sorted({n.file_path for n in nodes if n.file_path})
        # M6.5.a: JS/TS module-level definitions by (file, name) — file stems collide across directories
        self.js_top: dict[tuple[str, str], list[CodeNode]] = defaultdict(list)
        self._posix_files: dict[str, str] = {}
        self.sfc_components: dict[str, CodeNode] = {}   # M6.5.b: `.vue` file → its component node
        self._di_registrations: list[dict] | None = None   # M6.7.a/b, read lazily
        self._spring_beans: dict | None = None
        for n in nodes:
            if _family(n.language) == "javascript" and n.file_path:
                posix = posixpath.normpath(n.file_path.replace("\\", "/"))
                self._posix_files[posix] = n.file_path
                if n.qualified_name == f"{Path(n.file_path).stem}.{n.name}":
                    self.js_top[(posix, n.name)].append(n)
                if n.node_type == "ui_component" and (n.framework_hints or {}).get("vue") == "sfc":
                    self.sfc_components[posix] = n
        self._js_files: dict[tuple[str, str], str | None] = {}
        self._reexports: dict[str, list[dict]] = {}
        self._import_cache: dict[tuple, bool] = {}
        self.edges: list[CallEdge] = []
        self.unresolved: list[UnresolvedCall] = []

    def project_dir(self, file_path: str) -> str:
        """Nearest ancestor directory holding a *.csproj (C# `global using` scope); '' when none."""
        start = str(Path(file_path).parent)
        if start in self._project_of:
            return self._project_of[start]
        found = ""
        for d in [Path(start), *Path(start).parents]:
            try:
                if any(d.glob("*.csproj")):
                    found = str(d)
                    break
            except OSError:
                break
        self._project_of[start] = found
        return found

    def global_imports(self, node: CodeNode) -> list[dict]:
        if node.language != "csharp" or not self.global_usings:
            return []
        return self.global_usings.get(self.project_dir(node.file_path), [])

    def bases(self, cls: CodeNode) -> list[str]:
        return self.bases_of.get(cls.qualified_name, cls.bases or [])

    # ── recording ────────────────────────────────────────────────────────
    def _edge(self, caller: CodeNode, target: CodeNode, origin_name: str, call: str, **meta) -> None:
        if target.qualified_name != caller.qualified_name:
            self.edges.append(make_edge(caller.qualified_name, target.qualified_name, origin_name,
                                        call=call, **meta))

    def _miss(self, caller: CodeNode, site: dict, name: str, reason: str, count: int = 0) -> None:
        self.unresolved.append(UnresolvedCall(caller.qualified_name, name, reason, count, caller.file_path,
                                              site.get("line") or caller.line_start or None))

    # ── per-site resolution ──────────────────────────────────────────────
    def resolve_site(self, caller: CodeNode, site: dict, imports: dict[str, dict]) -> None:
        call_ref = site["name"]
        receiver = site.get("receiver")
        if site.get("binding") in ("template_handler", "template_component"):
            return self._template_site(caller, site, imports)

        if self.symbol_index is not None:
            indexed = self.symbol_index.resolve(caller.qualified_name, call_ref, receiver)
            if indexed and indexed in self.qualified and indexed != caller.qualified_name:
                self._edge(caller, self.qualified[indexed], "scip", call_ref)
                return

        if call_ref in self.qualified:
            # a markup page naming its code-behind handler / bean class is a framework binding
            origin_name = "framework" if caller.node_type == "ui_component" else "import_exact"
            self._edge(caller, self.qualified[call_ref], origin_name, call_ref)
            return

        name = call_ref
        if "." in call_ref and not receiver:
            # `PaymentService.charge` from a deduped calls list: split the receiver off.
            receiver, name = call_ref.rsplit(".", 1)

        if name in O.DYNAMIC_CALLS:
            return self._miss(caller, site, call_ref, "dynamic")

        if self._via_import(caller, site, name, receiver, imports, call_ref):
            return

        if receiver and (receiver.startswith("super(") or receiver in ("super", "base")):
            # `super().m()` / `base.M()`: only an ancestor can answer
            return self._inherited_only(caller, site, name, call_ref)
        if receiver and receiver in _SELF_RECEIVERS:
            return self._self_scope(caller, site, name, call_ref, explicit=True)
        if not receiver:
            if self._resolve_type(name):
                return self._type_reference(caller, site, name, imports, call_ref)
            if _family(caller.language) in _IMPLICIT_THIS and self._self_scope(caller, site, name, call_ref,
                                                                               explicit=False):
                return
            if self._local(caller, site, name, call_ref):
                return
            if _family(caller.language) in _IMPLICIT_THIS:
                # a bare Java/C# call outside the class hierarchy is a static
                # import or inherited from a type outside the repo
                return self._miss(caller, site, call_ref, "external" if self._has_foreign_base(caller)
                                  else "not_found")
        elif self._via_receiver_type(caller, site, name, receiver, imports, call_ref):
            return

        self._global(caller, site, name, receiver, imports, call_ref)

    def _via_import(self, caller, site, name, receiver, imports, call_ref) -> bool:
        """Receiver head (or the bare name itself) is an imported binding."""
        if receiver:
            imp = imports.get(receiver.split(".")[0])
        else:
            imp = imports.get(name)
            if imp is not None and imp.get("name") != name and imp.get("alias") != name:
                imp = None
        if imp is None:
            return False
        module = imp.get("module") or ""
        imported = imp.get("name")
        if _family(caller.language) == "javascript" and module.startswith("."):
            handled = self._via_js_module(caller, site, name, receiver, imp, call_ref)
            if handled is not None:
                return handled
        exact: list[CodeNode] = []
        loose: list[CodeNode] = []
        for cand in self.by_name.get(name, []):
            qn = cand.qualified_name
            if receiver and imported:
                # `from M import Cls` + `Cls.name()`
                if qn.endswith(f".{imported}.{name}") or qn == f"{imported}.{name}":
                    (exact if _module_matches_file(module, cand.file_path, caller.file_path) else loose).append(cand)
            elif receiver:
                # `import M` + `M.name()`
                tail = module.split(".")[-1] if module else ""
                if qn == f"{module}.{name}" or (tail and qn == f"{tail}.{name}"):
                    loose.append(cand)
            else:
                # `from M import name` + `name()`
                if (qn.endswith(f".{name}") or qn == name) and _module_matches_file(module, cand.file_path,
                                                                                    caller.file_path):
                    exact.append(cand)
        for group, origin_name in ((exact, "import_exact"), (loose, "import_module")):
            if len(group) == 1:
                self._edge(caller, group[0], origin_name, call_ref, receiver=receiver, **{"import": imp})
                return True
            if len(group) > 1:
                self._miss(caller, site, call_ref, "ambiguous", len(group))
                return True
        if not self._import_in_repo(imp, caller.file_path):
            # bound by an import from a library (`from flask_babel import get_locale`,
            # `current_app.x.index()`): a same-named repo symbol must never answer for it
            self._miss(caller, site, call_ref, "external")
            return True
        return False

    def _template_site(self, caller, site, imports) -> None:
        """M6.5.b: a Vue template binding is a framework binding. A handler names a function of the same
        file (matched by file, so two `Index.vue` files never cross-link); a component tag names the `.vue`
        file its import points to. A tag imported from a library is ``external``."""
        name = site["name"]
        if site["binding"] == "template_handler":
            posix = posixpath.normpath(caller.file_path.replace("\\", "/"))
            found = [t for t in self.js_top.get((posix, name), []) if t.node_type in O.CALLABLE_TYPES]
            if len(found) == 1:
                self._edge(caller, found[0], "framework", name, binding="template_handler")
            else:
                self._miss(caller, site, name, "ambiguous" if found else "not_found", len(found))
            return
        module = (imports.get(name) or {}).get("module") or ""
        if not module.startswith("."):
            return self._miss(caller, site, name, "external")
        file = self._js_module_file(module, caller.file_path)
        target = self.sfc_components.get(file) if file else None
        if target is not None:
            self._edge(caller, target, "framework", name, binding="template_component")
        else:
            self._miss(caller, site, name, "not_found")

    def _via_js_module(self, caller, site, name, receiver, imp, call_ref) -> bool | None:
        """M6.5.a: a relative JS/TS import (ES or CommonJS) names the module file, so the call binds to the
        function defined there — following barrel re-exports — instead of a repo-wide name guess.
        None = not decidable here (the module is not a repo file, or the binding is used as an object)."""
        file = self._js_module_file(imp.get("module") or "", caller.file_path)
        if file is None:
            return None
        if receiver:
            if imp.get("name") is not None or "." in receiver:
                return None   # `import { svc }` + `svc.m()`, or `ns.obj.m()`: a property, not a module function
            lookups = [name]
        else:
            lookups = [imp.get("name") or name] + (["default"] if imp.get("default") else [])
        for lookup in lookups:
            found = {t.qualified_name + t.file_path: t for t in self._js_exports(file, lookup, 0, set())
                     if t.node_type in O.CALLABLE_TYPES and t.qualified_name != caller.qualified_name}
            if len(found) == 1:
                self._edge(caller, next(iter(found.values())), "import_exact", call_ref,
                           **({"receiver": receiver} if receiver else {}), **{"import": imp})
                return True
            if found:
                self._miss(caller, site, call_ref, "ambiguous", len(found))
                return True
        return None

    def _js_module_file(self, module: str, importer: str) -> str | None:
        """`./x`, `../svc/x` → the posix path of the file it names (extensions, `index` files), known to the
        scan or present on disk (a barrel that declares nothing emits no node). None if not found."""
        key = (module, posixpath.dirname(importer.replace("\\", "/")))
        if key in self._js_files:
            return self._js_files[key]
        base = posixpath.normpath(posixpath.join(key[1], module))
        cands = [base + ext for ext in ("", *_JS_EXTS)] + [f"{base}/index{ext}" for ext in _JS_EXTS]
        hit = next((c for c in cands if c in self._posix_files), None) or \
            next((c for c in cands if Path(c).suffix in _JS_EXTS and Path(c).is_file()), None)
        self._js_files[key] = hit
        return hit

    def _js_exports(self, file: str, name: str, depth: int, seen: set) -> list[CodeNode]:
        """Definitions *name* resolves to from module *file*: its own top-level symbol, else its re-exports
        (`export * from`, `export { a as b } from`, `export { a as b }`, `export default a`) followed to a
        fixed point — cycle-safe and depth-capped."""
        if (file, name) in seen or depth > _MAX_REEXPORT_DEPTH:
            return []
        seen.add((file, name))
        own = self.js_top.get((file, name))
        if own:
            return list(own)
        if file not in self._reexports:
            from ..parsers.javascript import extract_reexports
            self._reexports[file] = extract_reexports(Path(self._posix_files.get(file, file)))
        out: list[CodeNode] = []
        for rx in self._reexports[file]:
            if rx["exported"] not in (name, "*") or (rx["exported"] == "*" and name == "default"):
                continue
            inner = name if rx["name"] == "*" else rx["name"]
            if rx["module"] is None:
                out += self._js_exports(file, inner, depth + 1, seen)
            elif (target := self._js_module_file(rx["module"], file)) is not None:
                out += self._js_exports(target, inner, depth + 1, seen)
        return out

    def _import_in_repo(self, imp: dict, importer: str) -> bool:
        """Does this import refer to code in the repository (vs. a library)?"""
        module = imp.get("module") or ""
        key = (module, imp.get("name"), importer if module.startswith(".") else "")
        hit = self._import_cache.get(key)
        if hit is None:
            name = imp.get("name")
            hit = (module.startswith(".")
                   or any(qn in self.qualified or qn in self.members
                          for qn in (module, f"{module}.{name}" if name else module, name or ""))
                   or any(_module_matches_file(module, f, importer) for f in self.files))
            self._import_cache[key] = hit
        return hit

    def _class_of(self, caller: CodeNode) -> CodeNode | None:
        owner = self.qualified.get(caller.qualified_name.rsplit(".", 1)[0])
        return owner if owner is not None and owner.node_type in _CLASS_TYPES else None

    def _self_scope(self, caller, site, name, call_ref, *, explicit: bool) -> bool:
        cls = self._class_of(caller)
        if cls is not None:
            target = self.qualified.get(f"{cls.qualified_name}.{name}")
            if target is not None and target.qualified_name != caller.qualified_name:
                self._edge(caller, target, "self_scope", call_ref)
                return True
            found = self._on_ancestors(cls, name)
            if len(found) == 1:
                self._edge(caller, found[0], "inherited", call_ref)
                return True
            if len(found) > 1:
                self._miss(caller, site, call_ref, "ambiguous", len(found))
                return True
        if cls is None and explicit and (caller.framework_hints or {}).get("vue") == "options_method":
            # M6.5.b: `this.load()` inside a Vue Options-API method — a method of the same component
            posix = posixpath.normpath(caller.file_path.replace("\\", "/"))
            found = [t for t in self.js_top.get((posix, name), [])
                     if t.node_type in O.CALLABLE_TYPES and t.qualified_name != caller.qualified_name]
            if len(found) == 1:
                self._edge(caller, found[0], "self_scope", call_ref)
                return True
        if explicit:
            foreign = cls is not None and self._has_foreign_base(caller)
            self._miss(caller, site, call_ref, "external" if foreign else "not_found")
            return True
        return False

    def _inherited_only(self, caller, site, name, call_ref) -> None:
        cls = self._class_of(caller)
        found = self._on_ancestors(cls, name) if cls is not None else []
        if len(found) == 1:
            self._edge(caller, found[0], "inherited", call_ref)
        elif found:
            self._miss(caller, site, call_ref, "ambiguous", len(found))
        else:
            self._miss(caller, site, call_ref, "external" if self._has_foreign_base(caller) else "not_found")

    def _resolve_type(self, short: str) -> list[CodeNode]:
        return [n for n in self.by_name.get(short.rsplit(".", 1)[-1], []) if n.node_type in _CLASS_TYPES]

    def _on_ancestors(self, cls: CodeNode, name: str) -> list[CodeNode]:
        """Method *name* on the nearest ancestors of *cls* that define it."""
        frontier, seen = [cls], {cls.qualified_name}
        for _ in range(_MAX_INHERIT_DEPTH):
            parents: list[CodeNode] = []
            for c in frontier:
                for base in self.bases(c):
                    types = self._resolve_type(base)
                    if len(types) == 1 and types[0].qualified_name not in seen:
                        seen.add(types[0].qualified_name)
                        parents.append(types[0])
            found = {t.qualified_name: t for p in parents
                     if (t := self.qualified.get(f"{p.qualified_name}.{name}")) is not None}
            if found:
                return list(found.values())
            frontier = parents
            if not frontier:
                break
        return []

    def _has_foreign_base(self, caller: CodeNode) -> bool:
        cls = self._class_of(caller)
        return cls is not None and any(not self._resolve_type(b) for b in self.bases(cls))

    def _is_local_type(self, caller: CodeNode, t: CodeNode, imports: dict[str, dict]) -> bool:
        if t.file_path == caller.file_path:
            return True
        pkg = t.qualified_name.rsplit(".", 1)[0].removeprefix("proto::")   # proto: its csharp_namespace (M6.15)
        if caller.qualified_name.startswith(pkg + "."):
            return True
        for imp in imports.values():
            module = imp.get("module") or ""
            if imp.get("name") == t.name and (not module or t.qualified_name in (f"{module}.{t.name}", t.name)
                                              or _module_matches_file(module, t.file_path, caller.file_path)):
                return True
            if not imp.get("name") and module == pkg:   # C# `using Ns;`, Java `import pkg.*`
                return True
        return False

    def _type_reference(self, caller, site, name, imports, call_ref) -> None:
        """A bare type name: constructor call (`new X()`, `X()`) or a DI constructor parameter."""
        types = [t for t in self._resolve_type(name) if t.qualified_name != caller.qualified_name]
        local = [t for t in types if self._is_local_type(caller, t, imports)]
        if len(local) == 1:
            self._edge(caller, local[0], "receiver_typed", call_ref)
        elif len(types) == 1:
            self._edge(caller, types[0], "receiver_global", call_ref)
        else:
            self._miss(caller, site, call_ref, "ambiguous" if types else "not_found", len(types))

    def _via_receiver_type(self, caller, site, name, receiver, imports, call_ref) -> bool:
        """Receiver is a typed field/param of the enclosing class, or a type name itself."""
        cls = self._class_of(caller)
        mocks = set((caller.framework_hints or {}).get("mocks") or ()) | \
            set(((cls.framework_hints or {}).get("mocks") or ()) if cls is not None else ())
        # M6.7.a: a test double (`Substitute.For<IRepo>()`, `@Mock`) answers the call: it may target the
        # declared type's own method, but never reaches an implementation class
        mock = bool(mocks) and receiver.removeprefix("this.").split(".")[0] in mocks
        ftypes = self.field_types.get(cls.qualified_name, {}) if cls is not None else {}
        rtypes = self.receiver_types.get(cls.qualified_name, {}) if cls is not None else {}
        local = (caller.framework_hints or {}).get("local_types") or {}
        last = receiver.rsplit(".", 1)[-1]
        # M6.3: a parameter/local shadows a field; the outer type (`List`) beats the JPA view (`Item`)
        type_name = (local.get(receiver) if "." not in receiver else None) or rtypes.get(receiver) \
            or rtypes.get(last) or ftypes.get(receiver) or ftypes.get(last)
        if not type_name and receiver.endswith(")") and _family(caller.language) in _TYPED_DECLS_LANGS:
            # M6.16: `orders.find(id).total()` — the receiver is a call; type it from the declared return
            # type of the repo method it resolves to (a library return type is then `external` below)
            type_name = self._returned_type(caller, receiver, imports, 0) or None
        declared = bool(type_name)
        if not type_name and mock:   # `var bus = Substitute.For<IEventBus>()`: untyped here, still a double
            self._miss(caller, site, call_ref, "external")
            return True
        if not type_name:
            # static call on a type name: `OrderService.create()` (JS/C#/Java statics)
            if receiver[:1].isupper() and self._resolve_type(receiver):
                type_name = receiver
            else:
                return False
        types = self._resolve_type(type_name)
        if not types:
            # an interface (not a class node): go straight to the implementations
            if not mock and self._via_implementations(caller, site, name, type_name.rsplit(".", 1)[-1], receiver,
                                                      call_ref, bases=None):
                return True
            short = type_name.rsplit(".", 1)[-1]
            if self._via_interface_declaration(caller, site, name, short, receiver, imports, call_ref):
                return True
            if declared and self._via_extension(caller, site, name, short, receiver, call_ref):
                return True
            if mock:
                self._miss(caller, site, call_ref, "external")
                return True
            if declared and _family(caller.language) == "javascript" and \
                    not ((imports.get(short) or {}).get("module") or ".").startswith("."):
                # M6.6: a TS type imported from a package (`HttpClient` from '@angular/common/http')
                self._miss(caller, site, call_ref, "external")
                return True
            if declared and _family(caller.language) in _TYPED_DECLS_LANGS \
                    and not any(n.node_type in _TYPE_DECLS for n in self.by_name.get(short, [])):
                # M6.2: the receiver's declared type is outside the repo (`ILogger<T> _logger`) and nothing
                # in the repo implements it — its methods are the library's; never guess a repo method by name
                self._miss(caller, site, call_ref, "external")
                return True
            return False
        local = [t for t in types if self._is_local_type(caller, t, imports)]
        if len(local) == 1:
            t, origin_name = local[0], "receiver_typed"
        elif len(types) == 1:
            t, origin_name = types[0], "receiver_global"
        else:
            self._miss(caller, site, call_ref, "di_ambiguous", len(types))
            return True

        target = self.qualified.get(f"{t.qualified_name}.{name}")
        if target is not None:
            self._edge(caller, target, origin_name, call_ref, receiver=receiver)
            return True
        found = self._on_ancestors(t, name)
        if len(found) == 1:
            self._edge(caller, found[0], "inherited", call_ref, receiver=receiver)
            return True
        if mock:
            self._miss(caller, site, call_ref, "external")
            return True
        return self._via_implementations(caller, site, name, t.name, receiver, call_ref, bases=t.bases or [])

    def _via_extension(self, caller, site, name, short, receiver, call_ref) -> bool:
        """C# extension method in the repo on a type outside it (`builder.RegisterViews()` with
        `static MauiAppBuilder RegisterViews(this MauiAppBuilder b)`): the declared type picks it (M6.16)."""
        found = [n for n in self.by_name.get(name, []) if n.language == caller.language
                 and (n.framework_hints or {}).get("extends_type") == short]
        if len(found) == 1:
            self._edge(caller, found[0], "receiver_typed", call_ref, receiver=receiver)
            return True
        if found:
            self._miss(caller, site, call_ref, "ambiguous", len(found))
            return True
        return False

    def _returned_type(self, caller, call_text: str, imports, depth: int) -> str:
        """Outer declared return type of the repo method *call_text* (`a.find(x)`, `make()`, `this.m()`,
        `a.b().c()`) resolves to; '' when it does not resolve to exactly one repo method with a type."""
        head = _strip_call(call_text)
        if not head or head.startswith(".") or depth > _MAX_CHAIN_DEPTH:
            return ""   # `.m()`: the chain's head was lost (e.g. split by an `#if` block) — untypable
        inner, _, method = head.rpartition(".")
        if "(" in method or not method.isidentifier():
            return ""
        type_name = self._receiver_type_name(caller, inner or None, imports, depth)
        cls = self._receiver_class(caller, inner or None, imports, depth, type_name)
        target = None
        if cls is not None:
            target = self.qualified.get(f"{cls.qualified_name}.{method}")
            if target is None:
                found = self._on_ancestors(cls, method)
                target = found[0] if len(found) == 1 else None
        elif type_name:   # a foreign type: a repo extension method on it (`services.AddOrders()`)
            ext = [n for n in self.by_name.get(method, []) if n.language == caller.language
                   and (n.framework_hints or {}).get("extends_type") == type_name.rsplit(".", 1)[-1]]
            target = ext[0] if len(ext) == 1 else None
        return _outer_type(target.return_type) if target is not None else ""

    def _receiver_type_name(self, caller, receiver: str | None, imports, depth: int) -> str:
        """Declared (or returned) type name of a receiver expression; '' when unknown or `this`."""
        if receiver is None or receiver in ("this", "base"):
            return ""
        if receiver.endswith(")"):
            return self._returned_type(caller, receiver, imports, depth + 1)
        cls = self._class_of(caller)
        local = (caller.framework_hints or {}).get("local_types") or {}
        rtypes = self.receiver_types.get(cls.qualified_name, {}) if cls is not None else {}
        ftypes = self.field_types.get(cls.qualified_name, {}) if cls is not None else {}
        last = receiver.removeprefix("this.")
        return local.get(receiver) or rtypes.get(last) or ftypes.get(last) or \
            (receiver if receiver[:1].isupper() else "")

    def _receiver_class(self, caller, receiver: str | None, imports, depth: int,
                        type_name: str | None = None) -> CodeNode | None:
        """The one repo type (class, or Java interface with members) a receiver expression denotes."""
        if receiver is None or receiver in ("this", "base"):
            return self._class_of(caller)
        if type_name is None:
            type_name = self._receiver_type_name(caller, receiver, imports, depth)
        if not type_name:
            return None
        short = type_name.rsplit(".", 1)[-1]
        types = self._resolve_type(short) or [n for n in self.by_name.get(short, [])
                                              if n.node_type == "interface" and (n.framework_hints or {}).get("declares_members")]
        local_types = [t for t in types if self._is_local_type(caller, t, imports)]
        pick = local_types if len(local_types) == 1 else types
        return pick[0] if len(pick) == 1 else None

    def _via_interface_declaration(self, caller, site, name, short, receiver, imports, call_ref) -> bool:
        """M6.3: a repo interface whose members are emitted (Java) and no repo class implements the call
        — e.g. a Spring Data repository: the call reaches the declared method (implemented at run time).
        A method the interface only inherits from a library interface (`save` from JpaRepository) is
        ``external``; one it neither declares nor inherits is ``not_found``."""
        ifaces = [n for n in self.by_name.get(short, [])
                  if n.node_type == "interface" and (n.framework_hints or {}).get("declares_members")]
        if not ifaces:
            return False
        local = [i for i in ifaces if self._is_local_type(caller, i, imports)]
        if len(local) == 1:
            iface, origin_name = local[0], "receiver_typed"
        elif len(ifaces) == 1:
            iface, origin_name = ifaces[0], "receiver_global"
        else:
            self._miss(caller, site, call_ref, "di_ambiguous", len(ifaces))
            return True
        target = self.qualified.get(f"{iface.qualified_name}.{name}")
        if target is not None:
            self._edge(caller, target, origin_name, call_ref, receiver=receiver)
            return True
        frontier, seen = [iface], {iface.qualified_name}
        for _ in range(_MAX_INHERIT_DEPTH):
            parents = [p for b in (frontier[0].bases or []) for p in self.by_name.get(b.rsplit(".", 1)[-1], [])
                       if p.node_type == "interface" and p.qualified_name not in seen] if frontier else []
            found = [t for p in parents if (t := self.qualified.get(f"{p.qualified_name}.{name}")) is not None]
            if len(found) == 1:
                self._edge(caller, found[0], "inherited", call_ref, receiver=receiver)
                return True
            if not parents:
                break
            seen.update(p.qualified_name for p in parents)
            frontier = parents
        foreign = any(not any(n.node_type in _TYPE_DECLS for n in self.by_name.get(b.rsplit(".", 1)[-1], []))
                      for b in iface.bases or [])
        self._miss(caller, site, call_ref, "external" if foreign else "not_found")
        return True

    def _via_implementations(self, caller, site, name, type_short, receiver, call_ref, *, bases) -> bool:
        """P0-2 interface → implementation(s) defining *name*.

        ``bases`` is the declared type's base list, or None when the type is
        not a node (then no implementation means "not ours": return False).
        """
        want = _imported_qn(caller, type_short)
        impl_targets = {x.qualified_name: x for impl in self.impls.get(type_short, [])
                        if (x := self.qualified.get(f"{impl.qualified_name}.{name}")) is not None
                        and not _different_import(want, _imported_qn(impl, type_short))}
        if not impl_targets and bases is None:
            return False
        if len(impl_targets) > 1:
            chosen = self._di_select(caller, receiver, type_short, list(impl_targets.values()))
            if chosen is not None:
                if len(chosen) != 1:
                    self._miss(caller, site, call_ref, "di_ambiguous", len(chosen))
                    return True
                self._edge(caller, chosen[0], "framework", call_ref, receiver=receiver, binding="di")
                return True
        if len(impl_targets) == 1:
            self._edge(caller, next(iter(impl_targets.values())), "framework", call_ref, receiver=receiver)
        elif len(impl_targets) > O.DISPATCH_CAP:
            self._miss(caller, site, call_ref, "dispatch_cap", len(impl_targets))
        elif impl_targets:
            for x in impl_targets.values():
                self._edge(caller, x, "dispatch", call_ref, receiver=receiver)
        else:
            # e.g. `orderRepository.findAll()` inherited from a framework base type
            foreign = any(not self._resolve_type(b) for b in bases or [])
            self._miss(caller, site, call_ref, "external" if foreign else "not_found")
        return True

    def _di_select(self, caller, receiver, type_short, targets: list[CodeNode]) -> list[CodeNode] | None:
        """M6.7.a/b: the implementation(s) the DI container injects for *type_short*, among *targets*
        (implementation methods). None = no DI fact applies (dispatch as before)."""
        owner = {t.qualified_name: self.qualified.get(t.qualified_name.rsplit(".", 1)[0]) for t in targets}
        targets = [t for t in targets if owner[t.qualified_name] is not None]
        if _family(caller.language) == "java":
            return self._spring_select(caller, receiver, targets, owner)
        if caller.language == "csharp":
            return self._registered_select(caller, type_short, targets, owner)
        return None

    def _spring_select(self, caller, receiver, targets, owner) -> list[CodeNode] | None:
        beans = self.spring_beans()
        cls = self._class_of(caller)
        field = (receiver or "").removeprefix("this.").split(".")[0]
        qualifier = ((cls.framework_hints or {}).get("qualifiers") or {}).get(field) if cls is not None else None
        if qualifier:                                   # @Qualifier("sms") → the bean named sms
            impl = beans["names"].get(qualifier)
            return [t for t in targets if (owner[t.qualified_name].framework_hints or {}).get("bean_name") == qualifier
                    or owner[t.qualified_name].name == impl]
        known = [t for t in targets if owner[t.qualified_name].name in beans["classes"]]
        if len(known) == 1 and len(known) < len(targets):   # one implementation is a bean, the rest are not
            return known
        primary = [t for t in (known or targets) if owner[t.qualified_name].name in beans["primary"]]
        if len(primary) == 1:                           # @Primary
            return primary
        return None

    def spring_beans(self) -> dict:
        """Known Spring beans: stereotype classes, `@Bean` factory products, XML `<bean>`s (M6.7.b).
        ``classes``: implementation short names; ``names``: bean name → implementation; ``primary``."""
        if self._spring_beans is None:
            classes: set[str] = set()
            names: dict[str, str] = {}
            primary: set[str] = set()
            for n in self.qualified.values():
                h = n.framework_hints or {}
                if n.language != "java":
                    continue
                if h.get("bean_name") and n.node_type in _CLASS_TYPES:
                    classes.add(n.name)
                    names.setdefault(h["bean_name"], n.name)
                    if h.get("primary"):
                        primary.add(n.name)
                bean = h.get("bean")
                if bean and bean.get("impl"):
                    classes.add(bean["impl"])
                    names.setdefault(bean["name"], bean["impl"])
                    if bean.get("primary"):
                        primary.add(bean["impl"])
            for bean_id, impl, is_primary in self._xml_beans():
                classes.add(impl)
                if bean_id:
                    names.setdefault(bean_id, impl)
                if is_primary:
                    primary.add(impl)
            self._spring_beans = {"classes": classes, "names": names, "primary": primary}
        return self._spring_beans

    def _xml_beans(self) -> list[tuple[str, str, bool]]:
        """`<bean id|name="x" class="a.b.Impl" [primary="true"]>` in the XML files of each Java module
        (nearest directory with a pom.xml / build.gradle)."""
        roots: set[Path] = set()
        for f in self.files:
            if not f.endswith(".java"):
                continue
            for d in Path(f).parents:
                if any((d / b).exists() for b in ("pom.xml", "build.gradle", "build.gradle.kts")):
                    roots.add(d)
                    break
        out: list[tuple[str, str, bool]] = []
        for root in sorted(roots):
            for x in root.rglob("*.xml"):
                if any(part in ("target", "build", "node_modules", ".git") for part in x.parts) or x.name == "pom.xml":
                    continue
                try:
                    if x.stat().st_size > 1_000_000:
                        continue
                    text = x.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for tag in _XML_BEAN.findall(text):
                    attrs = dict(_XML_ATTR.findall(tag))
                    if attrs.get("class"):
                        out.append((attrs.get("id") or attrs.get("name", "").split(",")[0].strip(),
                                    attrs["class"].rsplit(".", 1)[-1].replace("$", "."), attrs.get("primary") == "true"))
        return out

    def _registered_select(self, caller, type_short, targets, owner) -> list[CodeNode] | None:
        regs = self.di_registrations()
        here = self.project_dir(caller.file_path)
        for pool in ([r for r in regs if r["project"] == here], regs):   # the caller's composition root first
            chosen = [t for t in targets if any(self._registers(r, owner[t.qualified_name], type_short) for r in pool)]
            if len(chosen) > 1:   # same-named classes in several projects: the caller's own project's
                mine = [t for t in chosen if self.project_dir(t.file_path) == here]
                chosen = mine if len(mine) == 1 else chosen
            if chosen:
                return chosen
        return None

    def _registers(self, reg: dict, cls: CodeNode, type_short: str) -> bool:
        """Does registration *reg* (interface or None for a single-type one, implementation short name, the
        registering file's namespaces) register class *cls* for *type_short*?"""
        if reg["iface"] not in (type_short, None) or reg["impl"] != cls.name:
            return False
        ns = cls.qualified_name.rsplit(".", 1)[0] if "." in cls.qualified_name else ""
        return ns in reg["scopes"] or len(self._resolve_type(cls.name)) == 1

    def di_registrations(self) -> list[dict]:
        """Container registrations from every `.cs` file of every project (a top-level `Program.cs` emits no
        node, so files are read): ASP.NET `Add{Scoped,Transient,Singleton,HttpClient}<I, T>()` / `TryAdd*`,
        single-type `Add*<T>()`, factories `Add*<I>(sp => new T(..))`, Unity `RegisterType<I, T>()`, Autofac
        `RegisterType<T>().As<I>()`. Each record keeps the namespaces visible in the registering file."""
        if self._di_registrations is None:
            regs: list[dict] = []
            projects = sorted({self.project_dir(f) for f in self.files if f.endswith(".cs")} - {""})
            for proj in projects:
                project_usings = {u.get("module") for u in self.global_usings.get(proj, []) if u.get("module")}
                for f in Path(proj).rglob("*.cs"):
                    if any(part in ("bin", "obj") for part in f.parts):
                        continue
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if "Add" not in text and "Register" not in text:
                        continue
                    scopes = set(_CS_NAMESPACE.findall(text)) | set(_CS_USING.findall(text)) | project_usings
                    for iface, impl in _di_pairs(text):
                        prefix = impl.split("<", 1)[0].rpartition(".")[0]
                        regs.append({"project": proj, "iface": _short_type(iface) if iface else None,
                                     "impl": _short_type(impl), "scopes": scopes | ({prefix} if prefix else set())})
            self._di_registrations = regs
        return self._di_registrations

    def _bindable(self, caller: CodeNode, target: CodeNode) -> bool:
        """Name-only tiers: same language, a callable target, and — in languages
        without implicit ``this`` — never a class method from a bare call."""
        if target.qualified_name == caller.qualified_name:
            return False
        if _family(target.language) != _family(caller.language) or target.node_type not in O.CALLABLE_TYPES:
            return False
        if _family(caller.language) in _NO_IMPLICIT_THIS:
            owner = self.qualified.get(target.qualified_name.rsplit(".", 1)[0])
            if owner is not None and owner.node_type in _CLASS_TYPES:
                return False
        return True

    def _local(self, caller, site, name, call_ref) -> bool:
        """Receiver-less call: unique callee in the same file, then the same package."""
        cands = [t for t in self.by_name.get(name, []) if self._bindable(caller, t)]
        same_file = [t for t in cands if t.file_path == caller.file_path]
        if len(same_file) == 1:
            self._edge(caller, same_file[0], "same_file", call_ref)
            return True
        if len(same_file) > 1:
            self._miss(caller, site, call_ref, "ambiguous", len(same_file))
            return True
        for prefix in _module_prefixes(caller.qualified_name):
            same_pkg = [t for t in cands if t.qualified_name == f"{prefix}.{name}"]
            if len(same_pkg) == 1:
                self._edge(caller, same_pkg[0], "same_package", call_ref)
                return True
            if len(same_pkg) > 1:
                self._miss(caller, site, call_ref, "ambiguous", len(same_pkg))
                return True
        return False

    def _global(self, caller, site, name, receiver, imports, call_ref) -> None:
        """Last tier: the bare name, unique repo-wide, guarded (ADR-0004 short-name guards)."""
        if O.is_builtin(_family(caller.language), name):
            return self._miss(caller, site, call_ref, "builtin")
        cands = [t for t in self.by_name.get(name, []) if t.qualified_name != caller.qualified_name
                 and _family(t.language) == _family(caller.language) and t.node_type in O.CALLABLE_TYPES]
        if receiver is None:
            cands = [t for t in cands if self._bindable(caller, t)]
        else:
            # `obj.Entity(...)` is a method call, never a constructor of a repo class
            cands = [t for t in cands if t.node_type not in _CLASS_TYPES]
        if len(cands) == 1:
            self._edge(caller, cands[0], "global_unique", call_ref, **({"receiver": receiver} if receiver else {}))
        elif cands:
            self._miss(caller, site, call_ref, "ambiguous", len(cands))
        else:
            head = (receiver or "").split(".")[0]
            self._miss(caller, site, call_ref, "external" if head and head in imports else "not_found")

    # ── graph-wide dispatch edges ────────────────────────────────────────
    def dispatch_pass(self) -> None:
        """Base/interface method → each override in a repo subclass (``dispatch``)."""
        for base_name, impls in self.impls.items():
            bases = self._resolve_type(base_name)
            if len(bases) != 1:
                continue
            base_methods = [n for n in self.members.get(bases[0].qualified_name, [])
                            if n.node_type in O.CALLABLE_TYPES and n.node_type not in _CLASS_TYPES]
            for m in base_methods:
                overrides = [o for impl in impls
                             if (o := self.qualified.get(f"{impl.qualified_name}.{m.name}")) is not None]
                if 0 < len(overrides) <= O.DISPATCH_CAP:
                    for o in overrides:
                        self.edges.append(make_edge(m.qualified_name, o.qualified_name, "dispatch", call=m.name))


_GLOBAL_USING = re.compile(r"^\s*global\s+using\s+(static\s+)?(?:(\w+)\s*=\s*)?([\w.]+)\s*;", re.M)
_CSPROJ_USING = re.compile(r"<Using\s+Include=\"([\w.]+)\"(?:\s+Static=\"(true|True)\")?(?:\s+Alias=\"(\w+)\")?")


def _project_usings(project: Path) -> list[dict]:
    """`global using` lines from the project's .cs files (outside bin/obj) and `<Using>` items of its .csproj."""
    out: list[dict] = []
    files = [f for f in project.rglob("*.cs") if not ({"bin", "obj"} & set(f.relative_to(project).parts))]
    for f in sorted(files):
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            continue
        for static, alias, target in _GLOBAL_USING.findall(head):
            out.append(_using_record(target, alias or None, bool(static)))
    for proj in sorted(project.glob("*.csproj")):
        try:
            text = proj.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for target, static, alias in _CSPROJ_USING.findall(text):
            out.append(_using_record(target, alias or None, bool(static)))
    return out


def _using_record(target: str, alias: str | None, static: bool) -> dict:
    module, _, name = target.rpartition(".")
    if alias:
        return {"module": module, "name": name or None, "alias": alias, "static": static, "global": True}
    if static:
        return {"module": module, "name": name, "alias": None, "static": True, "global": True}
    return {"module": target, "name": None, "alias": None, "static": False, "global": True}


def _build_import_index(caller: CodeNode, extra: list[dict] | None = None) -> dict[str, dict]:
    """Map each local binding name to its import record.

    `from M import Name`          → {"Name": import_record}
    `from M import Name as Alias` → {"Alias": import_record}
    `import M`                    → {"M" (or first segment): import_record}
    `import M.N as Alias`         → {"Alias": import_record}
    """
    index: dict[str, dict] = {}
    for imp in [*(caller.imports or []), *(extra or [])]:
        alias = imp.get("alias")
        name = imp.get("name")
        module = imp.get("module") or ""
        if alias:
            local = alias
        elif name:
            local = name
        elif caller.language in ("csharp", "vbnet"):
            # `using Ns;` binds no name; key it uniquely so `using A.X;` and `using A.Y;` both survive (M6.2)
            local = f"*{module}" if module else ""
        else:
            # Plain `import M.N` binds the first segment (`M`).
            local = module.split(".")[0] if module else ""
        if local:
            index[local] = imp
    return index


_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")
_MAX_REEXPORT_DEPTH = 8   # barrel chains deeper than this are left unresolved (M6.5.a)
_MAX_CHAIN_DEPTH = 3      # `a().b().c().d()`: deeper call chains are not typed (M6.16)


def _imported_qn(node: CodeNode, short: str) -> str:
    """Fully-qualified name a file's explicit import gives *short* (`import jakarta.validation.Validator;`
    → `jakarta.validation.Validator`); '' when the file has no single-name import of it."""
    for imp in node.imports or []:
        if imp.get("name") == short and not imp.get("alias") and imp.get("module") and not imp.get("static"):
            return f"{imp['module']}.{short}"
    return ""


def _different_import(want: str, have: str) -> bool:
    """M6.15: two files that import *different* types of the same short name (`jakarta.validation.Validator`
    vs Spring's `org.springframework.validation.Validator`) are not talking about one interface."""
    return bool(want and have and want != have)


_DI_REGISTRATION = re.compile(
    r"\b(?:Try)?Add(?:Scoped|Transient|Singleton|HttpClient)\s*<\s*([\w.]+(?:<[^<>]*>)?)\s*,\s*([\w.]+(?:<[^<>]*>)?)\s*>")


_TYPE_ARG = r"([\w.]+(?:<[^<>]*>)?)"
_DI_FACTORY = re.compile(r"\b(?:Try)?Add(?:Scoped|Transient|Singleton)\s*<\s*" + _TYPE_ARG +
                         r"\s*>\s*\(\s*\(?\s*\w*\s*\)?\s*=>\s*(?:\{[^{}]*?return\s+)?new\s+([\w.]+)")
_DI_SINGLE = re.compile(r"\b(?:Try)?Add(?:Scoped|Transient|Singleton|HttpClient)\s*<\s*" + _TYPE_ARG + r"\s*>\s*\(")
_UNITY = re.compile(r"\bRegister(?:Type|Singleton)\s*<\s*" + _TYPE_ARG + r"\s*,\s*" + _TYPE_ARG + r"\s*>")
_AUTOFAC = re.compile(r"\bRegisterType\s*<\s*" + _TYPE_ARG + r"\s*>\s*\(\s*\)\s*\.\s*As\s*<\s*" + _TYPE_ARG + r"\s*>")
_AUTOFAC_SELF = re.compile(r"\bRegisterType\s*<\s*" + _TYPE_ARG + r"\s*>\s*\(\s*\)\s*\.\s*AsImplementedInterfaces\b")
_CS_NAMESPACE = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
_CS_USING = re.compile(r"^\s*(?:global\s+)?using\s+(?!static\b)([\w.]+)\s*;", re.M)
_XML_BEAN = re.compile(r"<bean\b([^>]*)>", re.S)
_XML_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')


def _di_pairs(text: str) -> list[tuple[str | None, str]]:
    """(interface or None, implementation) for every container registration in a C# file."""
    pairs: list[tuple[str | None, str]] = [(i, t) for i, t in _DI_REGISTRATION.findall(text)]
    pairs += [(i, t) for i, t in _UNITY.findall(text)]
    pairs += [(i, t) for t, i in _AUTOFAC.findall(text)]
    pairs += [(None, t) for t in _AUTOFAC_SELF.findall(text)]
    factories = {m.start(): (m.group(1), m.group(2)) for m in _DI_FACTORY.finditer(text)}
    pairs += list(factories.values())
    pairs += [(None, m.group(1)) for m in _DI_SINGLE.finditer(text) if m.start() not in factories]
    return pairs


def _short_type(text: str) -> str:
    return text.split("<", 1)[0].rsplit(".", 1)[-1].strip()


def _strip_call(text: str) -> str:
    """`orders.find(id)` → `orders.find`; `make<T>(x)` → `make`; '' if *text* is not a call."""
    text = text.strip()
    if not text.endswith(")"):
        return ""
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(text[i], 0)
        if depth == 0:
            head = text[:i].rstrip()
            if head.endswith(">") and "<" in head:     # generic method arguments
                head = head[: head.rindex("<")]
            return head
    return ""


def _outer_type(type_text: str | None) -> str:
    """Declared type → outer unqualified name: `java.util.List<Order>` → `List`, `Order?` → `Order`;
    void, primitives, arrays and type parameters give ''."""
    t = (type_text or "").strip().split("<", 1)[0].rstrip("?").strip()
    t = t.rsplit(".", 1)[-1]
    if not t or t.endswith("]") or t[:1].islower() or t in ("void", "var", "dynamic") or len(t) == 1:
        return ""   # len 1: a type parameter (`T get()`), unknown here
    return t


def _module_matches_file(module: str, file_path: str, importer: str = "") -> bool:
    """Heuristic: module `svc.orders` matches `.../svc/orders.py` or `.../svc/orders/__init__.py`.

    A relative JS/TS specifier (`./author.mapper`, `../services/x`) is resolved
    against the importing file's directory and matched with the usual
    extensions or an `index` file.

    Tolerates shorter tails (e.g. `orders` matches `.../orders.py`) so the
    check still helps when callers use short relative imports or when the
    repo doesn't map packages strictly to directory layout. Extension set
    covers the deep parsers and the A-6 import-map tier (Go/Rust/Ruby/PHP),
    whose extractors normalize module separators to dots.
    """
    if not module:
        return False
    if module.startswith(".") and "/" in module and importer:
        base = posixpath.normpath(posixpath.join(posixpath.dirname(importer.replace("\\", "/")), module))
        posix = posixpath.normpath(file_path.replace("\\", "/"))
        return any(posix == base + ext or posix == f"{base}/index{ext}" for ext in ("", *_JS_EXTS))
    parts = module.split(".")
    posix = file_path.replace("\\", "/")
    for n in range(len(parts), 0, -1):
        tail = "/".join(parts[-n:])
        for ext in (".py", ".go", ".rs", ".rb", ".php"):
            if posix.endswith(f"/{tail}{ext}"):
                return True
        if posix.endswith(f"/{tail}/__init__.py"):
            return True
    return False


def _module_prefixes(qualified_name: str) -> list[str]:
    parts = qualified_name.split(".")
    if len(parts) <= 1:
        return []
    # Try narrower prefixes first, but exclude the direct class owner handled separately.
    prefixes: list[str] = []
    for idx in range(max(1, len(parts) - 2), 0, -1):
        prefixes.append(".".join(parts[:idx]))
    return prefixes


_ENTRY_TYPES = frozenset({"endpoint", "batch_job", "cli_command", "event_consumer"})
_CLI_HINTS = frozenset({"main", "cli", "command", "cmd"})
_EVENT_HINTS = frozenset({"consumer", "listener", "subscriber", "handler"})


def identify_scenarios(nodes: list[CodeNode]) -> list[CodeNode]:
    """Identify entry points for execution scenarios.

    Includes HTTP endpoints, batch jobs, CLI entry points, and event consumers.
    """
    scenarios: list[CodeNode] = []
    for node in nodes:
        if node.node_type in _ENTRY_TYPES:
            scenarios.append(node)
        elif node.node_type in ("function", "method"):
            name_lower = node.name.lower()
            if any(h in name_lower for h in _CLI_HINTS):
                scenarios.append(node)
            elif any(h in name_lower for h in _EVENT_HINTS):
                scenarios.append(node)
    return scenarios


class ExecutionSliceBuilder:
    """Builds bounded execution slices starting from entry points."""

    def __init__(self, nodes: list[CodeNode], call_edges: list[CallEdge],
                 unresolved: list[UnresolvedCall] | None = None):
        self.nodes = {n.qualified_name: n for n in nodes}
        # ADR-0004 following rule: only static/framework/scip edges at or above
        # the floor are walked; name-only guesses (global_unique) never are.
        self.edges_by_caller = defaultdict(list)
        self.skipped_by_caller: dict[str, int] = defaultdict(int)
        for edge in call_edges:
            if O.followable(edge):
                self.edges_by_caller[edge.caller].append(edge)
            else:
                self.skipped_by_caller[edge.caller] += 1
        self.unresolved_by_caller: dict[str, set[str]] = defaultdict(set)
        for u in unresolved or []:
            self.unresolved_by_caller[u.caller].add(u.reason)

    def build_all_scenarios(self) -> list[Scenario]:
        entry_points = identify_scenarios(list(self.nodes.values()))
        scenarios = []
        for entry in entry_points:
            scenarios.append(self.build_scenario(entry))
        return scenarios

    def build_scenario(self, entry: CodeNode, max_depth: int = 5) -> Scenario:
        """Perform bounded BFS to extract a scenario-specific slice.

        Returns Scenario with:
        - nodes: all discovered nodes in BFS order
        - primary_path: top-confidence nodes capped at 15, in execution order
        - alternate_paths: conditional branches with conditions
        """
        scenario_id = f"scenario_{entry.name}_{entry.line_start}"
        scenario = Scenario(
            scenario_id=scenario_id,
            name=f"Flow: {entry.name}",
            entry_point=entry.qualified_name,
            trigger_type=self._get_trigger_type(entry),
            domain=entry.domain,
        )

        visited: set[str] = {entry.qualified_name}
        # BFS queue: (node_name, depth, prev_exec_node)
        queue: deque[tuple[str, int, ExecutionNode | None]] = deque([(entry.qualified_name, 0, None)])
        # Track state writes for read-after-write scoring: field -> value
        state_writes: dict[str, str] = {}

        while queue:
            node_name, depth, prev_exec = queue.popleft()

            node = self.nodes.get(node_name)
            exec_node = self._create_execution_node(node_name, node)

            # Multi-signal scoring
            exec_node.confidence = self._score_node(exec_node, depth, prev_exec, state_writes)

            # Track state writes for downstream read-after-write detection
            if exec_node.state_transition:
                t = exec_node.state_transition
                if t.field and t.to_state:
                    state_writes[t.field] = t.to_state

            scenario.nodes.append(exec_node)

            if not node:
                scenario.stop_reasons[node_name] = self._leaf_stop(node_name, exec_node)
                continue

            # Collect callees for branching detection
            callees = self.edges_by_caller.get(node_name, [])
            if depth >= max_depth:
                if callees:
                    scenario.stop_reasons.setdefault(node_name, "depth_cap")
                else:
                    scenario.stop_reasons.setdefault(node_name, self._leaf_stop(node_name, exec_node))
                continue
            new = [e for e in callees if e.callee not in visited]
            if callees and not new:
                scenario.stop_reasons.setdefault(node_name, "cycle")
            elif not callees:
                scenario.stop_reasons.setdefault(node_name, self._leaf_stop(node_name, exec_node))
            for edge in callees:
                # Determine edge type: CALL (default), ASYNC, or CONDITIONAL
                edge_type = self._infer_edge_type(node, edge, callees)
                scenario.edges.append(ExecutionEdge(
                    from_node=node_name,
                    to_node=edge.callee,
                    edge_type=edge_type,
                    confidence=edge.confidence,
                ))
                if edge.callee not in visited:
                    visited.add(edge.callee)
                    queue.append((edge.callee, depth + 1, exec_node))

            # Detect conditional branches (multiple callees = gateway)
            if len(callees) > 1:
                self._detect_alternate_paths(exec_node, callees, scenario)

        scenario.primary_path = self._build_primary_path(entry.qualified_name, scenario)

        scenario.external_interfaces = list(set(
            n.name for n in scenario.nodes if n.type in ("DB", "EXTERNAL_API", "QUEUE")
        ))

        return scenario

    def _leaf_stop(self, node_name: str, exec_node: ExecutionNode) -> str:
        """Why a node with no followable callees ends the slice."""
        if exec_node.type == "DB":
            return "db_boundary"
        if exec_node.type in ("EXTERNAL_API", "QUEUE"):
            return "external_boundary"
        reasons = self.unresolved_by_caller.get(node_name, set())
        if reasons & {"dynamic", "dispatch_cap", "di_ambiguous"}:
            return "dynamic_dispatch"
        if reasons - {"builtin"} or self.skipped_by_caller.get(node_name):
            return "unresolved_call"      # calls exist but none can be followed
        return "leaf_no_callees"

    @staticmethod
    def _score_node(
        exec_node: ExecutionNode,
        depth: int,
        prev_node: ExecutionNode | None,
        state_writes: dict[str, str],
    ) -> float:
        """Compute a multi-signal confidence score for a node in a scenario.

        Signals (from ENHANCEMENT_PLAN Pillar B):
          +5 call order  (entry/shallow nodes score higher)
          +4 state transition present
          +3 read-after-write detected (node reads a field written upstream)
          +3 data boundary (DB or QUEUE)
          +2 external API
        """
        score = 0.0
        # Call order: entry (depth=0) gets +5, each level costs 1 point
        score += max(0.0, 5.0 - depth)
        # State transition signal
        if exec_node.state_transition:
            score += 4.0
        # Data boundary signals
        if exec_node.type in ("DB", "QUEUE"):
            score += 3.0
        elif exec_node.type == "EXTERNAL_API":
            score += 2.0
        # Read-after-write: if this node's source mentions a field written upstream
        if state_writes and exec_node.file_path:
            for field, value in state_writes.items():
                # Simple heuristic: if the node name or type suggests a read on this state
                if field.lower() in exec_node.name.lower():
                    score += 3.0
                    break
        return score

    def _create_execution_node(self, node_name: str, node: CodeNode | None) -> ExecutionNode:
        """Convert a CodeNode into an ExecutionNode, enriching with hints."""
        if not node and node_name.startswith("external::"):
            # synthesized external-system node (external_client edge)
            return ExecutionNode(id=node_name, type="EXTERNAL_API", name=node_name.split("::", 1)[1],
                                 qualified_name=node_name, confidence=O.confidence("external_client"))
        if not node:
            # Unresolved/External
            return ExecutionNode(
                id=node_name,
                type="UNRESOLVED",
                name=node_name.split(".")[-1],
                qualified_name=node_name,
                confidence=0.5
            )

        node_type = "FUNCTION"
        if node.node_type in ("endpoint", "batch_job", "cli_command", "event_consumer"):
            node_type = "ENTRY"

        # Enrich from hints — pick the highest-priority boundary type
        hints = node.framework_hints
        boundaries = hints.get("boundaries", [])
        if boundaries:
            _BOUNDARY_PRIORITY: dict[str, int] = {"DB": 3, "QUEUE": 2, "EXTERNAL_API": 1}
            best = max(boundaries, key=lambda b: _BOUNDARY_PRIORITY.get(b["type"], 0))
            node_type = best["type"]

        # State transition handling
        transition = None
        transitions = hints.get("transitions", [])
        if transitions:
            t = transitions[0]
            # Phase 2d: `entity_id` is the unique trace key; parsers populate
            # it at emit time. Fall back to `entity` defensively so stale
            # fixtures still produce a usable (if non-unique) id.
            transition = StateTransition(
                entity=t["entity"],
                entity_id=t.get("entity_id") or t["entity"],
                field=t["field"],
                to_state=t["value"],
                trigger_function=node.qualified_name,
                guard_expr=t.get("guard"),
            )

        return ExecutionNode(
            id=node.qualified_name,
            type=node_type,
            name=node.name,
            qualified_name=node.qualified_name,
            file_path=node.file_path,
            line_number=node.line_start,
            summary=None, # will be filled by Tier 1/2
            domain=node.domain,
            state_transition=transition
        )

    def _build_primary_path(
        self,
        entry_id: str,
        scenario: Scenario,
        max_length: int = 15,
    ) -> list[str]:
        """Walk the dominant sync execution chain from the entry (Phase 1.4).

        DFS along the highest-weight successor at each step:
          - Skip ASYNC edges (fire-and-forget side effects live off-path).
          - Rank remaining successors by (semantic signal, edge confidence,
            earlier source line). Semantic signal rewards targets that carry
            state transitions or data boundaries — those are the events
            downstream BPMN/EARS generation actually cares about.

        Stops on: no sync successor, cycle revisit, or max_length reached.
        The result is guaranteed contiguous (every adjacent pair is a real
        edge), unlike the previous score-filtered BFS order.
        """
        edges_from: dict[str, list[ExecutionEdge]] = defaultdict(list)
        for e in scenario.edges:
            if e.edge_type != "ASYNC":
                edges_from[e.from_node].append(e)

        nodes_by_id = {n.id: n for n in scenario.nodes}

        path: list[str] = []
        visited: set[str] = set()
        current = entry_id

        while current and current not in visited and len(path) < max_length:
            visited.add(current)
            path.append(current)

            outs = edges_from.get(current, [])
            if not outs:
                break

            best = max(
                outs,
                key=lambda e: self._main_successor_rank(e, nodes_by_id.get(e.to_node)),
            )
            current = best.to_node

        return path

    @staticmethod
    def _main_successor_rank(
        edge: ExecutionEdge,
        target: ExecutionNode | None,
    ) -> tuple[float, float, int]:
        """Higher wins. Pulls the walk toward state transitions and boundaries."""
        signal = 0.0
        line = 10**6
        if target is not None:
            if target.state_transition is not None:
                signal += 2.0
            if target.type in ("DB", "QUEUE"):
                signal += 1.0
            elif target.type == "EXTERNAL_API":
                signal += 0.5
            if target.line_number:
                line = target.line_number
        # Negate line so earlier source lines rank higher.
        return (signal, edge.confidence, -line)

    def _detect_alternate_paths(
        self,
        gateway_node: ExecutionNode,
        callees: list[CallEdge],
        scenario: Scenario,
    ) -> None:
        """Detect and record alternate execution paths from conditional branches.

        When a node has multiple callees, treat it as a gateway and populate
        alternate_paths with inferred conditions based on callee names/types.
        """
        if len(callees) <= 1:
            return

        # Heuristic: infer condition names from callee names
        conditions = []
        for edge in callees:
            callee_name = edge.callee.split(".")[-1].lower()
            # Detect common condition patterns
            if any(x in callee_name for x in ("success", "ok", "valid", "pass")):
                condition = "success"
            elif any(x in callee_name for x in ("error", "fail", "reject", "invalid")):
                condition = "failure"
            elif any(x in callee_name for x in ("retry", "fallback")):
                condition = "retry"
            else:
                condition = f"branch_{len(conditions) + 1}"
            conditions.append((condition, edge.callee))

        # Record alternate paths
        for condition, path_start in conditions:
            # Build BFS path from this branch start
            branch_visited: set[str] = set()
            branch_queue: list[str] = [path_start]
            branch_path: list[str] = []

            while branch_queue and len(branch_path) < 10:  # Limit depth
                node_name = branch_queue.pop(0)
                if node_name in branch_visited:
                    continue
                branch_visited.add(node_name)
                branch_path.append(node_name)

                for edge in self.edges_by_caller.get(node_name, []):
                    branch_queue.append(edge.callee)

            if branch_path:
                scenario.alternate_paths.append({
                    "condition": condition,
                    "path": branch_path,
                })

    def _infer_edge_type(
        self,
        caller: CodeNode | None,
        edge: CallEdge,
        all_callees: list[CallEdge],
    ) -> str:
        """Determine edge type: CALL (sync), ASYNC (fire-and-forget), or CONDITIONAL (branch).

        Heuristics:
        - ASYNC: callee name contains async patterns, or caller has async_boundary hint
        - CONDITIONAL: multiple callees from same caller (gateway); inferred by branch detection
        - CALL: default synchronous call
        """
        if not caller:
            return "CALL"

        callee_name = edge.callee.split(".")[-1].lower()

        # Check for async patterns in callee name
        async_patterns = ("async", "schedule", "submit", "dispatch", "enqueue", "publish", "fire", "task")
        if any(pattern in callee_name for pattern in async_patterns):
            return "ASYNC"

        # Check for async patterns in caller's framework hints
        hints = caller.framework_hints or {}
        if hints.get("async_boundary"):
            return "ASYNC"

        # If multiple callees (gateway), edges become conditional (first one is explicit, rest are branches)
        if len(all_callees) > 1:
            return "CONDITIONAL"

        return "CALL"

    def _get_trigger_type(self, node: CodeNode) -> str:
        if node.node_type == "endpoint":
            return "HTTP"
        if node.node_type == "batch_job":
            return "SCHEDULED"
        if node.node_type == "event_consumer":
            return "EVENT"
        if node.node_type == "cli_command":
            return "CLI"
        name_lower = node.name.lower()
        if any(h in name_lower for h in _EVENT_HINTS):
            return "EVENT"
        return "CLI"
