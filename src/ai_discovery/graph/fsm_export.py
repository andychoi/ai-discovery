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

from .models import CrossEntityTransitionLink, EntityStateMachine, StateTransition


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
