"""Phase 3 deliverable: entity-impact query.

Given an entity identifier, produces a Markdown report summarising every
code path and cross-entity interaction that touches its transitions. This
is the *validation* tool for the backbone — if the report for a well-known
entity is obviously incomplete, the backbone's evidence gathering has gaps.

The query is a pure function over already-mined artifacts
(`EntityStateMachine`, `CrossEntityTransitionLink`, `EntityConditionCorrelation`);
the CLI command loads them from JSON and calls into here. Keeping the
query pure means it's straightforward to unit-test against hand-built
fixtures without running the full pipeline.

Entity matching is tolerant: exact `entity_id` match wins, then exact
`entity` name, then case-insensitive `entity` name. Downstream impact-
analysis tooling can call `find_entity` directly for programmatic use.
"""

from __future__ import annotations

from .models import (
    CrossEntityTransitionLink,
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


class EntityNotFound(ValueError):
    """Raised when the query target doesn't match any known FSM."""


def find_entity(
    identifier: str, fsms: list[EntityStateMachine],
) -> EntityStateMachine:
    """Locate the FSM whose entity_id or entity name matches `identifier`."""
    for f in fsms:
        if f.entity_id == identifier:
            return f
    for f in fsms:
        if f.entity == identifier:
            return f
    ident_lower = identifier.lower()
    for f in fsms:
        if f.entity.lower() == ident_lower:
            return f
    known = sorted({f.entity for f in fsms})
    raise EntityNotFound(
        f"No entity matches {identifier!r}. Known entities: {known}"
    )


def query_entity_impact(
    identifier: str,
    fsms: list[EntityStateMachine],
    cross_links: list[CrossEntityTransitionLink],
    conditions: list[EntityConditionCorrelation],
) -> str:
    """Render the impact-report Markdown for `identifier`."""
    target = find_entity(identifier, fsms)
    tid = target.entity_id or target.entity

    sections: list[str] = [_render_header(target)]
    sections.append(_render_transitions(target))

    inbound = [l for l in cross_links if l.to_entity_id == tid]
    outbound = [l for l in cross_links if l.from_entity_id == tid]
    if inbound:
        sections.append(_render_cross_links(
            "Inbound sequence links",
            "Transitions on *other entities* that precede a transition on this entity.",
            inbound, direction="inbound",
        ))
    if outbound:
        sections.append(_render_cross_links(
            "Outbound sequence links",
            "Transitions on this entity that precede a transition on *other entities*.",
            outbound, direction="outbound",
        ))

    cond_on = [c for c in conditions if c.target_entity_id == tid]
    cond_ctx = [c for c in conditions if c.context_entity_id == tid]
    if cond_on:
        sections.append(_render_conditions_on(target, cond_on))
    if cond_ctx:
        sections.append(_render_conditions_as_context(target, cond_ctx))

    return "\n\n".join(sections) + "\n"


# --- section renderers ------------------------------------------------------


def _render_header(f: EntityStateMachine) -> str:
    kind = f.metadata.get("entity_kind", "unknown")
    lines = [
        f"# Impact report: {f.entity}",
        "",
        f"**ID**: `{f.entity_id or f.entity}`  ",
        f"**Kind**: {kind}  ",
        f"**Fields**: {_or_dash(sorted(f.fields))}  ",
        f"**States**: {_or_dash(sorted(f.states))}  ",
        f"**Source files**: {_or_dash(sorted(f.source_files))}",
    ]
    merged = f.metadata.get("consolidated_from")
    if merged:
        lines.append(f"**Consolidated from**: {', '.join(merged)}")
    return "\n".join(lines)


def _render_transitions(f: EntityStateMachine) -> str:
    if not f.transitions:
        return "## Transitions\n\n_No transitions recorded._"
    lines = [
        "## Transitions",
        "",
        "| # | field | from | → to | trigger | guards | entry points |",
        "|---|-------|------|------|---------|--------|--------------|",
    ]
    for i, t in enumerate(_sorted_transitions(f.transitions), start=1):
        lines.append(
            f"| {i} | {_cell(t.field)} | {_cell(t.from_state)} | {_cell(t.to_state)} | "
            f"{_cell(_short(t.trigger_function))} | {_cell(_format_guard(t))} | "
            f"{_cell(_format_entry_points(t))} |"
        )
    return "\n".join(lines)


def _render_cross_links(
    title: str, blurb: str,
    links: list[CrossEntityTransitionLink], *, direction: str,
) -> str:
    lines = [f"## {title}", "", blurb, ""]
    for l in sorted(links, key=lambda x: (
        x.from_entity, x.from_field, x.from_state or "",
        x.to_entity, x.to_field, x.to_state or "",
    )):
        frm = f"`{l.from_entity}.{l.from_field}: → {l.from_state}`"
        to = f"`{l.to_entity}.{l.to_field}: → {l.to_state}`"
        lines.append(
            f"- {frm} → {to}  "
            f"_(support={l.support}, confidence={l.directional_confidence:.2f})_"
        )
    return "\n".join(lines)


def _render_conditions_on(
    f: EntityStateMachine, conds: list[EntityConditionCorrelation],
) -> str:
    lines = [
        f"## Conditions on {f.entity} transitions",
        "",
        "Other-entity states that precede a transition on this entity.",
        "",
    ]
    for c in sorted(conds, key=lambda x: (
        x.target_field, x.target_to_state or "",
        x.context_entity, x.context_field, x.context_state,
    )):
        lines.append(
            f"- **{f.entity}.{c.target_field} → {c.target_to_state}**: "
            f"WHILE `{c.context_entity}.{c.context_field}` = `{c.context_state}`  "
            f"_(support={c.support}, consistency={c.consistency:.2f})_"
        )
    return "\n".join(lines)


def _render_conditions_as_context(
    f: EntityStateMachine, conds: list[EntityConditionCorrelation],
) -> str:
    lines = [
        f"## {f.entity} as context for other entities",
        "",
        f"This entity's states predicting transitions on *other* entities.",
        "",
    ]
    for c in sorted(conds, key=lambda x: (
        x.target_entity, x.target_field, x.target_to_state or "",
        x.context_field, x.context_state,
    )):
        lines.append(
            f"- **{c.target_entity}.{c.target_field} → {c.target_to_state}**: "
            f"WHEN `{f.entity}.{c.context_field}` = `{c.context_state}`  "
            f"_(support={c.support}, consistency={c.consistency:.2f})_"
        )
    return "\n".join(lines)


# --- small helpers ----------------------------------------------------------


def _sorted_transitions(transitions: list[StateTransition]) -> list[StateTransition]:
    return sorted(transitions, key=lambda t: (
        t.field or "", t.from_state or "", t.to_state or "", t.trigger_function or "",
    ))


def _format_guard(t: StateTransition) -> str:
    parts: list[str] = []
    for ref in t.metadata.get("cross_entity_guards", []) or []:
        parts.append(
            f"{ref.get('entity_hint', '')}.{ref.get('field')} "
            f"{ref.get('operator')} {ref.get('value')}"
        )
    if t.guard_expr and not parts:
        parts.append(t.guard_expr)
    return " ∧ ".join(sorted(set(parts))) if parts else ""


def _format_entry_points(t: StateTransition) -> str:
    if not t.entry_points:
        return ""
    return ", ".join(
        f"{ep.get('kind', '?')}:{_short(ep.get('qualified_name', ''))}"
        for ep in t.entry_points
    )


def _short(name: str | None) -> str | None:
    if not name:
        return None
    return name.rsplit(".", 1)[-1]


def _cell(v: str | None) -> str:
    if v is None or v == "":
        return "-"
    return str(v).replace("|", "\\|").replace("\n", " ")


def _or_dash(items: list[str]) -> str:
    return ", ".join(items) if items else "-"
