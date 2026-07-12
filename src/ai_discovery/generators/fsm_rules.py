"""Shared FSM-rule helpers for the DMN and EARS generators.

Both generators consume the same inputs — an FSM's transitions plus the
Phase-3d ``EntityConditionCorrelation`` set — and index them identically before
each renders in its own notation (DMN decision tables vs. EARS requirement
blocks). The indexing and transition-ordering were byte-for-byte duplicated in
the two modules (docs/reviews 04 P1-6); this is their single home. The
*rendering* stays per-generator, since DMN and EARS format guards/context
differently on purpose.
"""

from __future__ import annotations

from collections import defaultdict

from ..graph.models import EntityConditionCorrelation, StateTransition

# Key: (target_entity_id, target_field, target_to_state)
ConditionKey = tuple[str, str, "str | None"]


def index_conditions_by_target(
    conditions: list[EntityConditionCorrelation],
) -> dict[ConditionKey, list[EntityConditionCorrelation]]:
    """Group mined conditions by the (entity_id, field, to_state) they gate."""
    by_target: dict[ConditionKey, list[EntityConditionCorrelation]] = defaultdict(list)
    for c in conditions:
        by_target[(c.target_entity_id, c.target_field, c.target_to_state)].append(c)
    return by_target


def sorted_transitions(transitions: list[StateTransition]) -> list[StateTransition]:
    """Deterministic transition order: (from_state, to_state, trigger_function)."""
    def key(t: StateTransition) -> tuple:
        return (t.from_state or "", t.to_state or "", t.trigger_function or "")
    return sorted(transitions, key=key)
