"""Tests for Phase 3d entity condition correlator."""

from __future__ import annotations

from ai_discovery.graph.entity_correlator import mine_entity_conditions
from ai_discovery.graph.models import (
    EntityStateMachine,
    ExecutionNode,
    Scenario,
    StateTransition,
)


def _fsm(entity: str) -> EntityStateMachine:
    return EntityStateMachine(entity=entity, entity_id=f"mod.{entity}")


def _transition(entity: str, to_state: str, field: str = "status") -> StateTransition:
    return StateTransition(
        entity=entity,
        entity_id=f"mod.{entity}",
        field=field,
        from_state=None,
        to_state=to_state,
        trigger_function=f"mod.{entity}_transition",
    )


def _node(nid: str, transition: StateTransition | None = None) -> ExecutionNode:
    return ExecutionNode(
        id=nid,
        type="TRANSITION" if transition else "FUNCTION",
        name=nid,
        state_transition=transition,
    )


def _scenario(sid: str, transitions: list[StateTransition]) -> Scenario:
    nodes = [_node(f"{sid}-n{i}", t) for i, t in enumerate(transitions)]
    return Scenario(
        scenario_id=sid,
        name=sid,
        entry_point=sid,
        trigger_type="HTTP",
        nodes=nodes,
        primary_path=[n.id for n in nodes],
    )


# --- core mining ------------------------------------------------------------


def test_basic_condition_correlation():
    """3 scenarios where Order→submitted precedes Invoice→pending → correlation."""
    scs = [
        _scenario(f"s{i}", [
            _transition("Order", "submitted"),
            _transition("Invoice", "pending"),
        ]) for i in range(3)
    ]
    out = mine_entity_conditions(
        [_fsm("Order"), _fsm("Invoice")], scs, min_support=2,
    )
    assert len(out) == 1
    c = out[0]
    assert c.target_entity == "Invoice"
    assert c.target_to_state == "pending"
    assert c.context_entity == "Order"
    assert c.context_state == "submitted"
    assert c.support == 3
    assert c.consistency == 1.0


def test_min_support_filters_rare_patterns():
    scs = [_scenario("s1", [_transition("A", "x"), _transition("B", "y")])]
    out = mine_entity_conditions([_fsm("A"), _fsm("B")], scs, min_support=2)
    assert out == []


def test_consistency_threshold_filters_inconsistent_context():
    """If Invoice→pending fires twice with Order=submitted and twice with
    Order=approved, consistency for each is 0.5 → filtered at threshold 0.75."""
    scs = [
        _scenario("s1", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s2", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s3", [_transition("Order", "approved"), _transition("Invoice", "pending")]),
        _scenario("s4", [_transition("Order", "approved"), _transition("Invoice", "pending")]),
    ]
    out = mine_entity_conditions(
        [_fsm("Order"), _fsm("Invoice")], scs,
        min_support=2, consistency_threshold=0.75,
    )
    assert out == []


