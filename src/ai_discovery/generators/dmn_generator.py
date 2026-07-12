"""Phase 3 deliverable: FSM + guards/conditions → DMN decision tables.

Produces one decision table per `(entity, field)` pair so each table's output
variable is a single FSM slot (matching DMN 1.3's `decisionTable` shape — one
output per table). Each row is a transition; inputs are:

  * **from**        — the required starting state
  * **guard**       — syntactic predicates parsed from source (`guard_expr`
                      plus Phase 3c cross-entity references)
  * **context**     — statistical conditions mined from scenario walks
                      (Phase 3d `EntityConditionCorrelation`)

Transitions with *neither* a guard nor a mined condition are omitted: they
represent state changes the code performs unconditionally, which aren't DMN
rule candidates. Output is Markdown for PR review; DMN 1.3 XML export can
layer on later without repartitioning.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.models import (
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)
from .fsm_rules import index_conditions_by_target, sorted_transitions as _sorted_transitions


def generate_entity_decisions_markdown(
    fsms: list[EntityStateMachine],
    conditions: list[EntityConditionCorrelation],
) -> str:
    """Render one Markdown section per (entity, field) with a decision table."""
    conditions_by_target = index_conditions_by_target(conditions)

    sections: list[str] = []
    for fsm in sorted(fsms, key=lambda f: f.entity_id or f.entity):
        by_field: dict[str, list[StateTransition]] = defaultdict(list)
        for t in fsm.transitions:
            by_field[t.field or ""].append(t)

        for field in sorted(by_field):
            transitions = by_field[field]
            rows: list[tuple[StateTransition, str, str]] = []
            for t in _sorted_transitions(transitions):
                guard_cell = _format_guard(t)
                ctx_cell = _format_context(
                    conditions_by_target.get((t.entity_id or t.entity, t.field or "", t.to_state), []),
                )
                if guard_cell == "-" and ctx_cell == "-":
                    continue
                rows.append((t, guard_cell, ctx_cell))

            if not rows:
                continue
            sections.append(_render_table(fsm, field, rows))

    if not sections:
        return "# Entity decision tables\n\n_No guarded or conditioned transitions found._\n"
    return "# Entity decision tables\n\n" + "\n\n".join(sections) + "\n"


def _render_table(fsm: EntityStateMachine, field: str, rows: list[tuple[StateTransition, str, str]]) -> str:
    header = f"## {fsm.entity}.{field}\n\nHit policy: FIRST\n\n"
    header += "| # | from | guard | context | → to | trigger |\n"
    header += "|---|------|-------|---------|------|---------|\n"
    body_lines = []
    for i, (t, guard_cell, ctx_cell) in enumerate(rows, start=1):
        body_lines.append(
            f"| {i} | {_cell(t.from_state)} | {_cell(guard_cell)} | {_cell(ctx_cell)} | "
            f"{_cell(t.to_state)} | {_cell(_short_trigger(t.trigger_function))} |"
        )
    return header + "\n".join(body_lines)


def _format_guard(t: StateTransition) -> str:
    parts: list[str] = []
    for ref in t.metadata.get("cross_entity_guards", []) or []:
        hint = ref.get("entity_hint") or ""
        parts.append(f"{hint}.{ref.get('field')} {ref.get('operator')} {_quote_value(ref.get('value'))}")
    if t.guard_expr and not parts:
        parts.append(t.guard_expr)
    if not parts:
        return "-"
    return " ∧ ".join(sorted(set(parts)))


def _format_context(conds: list[EntityConditionCorrelation]) -> str:
    if not conds:
        return "-"
    clauses = sorted({
        f"{c.context_entity}.{c.context_field} = {c.context_state}"
        for c in conds
    })
    return " ∧ ".join(clauses)


def _short_trigger(name: str | None) -> str | None:
    if not name:
        return None
    return name.rsplit(".", 1)[-1]


def _cell(v: str | None) -> str:
    if v is None or v == "":
        return "-"
    return _escape_cell(v)


def _escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _quote_value(v: str | None) -> str:
    if v is None:
        return ""
    return v
