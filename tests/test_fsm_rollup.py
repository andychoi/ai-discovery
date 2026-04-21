"""Phase 2.1 tests: per-entity FSM rollup.

Uses hand-built StateTransitions so the rollup's contract is exercised
independently of the parser/call-graph pipeline that feeds it in practice.
"""

from __future__ import annotations

from ai_discovery.graph.fsm_rollup import build_entity_state_machines
from ai_discovery.graph.models import StateTransition


def _t(
    entity: str,
    field: str = "status",
    to: str | None = None,
    frm: str | None = None,
    trigger: str | None = None,
    confidence: float = 1.0,
    entry_points: list[dict] | None = None,
    guard: str | None = None,
) -> StateTransition:
    return StateTransition(
        entity=entity,
        field=field,
        from_state=frm,
        to_state=to,
        trigger_function=trigger,
        confidence=confidence,
        entry_points=entry_points or [],
        guard_expr=guard,
    )


def test_groups_transitions_by_entity():
    transitions = [
        _t("Order", to="CREATED", trigger="svc.order.create"),
        _t("Order", to="APPROVED", trigger="svc.order.approve"),
        _t("Customer", to="ACTIVE", trigger="svc.customer.activate"),
    ]
    fsms = build_entity_state_machines(transitions)

    by_entity = {f.entity: f for f in fsms}
    assert set(by_entity) == {"Order", "Customer"}
    assert len(by_entity["Order"].transitions) == 2
    assert len(by_entity["Customer"].transitions) == 1


def test_states_aggregate_from_and_to():
    transitions = [
        _t("Order", frm="DRAFT", to="CREATED", trigger="a"),
        _t("Order", frm="CREATED", to="APPROVED", trigger="b"),
        _t("Order", frm="APPROVED", to="FULFILLED", trigger="c"),
    ]
    (fsm,) = build_entity_state_machines(transitions)
    assert fsm.states == {"DRAFT", "CREATED", "APPROVED", "FULFILLED"}


def test_fields_aggregate_per_entity():
    transitions = [
        _t("Order", field="status", to="ACTIVE", trigger="a"),
        _t("Order", field="fulfillment_state", to="SHIPPED", trigger="b"),
    ]
    (fsm,) = build_entity_state_machines(transitions)
    assert fsm.fields == {"status", "fulfillment_state"}


def test_dedupes_identical_observations_keeping_richer_evidence():
    """Two scenario walks produce the same (field, from, to, trigger). Keep one."""
    weak = _t("Order", to="APPROVED", trigger="svc.approve", confidence=0.8)
    strong = _t(
        "Order", to="APPROVED", trigger="svc.approve",
        confidence=0.95, entry_points=[{"kind": "API", "qualified_name": "C.approve", "confidence": 0.9, "hop_count": 1}],
    )
    (fsm,) = build_entity_state_machines([weak, strong])
    assert len(fsm.transitions) == 1
    # Strong evidence wins (higher confidence).
    assert fsm.transitions[0].confidence == 0.95
    assert fsm.transitions[0].entry_points


def test_same_state_change_from_different_triggers_is_not_deduped():
    """Different triggers reaching the same state = different BPMN lanes."""
    from_api = _t("Order", to="APPROVED", trigger="api.approve")
    from_batch = _t("Order", to="APPROVED", trigger="batch.approve")
    (fsm,) = build_entity_state_machines([from_api, from_batch])
    triggers = {t.trigger_function for t in fsm.transitions}
    assert triggers == {"api.approve", "batch.approve"}


def test_source_files_from_node_index():
    transitions = [
        _t("Order", to="CREATED", trigger="svc.a.create"),
        _t("Order", to="APPROVED", trigger="svc.b.approve"),
    ]
    node_index = {
        "svc.a.create": "svc/a.py",
        "svc.b.approve": "svc/b.py",
    }
    (fsm,) = build_entity_state_machines(transitions, node_file_index=node_index)
    assert fsm.source_files == {"svc/a.py", "svc/b.py"}


def test_confidence_averages_observations():
    transitions = [
        _t("Order", to="CREATED", trigger="a", confidence=1.0),
        _t("Order", to="APPROVED", trigger="b", confidence=0.5),
    ]
    (fsm,) = build_entity_state_machines(transitions)
    assert fsm.confidence == 0.75


def test_entry_points_carry_through():
    """Downstream DMN/EARS generators need the entry_points intact."""
    eps = [
        {"kind": "API", "qualified_name": "OrdersController.approve", "confidence": 0.9, "hop_count": 2},
    ]
    (fsm,) = build_entity_state_machines([
        _t("Order", to="APPROVED", trigger="svc.approve", entry_points=eps),
    ])
    assert fsm.transitions[0].entry_points == eps


def test_guard_expr_carries_through():
    (fsm,) = build_entity_state_machines([
        _t("Order", to="REVIEW", trigger="svc.flag", guard="order.total > 1000"),
    ])
    assert fsm.transitions[0].guard_expr == "order.total > 1000"


def test_empty_input_returns_empty_list():
    assert build_entity_state_machines([]) == []


def test_transitions_missing_entity_are_skipped():
    """Entity-less transitions (e.g. top-level function assignments) don't make FSMs."""
    transitions = [
        _t("", to="X", trigger="a"),
        _t("Order", to="APPROVED", trigger="b"),
    ]
    fsms = build_entity_state_machines(transitions)
    assert [f.entity for f in fsms] == ["Order"]


def test_output_is_sorted_by_entity_name_for_stable_diffs():
    """Spec Phase 2 exit criterion 3: FSM artifact must be diffable across runs."""
    transitions = [
        _t("Zeta", to="X", trigger="a"),
        _t("Alpha", to="Y", trigger="b"),
        _t("Mu", to="Z", trigger="c"),
    ]
    fsms = build_entity_state_machines(transitions)
    assert [f.entity for f in fsms] == ["Alpha", "Mu", "Zeta"]
