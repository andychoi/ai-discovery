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
    load_cross_entity_links_json,
    load_entity_conditions_json,
    load_entity_state_machines_json,
    write_cross_entity_links_json,
    write_entity_conditions_json,
    write_entity_state_machines_json,
)
from ai_discovery.graph.fsm_rollup import build_entity_state_machines
from ai_discovery.graph.models import (
    CrossEntityTransitionLink,
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


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


# --- Round-trip loaders (Phase 3 impact query) ------------------------------


def test_fsm_json_round_trip(tmp_path: Path):
    """Write → load → write yields byte-identical JSON."""
    fsm = EntityStateMachine(
        entity="Order", entity_id="mod.Order",
        states={"draft", "submitted"}, fields={"status"},
        source_files={"src/order.py"}, confidence=0.875,
        metadata={"entity_kind": "transactional"},
        transitions=[StateTransition(
            entity="Order", entity_id="mod.Order", field="status",
            from_state="draft", to_state="submitted",
            trigger_function="mod.Order.submit",
            guard_expr="self.total > 0", confidence=0.9,
            entry_points=[{"kind": "API", "qualified_name": "routes.create",
                           "confidence": 0.9, "hop_count": 1}],
            metadata={"cross_entity_guards": [
                {"entity_hint": "order", "field": "status",
                 "operator": "==", "value": "x", "resolved_entity_id": None}
            ]},
        )],
    )
    path = tmp_path / "fsms.json"
    write_entity_state_machines_json([fsm], path)

    loaded = load_entity_state_machines_json(path)
    assert len(loaded) == 1
    assert loaded[0].entity == "Order"
    assert loaded[0].entity_id == "mod.Order"
    assert loaded[0].states == {"draft", "submitted"}
    assert loaded[0].metadata["entity_kind"] == "transactional"

    t = loaded[0].transitions[0]
    assert t.guard_expr == "self.total > 0"
    assert t.entry_points[0]["kind"] == "API"
    assert t.metadata["cross_entity_guards"][0]["field"] == "status"

    # Re-export the loaded fsm and compare byte-wise to the original.
    path2 = tmp_path / "fsms2.json"
    write_entity_state_machines_json(loaded, path2)
    assert path.read_text() == path2.read_text()


def test_cross_entity_links_round_trip(tmp_path: Path):
    link = CrossEntityTransitionLink(
        from_entity="Order", from_entity_id="mod.Order",
        from_field="status", from_state="submitted",
        to_entity="Invoice", to_entity_id="mod.Invoice",
        to_field="status", to_state="pending",
        support=5, directional_confidence=0.875,
    )
    path = tmp_path / "links.json"
    write_cross_entity_links_json([link], path)
    loaded = load_cross_entity_links_json(path)
    assert len(loaded) == 1
    assert loaded[0].from_entity == "Order" and loaded[0].to_entity == "Invoice"
    assert loaded[0].support == 5


def test_entity_conditions_round_trip(tmp_path: Path):
    cond = EntityConditionCorrelation(
        target_entity="Invoice", target_entity_id="mod.Invoice",
        target_field="status", target_to_state="pending",
        context_entity="Order", context_entity_id="mod.Order",
        context_field="status", context_state="submitted",
        support=7, consistency=0.875,
    )
    path = tmp_path / "conds.json"
    write_entity_conditions_json([cond], path)
    loaded = load_entity_conditions_json(path)
    assert len(loaded) == 1
    assert loaded[0].target_entity == "Invoice"
    assert loaded[0].context_state == "submitted"
    assert loaded[0].support == 7
