"""Phase 3.1c: parse `StateTransition.guard_expr` for cross-entity state references.

A guard like `order.status == 'approved'` attached to an Invoice transition
means Invoice's lifecycle gates on Order's state. That's a cross-entity
condition that DMN generators should key rules on and BPMN generators should
render as a conditional edge. This module extracts those references from the
raw guard text (language-agnostic source strings emitted by `find_enclosing_guard`
in `parsers/base.py`) and annotates the transition's metadata.

Scoped deliberately to `lhs.field <op> value` comparisons — the common DMN-
ready shape. Truthy checks, method calls, and deep attribute chains are
intentionally out of scope here; they'd need AST-level analysis that the
parsers don't currently preserve through to the FSM layer.
"""

from __future__ import annotations

import re

from .models import EntityStateMachine

_INTRA_ENTITY_REFS = frozenset({"self", "this", "cls", "super"})

_COMPARISON_RE = re.compile(
    r"""
    (?<![.\w])                  # not preceded by . or word char
    (?P<lhs>[A-Za-z_]\w*)       # entity reference
    \.
    (?P<field>[A-Za-z_]\w*)     # field name
    \s*(?P<op>==|!=|>=|<=|>|<)\s*
    (?P<value>
        '[^']*'                 # single-quoted string
      | "[^"]*"                 # double-quoted string
      | \[[^\]]*\]              # bracketed list
      | -?\d+(?:\.\d+)?         # number
      | True|False|None|null    # literals
      | [A-Za-z_]\w*            # bare identifier
    )
    """,
    re.VERBOSE,
)


def _normalize_entity_hint(hint: str) -> str:
    """Lowercase + trivial depluralization for name matching."""
    h = (hint or "").lower()
    if len(h) > 3 and h.endswith("s") and not h.endswith("ss"):
        h = h[:-1]
    return h


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_cross_entity_guards(fsms: list[EntityStateMachine]) -> int:
    """Walk every transition's `guard_expr`, extract cross-entity state
    references, annotate `transition.metadata['cross_entity_guards']`.

    Returns the number of transitions annotated (each with ≥1 ref).
    """
    if not fsms:
        return 0

    # Name → entity_id lookup for hint resolution. Index on both the display
    # name and the unqualified tail of entity_id so `order` resolves to
    # `mod.Order` regardless of which form matches first.
    hint_to_id: dict[str, str] = {}
    for fsm in fsms:
        candidates = [fsm.entity]
        if fsm.entity_id:
            candidates.append(fsm.entity_id.rsplit(".", 1)[-1])
        for key in candidates:
            norm = _normalize_entity_hint(key)
            if norm:
                hint_to_id.setdefault(norm, fsm.entity_id or fsm.entity)

    annotated = 0
    for fsm in fsms:
        owner_norm = _normalize_entity_hint(fsm.entity)
        for t in fsm.transitions:
            if not t.guard_expr:
                continue
            new_refs: list[dict] = []
            for m in _COMPARISON_RE.finditer(t.guard_expr):
                lhs = m.group("lhs")
                if lhs in _INTRA_ENTITY_REFS:
                    continue
                norm = _normalize_entity_hint(lhs)
                if norm == owner_norm:
                    continue
                new_refs.append({
                    "raw": m.group(0),
                    "entity_hint": lhs,
                    "resolved_entity_id": hint_to_id.get(norm),
                    "field": m.group("field"),
                    "operator": m.group("op"),
                    "value": _strip_quotes(m.group("value")),
                })
            if not new_refs:
                continue
            existing = t.metadata.setdefault("cross_entity_guards", [])
            seen = {
                (r["entity_hint"], r["field"], r["operator"], r["value"])
                for r in existing
            }
            added_here = False
            for r in new_refs:
                key = (r["entity_hint"], r["field"], r["operator"], r["value"])
                if key in seen:
                    continue
                existing.append(r)
                seen.add(key)
                added_here = True
            if added_here:
                annotated += 1
    return annotated
