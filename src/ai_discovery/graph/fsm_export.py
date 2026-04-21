"""Phase 2.3: JSON export for EntityStateMachine.

Serializes the canonical backbone artifact so it survives a pipeline run, can
be diff-reviewed in PRs, and fed to downstream tooling (BPMN/DMN/EARS
generators, external dashboards). The format is frozen on three contracts:

  1. **Stable**: sets → sorted lists, top-level FSMs sorted by entity, dict
     keys sorted — byte-for-byte identical JSON for identical inputs.
  2. **Lossless**: every `StateTransition` field round-trips (including
     `entry_points`, `guard_expr`, `metadata`).
  3. **Human-reviewable**: indented, ordered fields, no trailing whitespace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    CrossEntityTransitionLink,
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


def fsm_to_dict(fsm: EntityStateMachine) -> dict[str, Any]:
    """Serialize one FSM to a plain JSON-ready dict."""
    out: dict[str, Any] = {
        "entity": fsm.entity,
        "entity_id": fsm.entity_id or fsm.entity,
        "states": sorted(fsm.states),
        "fields": sorted(fsm.fields),
        "source_files": sorted(fsm.source_files),
        "confidence": round(fsm.confidence, 4),
        "transitions": [_transition_to_dict(t) for t in _sorted_transitions(fsm.transitions)],
    }
    # Phase 2.4: include provenance only when set, so pre-consolidation FSMs
    # continue to serialize byte-identically.
    if fsm.metadata:
        out["metadata"] = fsm.metadata
    return out


def _transition_to_dict(t: StateTransition) -> dict[str, Any]:
    return {
        "entity": t.entity,
        "entity_id": t.entity_id or t.entity,
        "field": t.field,
        "from_state": t.from_state,
        "to_state": t.to_state,
        "trigger_function": t.trigger_function,
        "guard_expr": t.guard_expr,
        "confidence": round(t.confidence, 4),
        "entry_points": t.entry_points,
        "metadata": t.metadata,
    }


def _sorted_transitions(transitions: list[StateTransition]) -> list[StateTransition]:
    """Stable order for diffs: (field, from, to, trigger_function), None last."""
    def key(t: StateTransition) -> tuple:
        return (
            t.field or "",
            t.from_state or "",
            t.to_state or "",
            t.trigger_function or "",
        )
    return sorted(transitions, key=key)


def entity_state_machines_to_json(fsms: list[EntityStateMachine]) -> str:
    payload = {
        "version": 1,
        "entity_state_machines": [fsm_to_dict(f) for f in fsms],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_entity_state_machines_json(
    fsms: list[EntityStateMachine],
    output_path: Path,
) -> Path:
    """Write `entity_state_machines.json` to `output_path`. Returns the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(entity_state_machines_to_json(fsms))
    return output_path


# ---------------------------------------------------------------------------
# Phase 3.1b: cross-entity transition links export.
# ---------------------------------------------------------------------------

def cross_entity_link_to_dict(link: CrossEntityTransitionLink) -> dict[str, Any]:
    out: dict[str, Any] = {
        "from_entity": link.from_entity,
        "from_entity_id": link.from_entity_id,
        "from_field": link.from_field,
        "from_state": link.from_state,
        "to_entity": link.to_entity,
        "to_entity_id": link.to_entity_id,
        "to_field": link.to_field,
        "to_state": link.to_state,
        "support": link.support,
        "directional_confidence": round(link.directional_confidence, 4),
    }
    if link.metadata:
        out["metadata"] = link.metadata
    return out


def cross_entity_links_to_json(links: list[CrossEntityTransitionLink]) -> str:
    payload = {
        "version": 1,
        "cross_entity_transitions": [cross_entity_link_to_dict(l) for l in links],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_cross_entity_links_json(
    links: list[CrossEntityTransitionLink],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(cross_entity_links_to_json(links))
    return output_path


# ---------------------------------------------------------------------------
# Phase 3.1d: entity condition correlations export.
# ---------------------------------------------------------------------------

def entity_condition_to_dict(c: EntityConditionCorrelation) -> dict[str, Any]:
    out: dict[str, Any] = {
        "target_entity": c.target_entity,
        "target_entity_id": c.target_entity_id,
        "target_field": c.target_field,
        "target_to_state": c.target_to_state,
        "context_entity": c.context_entity,
        "context_entity_id": c.context_entity_id,
        "context_field": c.context_field,
        "context_state": c.context_state,
        "support": c.support,
        "consistency": round(c.consistency, 4),
    }
    if c.metadata:
        out["metadata"] = c.metadata
    return out


def entity_conditions_to_json(correlations: list[EntityConditionCorrelation]) -> str:
    payload = {
        "version": 1,
        "entity_conditions": [entity_condition_to_dict(c) for c in correlations],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_entity_conditions_json(
    correlations: list[EntityConditionCorrelation],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(entity_conditions_to_json(correlations))
    return output_path


# ---------------------------------------------------------------------------
# Loaders — round-trip symmetry for the three canonical artifacts. Impact-
# query and any future tooling that consumes the written JSON uses these
# instead of re-deriving the schema.
# ---------------------------------------------------------------------------

def load_entity_state_machines_json(path: Path) -> list[EntityStateMachine]:
    payload = json.loads(path.read_text())
    out: list[EntityStateMachine] = []
    for d in payload.get("entity_state_machines", []):
        fsm = EntityStateMachine(
            entity=d["entity"],
            entity_id=d.get("entity_id", ""),
            states=set(d.get("states", [])),
            fields=set(d.get("fields", [])),
            source_files=set(d.get("source_files", [])),
            confidence=d.get("confidence", 1.0),
            metadata=d.get("metadata", {}),
        )
        for td in d.get("transitions", []):
            fsm.transitions.append(StateTransition(
                entity=td["entity"],
                entity_id=td.get("entity_id", ""),
                field=td.get("field", ""),
                from_state=td.get("from_state"),
                to_state=td.get("to_state"),
                trigger_function=td.get("trigger_function"),
                guard_expr=td.get("guard_expr"),
                confidence=td.get("confidence", 1.0),
                entry_points=td.get("entry_points", []),
                metadata=td.get("metadata", {}),
            ))
        out.append(fsm)
    return out


def load_cross_entity_links_json(path: Path) -> list[CrossEntityTransitionLink]:
    payload = json.loads(path.read_text())
    return [
        CrossEntityTransitionLink(
            from_entity=d["from_entity"],
            from_entity_id=d.get("from_entity_id", ""),
            from_field=d["from_field"],
            from_state=d.get("from_state"),
            to_entity=d["to_entity"],
            to_entity_id=d.get("to_entity_id", ""),
            to_field=d["to_field"],
            to_state=d.get("to_state"),
            support=d["support"],
            directional_confidence=d["directional_confidence"],
            metadata=d.get("metadata", {}),
        )
        for d in payload.get("cross_entity_transitions", [])
    ]


def load_entity_conditions_json(path: Path) -> list[EntityConditionCorrelation]:
    payload = json.loads(path.read_text())
    return [
        EntityConditionCorrelation(
            target_entity=d["target_entity"],
            target_entity_id=d.get("target_entity_id", ""),
            target_field=d["target_field"],
            target_to_state=d.get("target_to_state"),
            context_entity=d["context_entity"],
            context_entity_id=d.get("context_entity_id", ""),
            context_field=d["context_field"],
            context_state=d["context_state"],
            support=d["support"],
            consistency=d["consistency"],
            metadata=d.get("metadata", {}),
        )
        for d in payload.get("entity_conditions", [])
    ]
