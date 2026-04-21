"""Tests for Phase 3.1b cross-entity transition correlator."""

from __future__ import annotations

from ai_discovery.graph.entity_correlator import mine_cross_entity_transitions
from ai_discovery.graph.fsm_export import cross_entity_links_to_json
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


def _scenario(scenario_id: str, transitions_in_order: list[StateTransition]) -> Scenario:
    """Build a scenario whose primary_path walks each supplied transition."""
    nodes = [_node(f"{scenario_id}-n{i}", t) for i, t in enumerate(transitions_in_order)]
    return Scenario(
        scenario_id=scenario_id,
        name=scenario_id,
        entry_point=scenario_id,
        trigger_type="HTTP",
        nodes=nodes,
        edges=[],
        primary_path=[n.id for n in nodes],
    )


# --- basic mining -----------------------------------------------------------

def test_single_pair_single_scenario():
    """One scenario with Order→submitted then Invoice→pending yields one link."""
    order_sub = _transition("Order", "submitted")
    inv_pending = _transition("Invoice", "pending")
    scenario = _scenario("sc1", [order_sub, inv_pending])
    links = mine_cross_entity_transitions(
        [_fsm("Order"), _fsm("Invoice")],
        [scenario],
        min_support=1,
    )
    assert len(links) == 1
    link = links[0]
    assert link.from_entity == "Order"
    assert link.from_state == "submitted"
    assert link.to_entity == "Invoice"
    assert link.to_state == "pending"
    assert link.support == 1
    assert link.directional_confidence == 1.0


def test_min_support_filters_single_observations():
    """min_support=2 drops pairs seen only once."""
    sc = _scenario("sc1", [_transition("A", "x"), _transition("B", "y")])
    links = mine_cross_entity_transitions([_fsm("A"), _fsm("B")], [sc], min_support=2)
    assert links == []


def test_directional_threshold_suppresses_ambiguous_pairs():
    """A pair that appears 50/50 in both directions is suppressed."""
    sc1 = _scenario("sc1", [_transition("A", "x"), _transition("B", "y")])
    sc2 = _scenario("sc2", [_transition("B", "y"), _transition("A", "x")])
    links = mine_cross_entity_transitions(
        [_fsm("A"), _fsm("B")],
        [sc1, sc2],
        min_support=1,
        directional_threshold=0.75,
    )
    assert links == []


def test_directional_confidence_reflects_ratio():
    """3x A→B + 1x B→A → confidence 0.75."""
    scs = [
        _scenario("s1", [_transition("A", "x"), _transition("B", "y")]),
        _scenario("s2", [_transition("A", "x"), _transition("B", "y")]),
        _scenario("s3", [_transition("A", "x"), _transition("B", "y")]),
        _scenario("s4", [_transition("B", "y"), _transition("A", "x")]),
    ]
    links = mine_cross_entity_transitions(
        [_fsm("A"), _fsm("B")], scs, min_support=1, directional_threshold=0.5,
    )
    assert len(links) == 1
    assert links[0].from_entity == "A"
    assert links[0].support == 3
    assert round(links[0].directional_confidence, 2) == 0.75


# --- deduplication ----------------------------------------------------------

def test_same_entity_transitions_not_paired():
    """Two transitions on the same entity within a scenario are intra-entity,
    not cross-entity — skipped."""
    scs = [
        _scenario("s1", [
            _transition("Order", "submitted"),
            _transition("Order", "approved"),
        ]),
    ]
    links = mine_cross_entity_transitions([_fsm("Order")], scs, min_support=1)
    assert links == []


def test_repeated_transition_within_scenario_counted_once():
    """If primary_path visits the same transition twice, it should contribute
    once per scenario, not inflate support."""
    order_sub = _transition("Order", "submitted")
    inv_pending = _transition("Invoice", "pending")
    # primary_path with a repeat of Order.submitted — support should still be 1.
    scenario = _scenario("sc1", [order_sub, order_sub, inv_pending])
    links = mine_cross_entity_transitions(
        [_fsm("Order"), _fsm("Invoice")],
        [scenario],
        min_support=1,
    )
    assert len(links) == 1
    assert links[0].support == 1


# --- multiple pairs / determinism ------------------------------------------

def test_multiple_pairs_sorted_deterministically():
    """Output is sorted by (from_entity_id, from_field, from_state, …)."""
    scs = [
        _scenario("s1", [_transition("B", "y"), _transition("C", "z")]),
        _scenario("s2", [_transition("A", "x"), _transition("B", "y")]),
    ]
    links = mine_cross_entity_transitions(
        [_fsm("A"), _fsm("B"), _fsm("C")], scs, min_support=1,
    )
    assert len(links) == 2
    # First link sorts by from_entity_id ascending: mod.A < mod.B
    assert links[0].from_entity == "A" and links[0].to_entity == "B"
    assert links[1].from_entity == "B" and links[1].to_entity == "C"


def test_empty_scenarios_returns_empty():
    assert mine_cross_entity_transitions([_fsm("A")], []) == []


def test_scenarios_without_transitions_returns_empty():
    scenario = Scenario(
        scenario_id="s", name="s", entry_point="e", trigger_type="HTTP",
        nodes=[_node("n0")], primary_path=["n0"],
    )
    assert mine_cross_entity_transitions([_fsm("A")], [scenario]) == []


def test_transition_without_entity_id_is_skipped():
    """Defensive: a transition missing `entity_id` shouldn't crash mining."""
    bad = StateTransition(entity="X", field="status", to_state="done", entity_id="")
    good_a = _transition("A", "x")
    good_b = _transition("B", "y")
    scenario = _scenario("s", [good_a, bad, good_b])
    links = mine_cross_entity_transitions(
        [_fsm("A"), _fsm("B")],
        [scenario],
        min_support=1,
    )
    assert len(links) == 1
    assert links[0].from_entity == "A"
    assert links[0].to_entity == "B"


# --- JSON export ------------------------------------------------------------

def test_json_export_round_trips():
    scs = [_scenario("s1", [_transition("A", "x"), _transition("B", "y")])]
    links = mine_cross_entity_transitions([_fsm("A"), _fsm("B")], scs, min_support=1)
    text = cross_entity_links_to_json(links)
    assert "cross_entity_transitions" in text
    assert '"from_entity": "A"' in text
    assert '"to_entity": "B"' in text
    # Byte-stable: second call produces identical output.
    assert cross_entity_links_to_json(links) == text


def test_json_export_empty_list():
    text = cross_entity_links_to_json([])
    assert "cross_entity_transitions" in text
    assert "[]" in text