def test_consistency_threshold_accepts_dominant_context():
    """3 of 4 times Invoice→pending, Order was submitted → consistency 0.75."""
    scs = [
        _scenario("s1", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s2", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s3", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s4", [_transition("Order", "approved"), _transition("Invoice", "pending")]),
    ]
    out = mine_entity_conditions(
        [_fsm("Order"), _fsm("Invoice")], scs,
        min_support=2, consistency_threshold=0.75,
    )
    assert len(out) == 1
    assert out[0].context_state == "submitted"
    assert out[0].support == 3
    assert round(out[0].consistency, 2) == 0.75


# --- context tracking -------------------------------------------------------


def test_latest_context_state_wins_within_scenario():
    """If Order transitions twice (submitted then approved) before Invoice
    fires, the latest state (approved) is what gets correlated."""
    scs = [
        _scenario("s1", [
            _transition("Order", "submitted"),
            _transition("Order", "approved"),
            _transition("Invoice", "pending"),
        ]) for _ in range(2)
    ]
    out = mine_entity_conditions(
        [_fsm("Order"), _fsm("Invoice")], scs, min_support=2,
    )
    # Only the 'approved' context should appear — 'submitted' was overwritten.
    contexts = {(c.context_state, c.support) for c in out}
    assert ("approved", 2) in contexts
    assert not any(c.context_state == "submitted" for c in out)


def test_same_entity_not_its_own_context():
    """A transition's own entity state shouldn't be correlated with itself."""
    scs = [
        _scenario(f"s{i}", [
            _transition("Order", "draft"),
            _transition("Order", "submitted"),
        ]) for i in range(3)
    ]
    out = mine_entity_conditions([_fsm("Order")], scs, min_support=2)
    assert out == []


def test_no_context_before_first_transition():
    """The first transition in a scenario has no prior context — no correlation."""
    scs = [_scenario("s1", [_transition("Order", "submitted")])]
    out = mine_entity_conditions([_fsm("Order")], scs, min_support=1)
    assert out == []


def test_multiple_context_fields_per_entity():
    """An entity can have multiple fields; each tracked independently."""
    scs = []
    for _ in range(2):
        scs.append(_scenario("s", [
            StateTransition(
                entity="Order", entity_id="mod.Order", field="status",
                to_state="submitted", trigger_function="t",
            ),
            StateTransition(
                entity="Order", entity_id="mod.Order", field="payment_status",
                to_state="authorized", trigger_function="t",
            ),
            _transition("Invoice", "pending"),
        ]))
    out = mine_entity_conditions([_fsm("Order"), _fsm("Invoice")], scs, min_support=2)
    # Two correlations: one per Order field
    fields = {c.context_field for c in out}
    assert fields == {"status", "payment_status"}


# --- edge cases -------------------------------------------------------------


def test_empty_scenarios_returns_empty():
    assert mine_entity_conditions([_fsm("A")], []) == []


def test_transition_without_entity_id_ignored():
    bad = StateTransition(entity="X", field="status", to_state="done", entity_id="")
    good = _transition("B", "y")
    scs = [_scenario("s1", [_transition("A", "x"), bad, good]) for _ in range(2)]
    out = mine_entity_conditions([_fsm("A"), _fsm("B")], scs, min_support=2)
    # A→x should correlate with B→y; bad transition contributes no context.
    assert len(out) == 1
    assert out[0].target_entity == "B"
    assert out[0].context_entity == "A"


def test_output_is_deterministic():
    """Same input → byte-identical output across runs."""
    scs = [
        _scenario("s1", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s2", [_transition("Order", "submitted"), _transition("Invoice", "pending")]),
        _scenario("s3", [_transition("Customer", "active"), _transition("Invoice", "pending")]),
        _scenario("s4", [_transition("Customer", "active"), _transition("Invoice", "pending")]),
    ]
    a = mine_entity_conditions(
        [_fsm("Order"), _fsm("Invoice"), _fsm("Customer")], scs,
        min_support=2, consistency_threshold=0.5,
    )
    b = mine_entity_conditions(
        [_fsm("Customer"), _fsm("Invoice"), _fsm("Order")], scs,
        min_support=2, consistency_threshold=0.5,
    )
    # Order of scenarios and fsms shouldn't change output tuples
    assert [(c.target_entity_id, c.context_entity_id, c.context_state) for c in a] == \
           [(c.target_entity_id, c.context_entity_id, c.context_state) for c in b]


def test_repeated_target_in_scenario_counted_once_per_scenario():
    """If Invoice→pending appears twice in one scenario walk with the same
    context, support should still count the scenario once (not twice)."""
    # Build a scenario where Invoice fires twice after Order=submitted.
    scs = [_scenario("s1", [
        _transition("Order", "submitted"),
        _transition("Invoice", "pending"),
        _transition("Invoice", "pending"),  # same transition again
    ])]
    out = mine_entity_conditions(
        [_fsm("Order"), _fsm("Invoice")], scs, min_support=1,
    )
    # Should be exactly one observation with support=1, not 2.
    target_obs = [c for c in out if c.target_entity == "Invoice"]
    assert len(target_obs) == 1
    assert target_obs[0].support == 1
