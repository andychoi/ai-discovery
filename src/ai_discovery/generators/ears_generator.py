"""Phase 3 deliverable: entry-points + transitions → EARS requirement skeletons.

Produces a Markdown document of EARS-style requirements — one per transition
with a known `to_state`. Layout follows the backbone-plan template
(`WHEN [entry-point], THEN [from→to] IF [guard]`), extended with a `WHILE`
clause when the transition has mined cross-entity context (Phase 3.1d) so
the canonical EARS keyword stack applies:

  * **WHILE** — state-context precondition (statistical, Phase 3.1d)
  * **WHEN**  — triggering event (entry-point from linker, or trigger_function)
  * **THEN**  — the response (state transition)
  * **IF**    — guard qualifier (syntactic, `guard_expr` + Phase 3.1c)

These are *skeletons* — confidence-annotated, reviewer-editable. Unlike the
DMN generator, EARS emits for every transition with a `to_state`, because
every state change is a behavioral requirement worth documenting, whether
or not it has a machine-readable rule.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.models import (
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


_ENTRY_PHRASING: dict[str, str] = {
    "API": "API endpoint",
    "UI": "UI action",
    "Batch": "batch job",
    "Event": "event",
    "CLI": "CLI command",
}


def generate_entity_ears_markdown(
    fsms: list[EntityStateMachine],
    conditions: list[EntityConditionCorrelation],
) -> str:
    """Render one EARS block per transition, grouped by (entity, field)."""
    conditions_by_target: dict[tuple[str, str, str | None], list[EntityConditionCorrelation]] = defaultdict(list)
    for c in conditions:
        conditions_by_target[(c.target_entity_id, c.target_field, c.target_to_state)].append(c)

    sections: list[str] = []
    for fsm in sorted(fsms, key=lambda f: f.entity_id or f.entity):
        by_field: dict[str, list[StateTransition]] = defaultdict(list)
        for t in fsm.transitions:
            if t.to_state is None:
                continue
            by_field[t.field or ""].append(t)

        for field in sorted(by_field):
            transitions = _sorted_transitions(by_field[field])
            blocks: list[str] = []
            for i, t in enumerate(transitions, start=1):
                ctx = conditions_by_target.get(
                    (t.entity_id or t.entity, t.field or "", t.to_state), [],
                )
                blocks.append(_render_block(fsm, t, ctx, i))
            if blocks:
                sections.append(f"## {fsm.entity}.{field}\n\n" + "\n\n".join(blocks))

    if not sections:
        return "# Entity requirements (EARS)\n\n_No transitions found._\n"
    return "# Entity requirements (EARS)\n\n" + "\n\n".join(sections) + "\n"


def _render_block(
    fsm: EntityStateMachine,
    t: StateTransition,
    ctx: list[EntityConditionCorrelation],
    index: int,
) -> str:
    lines: list[str] = [f"### REQ-{fsm.entity}-{t.field}-{index:03d}"]

    while_clause = _format_while(ctx)
    if while_clause:
        lines.append(f"**WHILE** {while_clause},  ")

    when_clause, when_confidence = _format_when(t)
    lines.append(f"**WHEN** {when_clause},  ")

    lines.append(f"**THEN** `{fsm.entity}.{t.field}` SHALL transition "
                 f"{_from_phrase(t.from_state)} to `{t.to_state}`")

    if_clause = _format_if(t)
    if if_clause:
        lines.append(f"**IF** {if_clause}")

    # Trailing provenance annotation — reviewers need to know what was derived
    # vs. asserted. Entry-point confidence is the weakest link in the chain
    # since it comes from BFS across resolved edges.
    annotations: list[str] = []
    if when_confidence is not None:
        annotations.append(f"entry-point confidence: {when_confidence:.2f}")
    if ctx:
        annotations.append(f"context support: {max(c.support for c in ctx)}")
    if annotations:
        lines.append("")
        lines.append(f"_{'; '.join(annotations)}_")

    return "\n".join(lines)


def _format_while(ctx: list[EntityConditionCorrelation]) -> str:
    if not ctx:
        return ""
    clauses = sorted({
        f"`{c.context_entity}.{c.context_field}` IS `{c.context_state}`"
        for c in ctx
    })
    return " AND ".join(clauses)


def _format_when(t: StateTransition) -> tuple[str, float | None]:
    """Pick the best entry-point (nearest hop, highest confidence)."""
    entries = t.entry_points or []
    if entries:
        best = entries[0]
        kind = best.get("kind", "")
        phrase = _ENTRY_PHRASING.get(kind, kind.lower() if kind else "trigger")
        qn = best.get("qualified_name", "")
        return f"{phrase} `{qn}` is invoked", best.get("confidence")
    # Fallback: use trigger_function name directly. No confidence signal here
    # because the link is trivial (trigger_function IS the trigger).
    if t.trigger_function:
        return f"`{t.trigger_function}` is invoked", None
    return "a trigger fires", None


def _format_if(t: StateTransition) -> str:
    parts: list[str] = []
    for ref in t.metadata.get("cross_entity_guards", []) or []:
        hint = ref.get("entity_hint") or ""
        parts.append(
            f"`{hint}.{ref.get('field')}` {ref.get('operator')} `{ref.get('value')}`"
        )
    if t.guard_expr and not parts:
        parts.append(f"`{t.guard_expr}`")
    if not parts:
        return ""
    return " AND ".join(sorted(set(parts)))


def _from_phrase(from_state: str | None) -> str:
    if from_state is None:
        return ""
    return f"from `{from_state}`"


def _sorted_transitions(transitions: list[StateTransition]) -> list[StateTransition]:
    def key(t: StateTransition) -> tuple:
        return (t.from_state or "", t.to_state or "", t.trigger_function or "")
    return sorted(transitions, key=key)
