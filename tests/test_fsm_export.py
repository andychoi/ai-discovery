"""Phase 2.3 tests: JSON export for EntityStateMachine.

The contract under test is: JSON output is stable across runs (spec exit
criterion 3), every field round-trips (lossless), and sets serialize as
sorted lists (so identical inputs produce byte-identical outputs).
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_discovery.graph.fsm_export import (
    entity_state_machines_to_json,
    fsm_to_dict,
    write_entity_state_machines_json,
)
from ai_discovery.graph.fsm_rollup import build_entity_state_machines
from ai_discovery.graph.models import EntityStateMachine, StateTransition


def _t(**kw) -> StateTransition:
    defaults = dict(
        entity="Order", field="status", from_state=None, to_state="ACTIVE",
        trigger_function="svc.activate", confidence=1.0,
    )
    defaults.update(kw)
    return StateTransition(**defaults)


def test_states_serialize_as_sorted_list():
    fsm = EntityStateMachine(
        entity="Order",
        states={"ZEBRA", "APPLE", "MANGO"},
    )
    out = fsm_to_dict(fsm)
    assert out["states"] == ["APPLE", "MANGO", "ZEBRA"]


def test_source_files_and_fields_also_sorted():
    fsm = EntityStateMachine(
        entity="Order",
        fields={"status", "fulfillment_state"},
        source_files={"b.py", "a.py"},
    )
    out = fsm_to_dict(fsm)
    assert out["fields"] == ["fulfillment_state", "status"]
    assert out["source_files"] == ["a.py", "b.py"]


def test_transitions_sorted_by_stable_key():
    fsm = EntityStateMachine(
        entity="Order",
        transitions=[
            _t(to_state="SHIPPED", trigger_function="z"),
            _t(to_state="CREATED", trigger_function="a"),
            _t(to_state="CREATED", trigger_function="b"),
        ],
    )
    out = fsm_to_dict(fsm)
    order = [(t["to_state"], t["trigger_function"]) for t in out["transitions"]]
    assert order == [("CREATED", "a"), ("CREATED", "b"), ("SHIPPED", "z")]


def test_all_transition_fields_round_trip():
    entry_points = [
        {"kind": "API", "qualified_name": "OrdersController.approve",
         "confidence": 0.9, "hop_count": 2},
    ]
    metadata = {"resolved_by": "import_scope"}
    fsm = EntityStateMachine(
        entity="Order",
        transitions=[_t(
            from_state="PENDING",
            to_state="APPROVED",
            guard_expr="order.total > 1000",
            entry_points=entry_points,
            confidence=0.95,
        )],
    )
    fsm.transitions[0].metadata = metadata

    out = fsm_to_dict(fsm)
    t = out["transitions"][0]
    assert t["entity"] == "Order"
    assert t["field"] == "status"
    assert t["from_state"] == "PENDING"
    assert t["to_state"] == "APPROVED"
    assert t["guard_expr"] == "order.total > 1000"
    assert t["entry_points"] == entry_points
    assert t["metadata"] == metadata
    assert t["confidence"] == 0.95


def test_json_output_is_byte_stable_across_runs():
    """Spec Phase 2 exit criterion 3: artifact must be diffable."""
    # Build identical FSMs twice, with intentionally different set-insertion order.
    transitions = [
        _t(to_state="CREATED", trigger_function="a"),
        _t(to_state="APPROVED", trigger_function="b"),
    ]
    fsms_a = build_entity_state_machines(transitions)
    fsms_b = build_entity_state_machines(list(reversed(transitions)))
    assert entity_state_machines_to_json(fsms_a) == entity_state_machines_to_json(fsms_b)


def test_top_level_fsms_sorted_by_entity():
    fsms = [
        EntityStateMachine(entity="Zeta"),
        EntityStateMachine(entity="Alpha"),
        EntityStateMachine(entity="Mu"),
    ]
    # Rollup already sorts; export preserves that order.
    out = json.loads(entity_state_machines_to_json(
        sorted(fsms, key=lambda f: f.entity)
    ))
    assert [f["entity"] for f in out["entity_state_machines"]] == ["Alpha", "Mu", "Zeta"]


def test_version_field_present():
    out = json.loads(entity_state_machines_to_json([]))
    assert out["version"] == 1
    assert out["entity_state_machines"] == []


def test_write_creates_file_and_parent_dir(tmp_path: Path):
    fsm = EntityStateMachine(entity="Order", states={"A"}, fields={"status"})
    target = tmp_path / "nested" / "dir" / "out.json"
    returned = write_entity_state_machines_json([fsm], target)

    assert returned == target
    assert target.exists()
    content = json.loads(target.read_text())
    assert content["entity_state_machines"][0]["entity"] == "Order"


def test_confidence_rounded_to_four_places():
    """Avoid diff noise from float representation drift."""
    fsm = EntityStateMachine(entity="Order", confidence=0.8333333333)
    out = fsm_to_dict(fsm)
    assert out["confidence"] == 0.8333
