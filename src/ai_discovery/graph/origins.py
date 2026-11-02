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
    # M6.8: table access from a SQL statement in the caller. One origin per evidence (parsed vs regex); the
    # statement decides reads_table vs writes_table (``table_edge``), the origin only the confidence.
    Origin("sql_parsed", 0.90, edge_kind="reads_table"),
    Origin("sql_regex", 0.60, edge_kind="reads_table"),
    # M6.10: code / procedure / MyBatis CALLABLE → stored procedure, by an explicit call form (CommandType.
    # StoredProcedure, {call x}, EXEC x, withProcedureName)
    Origin("proc_call", 0.90, edge_kind="calls_proc"),
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
    if language == "python" and name.startswith("__") and name.endswith("__"):
        return True     # dunder protocol methods (`__init__`, `__enter__`, …)
    return name in BUILTINS.get(language, _COMMON_MEMBERS)


# ── Following rules (ADR-0004 §2, M1.12.b) ──────────────────────────────────
# Execution slices, flow BFS and entry-point linking follow only these
# evidence kinds, and only origins at or above the floor: `global_unique`
# (0.50) — a name-only guess — is never walked by default.
FLOW_EVIDENCE = frozenset({"static", "framework", "scip"})
FLOW_MIN_CONFIDENCE = 0.75
FLOW_EDGE_KINDS = frozenset({"calls", "dispatches_to", "framework_binds", "calls_external"})

# One stop reason per slice leaf (design/graph-accuracy.md §8).
STOP_REASONS = frozenset({"leaf_no_callees", "depth_cap", "cycle", "unresolved_call", "dynamic_dispatch",
                          "external_boundary", "db_boundary", "flow_cap"})

# Slice bounds (design §8, M6.17): calls deep, and at most FLOW_MAX_FLOWS flows per entry point. A flow is one
# entry-to-stop path of the slice's BFS tree; once that many have stopped, the remaining frontier is recorded
# with stop reason `flow_cap` and not expanded.
FLOW_MAX_DEPTH = 8
FLOW_MAX_FLOWS = 50


def followable(edge, *, min_confidence: float = FLOW_MIN_CONFIDENCE,
               evidence: frozenset[str] = FLOW_EVIDENCE) -> bool:
    """True when a flow/slice/BFS may walk *edge* (a ``CallEdge``)."""
    meta = edge.metadata or {}
    origin_name = meta.get("resolved_by")
    if origin_name not in ORIGINS:
        # an edge built without an origin (tests, adapters): judge by confidence alone
        return edge.confidence >= min_confidence
    o = ORIGINS[origin_name]
    return (o.evidence_kind in evidence and o.edge_kind in FLOW_EDGE_KINDS
            and o.confidence >= min_confidence)
