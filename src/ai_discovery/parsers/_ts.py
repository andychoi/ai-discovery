"""Shared tree-sitter helpers for the AST parsers.

The four full-AST parsers (Python, Java, C#, JS/TS) carried verbatim copies of
the same query-runner, call-site, inside-class, and state-transition logic —
~40% of each was per-language rebinding of identical algorithms (docs/reviews
04 P1-3). These are their single home. Per-language variation is passed in
(query objects, self-receiver keywords) rather than re-implemented; the parsers
supply their own tree-sitter queries whose capture names follow a shared
convention (``site.name``/``site.receiver`` for call sites,
``assign.obj``/``assign.attr``/``assign.val`` for assignments).

This is a helpers module rather than a base class: the parsers compose these
functions, which gets the same de-duplication without restructuring four large
modules into an inheritance hierarchy.

Scope note — only the *byte-identical* helpers live here. `_detect_boundaries`
(varies on case folding, parent node type, and exact-vs-substring client match)
and `_extract_state_transitions` (varies on the self-receiver keyword set, the
absent-receiver default, a Python-only class guard, a Python-only ``or ""``
entity_id fallback, and JS's backtick string-strip) stay per-parser: their
per-language differences are real semantics, not just node names, so a shared
helper would need enough parameters to become its own maintenance hazard.
Consolidating them safely needs a dedicated equivalence-tested pass.
"""

from __future__ import annotations

from tree_sitter import Query, QueryCursor


def matches(query: Query, node) -> list[dict[str, list]]:
    """Execute a query and return a list of match dicts (paired captures)."""
    return [caps for _pat_idx, caps in QueryCursor(query).matches(node)]


def captures(query: Query, node) -> dict[str, list]:
    """Execute a query and return captures as {name: [node, ...]}."""
    return QueryCursor(query).captures(node)


def inside_class(node, class_ranges: set[tuple[int, int]]) -> bool:
    """Return True if *node*'s start row sits within any recorded class range."""
    row = node.start_point.row
    for start, end in class_ranges:
        if start <= row <= end:
            return True
    return False


def extract_call_sites(query: Query | None, node) -> list[dict]:
    """One record per call site, retaining per-site receiver text.

    Deduplicating call extraction loses which object a call was made on; the
    resolver needs the receiver to disambiguate same-named methods (e.g.
    ``order_service.save`` vs ``customer_service.save``). Emits the raw
    receiver source text; unqualified calls record ``receiver=None``.

    *query* may be ``None`` (some grammars fail to compile the call-site query
    on older tree-sitter versions) — in that case no sites are returned.
    """
    sites: list[dict] = []
    if query is None:
        return sites
    for match in matches(query, node):
        name_nodes = match.get("site.name", [])
        if not name_nodes:
            continue
        receiver_nodes = match.get("site.receiver", [])
        sites.append({
            "name": name_nodes[0].text.decode(),
            "receiver": receiver_nodes[0].text.decode() if receiver_nodes else None,
        })
    return sites
