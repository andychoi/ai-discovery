"""Closed edge-origin vocabulary (ADR-0004, roadmap item M1.12.a).

Every call edge carries exactly one ``origin`` from ``ORIGINS``; the origin
alone fixes the edge's confidence (one value per origin, tuned per origin from
audit data, never per edge). The origin also fixes the edge kind and the
evidence kind. An ambiguous or unresolvable call gets **no edge** — it is
recorded as an unresolved call with one of ``UNRESOLVED_REASONS``.

The origin is stored in ``call_edges.resolved_by`` (the column keeps its name;
see ADR-0004 §Decision 1), the edge kind in ``call_edges.edge_kind`` and the
evidence kind in ``call_edges.evidence_kind`` (migration 0003).
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass


@dataclass(frozen=True)
class Origin:
    name: str
    confidence: float
    edge_kind: str = "calls"
    evidence_kind: str = "static"


_ORIGIN_LIST = [
    Origin("scip", 1.00, evidence_kind="scip"),
    Origin("same_file", 0.95),
    Origin("self_scope", 0.95),
    Origin("import_exact", 0.95),
    Origin("receiver_typed", 0.93),
    Origin("same_package", 0.90),
    Origin("inherited", 0.90),
    Origin("import_module", 0.88),
    Origin("framework", 0.90, edge_kind="framework_binds", evidence_kind="framework"),
    Origin("dispatch", 0.75, edge_kind="dispatches_to"),
    Origin("receiver_global", 0.75),
    Origin("global_unique", 0.50),
    Origin("sql_regex", 0.60, edge_kind="reads_table"),
    # Outbound call through a known client library (axios/kafka/redis/stripe…)
    # to a synthesized external-system node (extractors/external_system_extractor.py).
    Origin("external_client", 0.90, edge_kind="calls_external", evidence_kind="framework"),
]
ORIGINS: dict[str, Origin] = {o.name: o for o in _ORIGIN_LIST}

EVIDENCE_KINDS = frozenset({"static", "framework", "dynamic_hint", "scip", "co_change", "coverage", "runtime"})
EDGE_KINDS = frozenset({"calls", "dispatches_to", "framework_binds", "type_use", "markup_event", "calls_proc",
                        "reads_table", "writes_table", "dynamic_hint", "executes", "co_change", "calls_external"})
UNRESOLVED_REASONS = frozenset({"ambiguous", "not_found", "dynamic", "builtin", "external", "dispatch_cap",
                                "di_ambiguous"})

# Above this many overriding implementations a dispatch call is refused
# (reason ``dispatch_cap``) instead of emitting one edge per override.
DISPATCH_CAP = 24


def origin(name: str) -> Origin:
    """The ``Origin`` for *name*; a name outside the closed enum is a bug."""
    try:
        return ORIGINS[name]
    except KeyError:
        raise ValueError(f"unknown edge origin {name!r} (ADR-0004 closed vocabulary)") from None


def confidence(name: str) -> float:
    return origin(name).confidence


# ── Short-name guards ────────────────────────────────────────────────────────
# Call names that, without a known receiver type, almost always hit the
# language runtime or its standard library rather than repository code. A
# repo method that happens to share the name never binds through the
# name-only tiers (``global_unique``); the call is recorded as ``builtin``.

_COMMON_MEMBERS = frozenset({
    "get", "set", "put", "add", "remove", "clear", "contains", "size", "length", "count", "keys", "values",
    "items", "append", "extend", "insert", "pop", "push", "shift", "map", "filter", "reduce", "forEach",
    "find", "join", "split", "replace", "trim", "strip", "format", "toString", "equals", "hashCode",
    "compareTo", "close", "open", "read", "write", "flush", "send", "then", "catch", "log", "error",
    "warn", "info", "debug", "sort", "reverse", "copy", "update", "next", "iterator", "stream", "collect",
    "isEmpty", "startsWith", "endsWith", "substring", "indexOf", "toUpperCase", "toLowerCase", "lower",
    "upper", "encode", "decode", "parse", "json", "status", "use", "listen", "emit", "on", "of",
})

BUILTINS: dict[str, frozenset[str]] = {
    "python": frozenset(n for n in dir(builtins) if not n.startswith("_")) | _COMMON_MEMBERS | frozenset({
        "startswith", "endswith", "setdefault", "fetchone", "fetchall", "execute", "commit",
    }),
    "java": _COMMON_MEMBERS | frozenset({
        "println", "print", "printf", "valueOf", "getClass", "notify", "notifyAll", "wait", "orElse",
        "orElseThrow", "ifPresent", "isPresent", "asList", "toList", "builder", "build", "getMessage",
        "printStackTrace", "charAt", "matches", "containsKey", "getOrDefault", "putAll", "addAll",
        "parseInt", "parseLong", "requireNonNull",
    }),
    "csharp": _COMMON_MEMBERS | frozenset({
        "ToString", "Equals", "GetHashCode", "GetType", "WriteLine", "Write", "Add", "Remove", "Contains",
        "Count", "Any", "All", "Where", "Select", "First", "FirstOrDefault", "Single", "SingleOrDefault",
        "ToList", "ToArray", "ToListAsync", "FirstOrDefaultAsync", "SaveChanges", "SaveChangesAsync",
        "Include", "OrderBy", "OrderByDescending", "Format", "Parse", "TryParse", "Dispose", "Ok",
        "NotFound", "BadRequest", "View", "Redirect", "RedirectToAction", "CreatedAtAction", "Json",
    }),
    "javascript": _COMMON_MEMBERS | frozenset({
        "require", "parseInt", "parseFloat", "isNaN", "setTimeout", "setInterval", "clearTimeout",
        "stringify", "assign", "entries", "fetch", "resolve", "reject", "all", "includes", "slice",
        "splice", "concat", "some", "every", "toFixed", "router", "Router", "send", "sendStatus",
        "render", "redirect", "next",
    }),
}
BUILTINS["typescript"] = BUILTINS["javascript"]

# Calls whose target is chosen at run time by a string or reflection; recorded
# as ``dynamic`` instead of being bound to a same-named repo symbol.
DYNAMIC_CALLS = frozenset({
    "getattr", "setattr", "eval", "exec", "__import__", "import_module", "forName", "newInstance",
    "invoke", "CreateInstance", "GetMethod", "InvokeMember",
})

# Node types that can be the target of a call edge.
CALLABLE_TYPES = frozenset({"method", "function", "endpoint", "batch_job", "cli_command", "event_consumer",
                            "constructor", "class", "db_model"})  # class: constructor call


def is_builtin(language: str, name: str) -> bool:
    return name in BUILTINS.get(language, _COMMON_MEMBERS)
