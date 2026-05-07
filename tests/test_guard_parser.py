"""Tests for Phase 3c cross-entity guard parser."""

from __future__ import annotations

from ai_discovery.graph.guard_parser import parse_cross_entity_guards
from ai_discovery.graph.models import EntityStateMachine, StateTransition


def _fsm(entity: str, transitions: list[StateTransition] | None = None) -> EntityStateMachine:
    return EntityStateMachine(
        entity=entity,
        entity_id=f"mod.{entity}",
        transitions=transitions or [],
    )


def _trans(entity: str, guard: str | None) -> StateTransition:
    return StateTransition(
        entity=entity,
        entity_id=f"mod.{entity}",
        field="status",
        from_state="a",
        to_state="b",
        guard_expr=guard,
    )


# --- core parsing -----------------------------------------------------------


def test_no_guards_returns_zero():
    fsm = _fsm("Order", [_trans("Order", None)])
    assert parse_cross_entity_guards([fsm]) == 0


def test_cross_entity_ref_parsed_and_resolved():
    invoice = _fsm("Invoice", [_trans("Invoice", "order.status == 'approved'")])
    order = _fsm("Order")
    assert parse_cross_entity_guards([invoice, order]) == 1
    refs = invoice.transitions[0].metadata["cross_entity_guards"]
    assert len(refs) == 1
    assert refs[0]["entity_hint"] == "order"
    assert refs[0]["field"] == "status"
    assert refs[0]["operator"] == "=="
    assert refs[0]["value"] == "approved"
    assert refs[0]["resolved_entity_id"] == "mod.Order"


def test_intra_entity_self_reference_skipped():
    """`self.x == y` is the transition's own entity — not cross-entity."""
    order = _fsm("Order", [_trans("Order", "self.total > 100")])
    assert parse_cross_entity_guards([order]) == 0
    assert "cross_entity_guards" not in order.transitions[0].metadata


def test_same_named_entity_ref_skipped():
    """`order.x` on an Order transition is also intra-entity."""
    order = _fsm("Order", [_trans("Order", "order.total > 100")])
    assert parse_cross_entity_guards([order]) == 0


def test_this_cls_super_skipped():
    order = _fsm("Order", [
        _trans("Order", "this.total > 100"),
        _trans("Order", "cls.default == 1"),
        _trans("Order", "super.foo == 'bar'"),
    ])
    assert parse_cross_entity_guards([order]) == 0


def test_multiple_refs_in_one_guard():
    invoice = _fsm("Invoice", [
        _trans("Invoice", "order.status == 'approved' and customer.tier == 'gold'"),
    ])
    order = _fsm("Order")
    customer = _fsm("Customer")
    assert parse_cross_entity_guards([invoice, order, customer]) == 1
    refs = invoice.transitions[0].metadata["cross_entity_guards"]
    assert {r["entity_hint"] for r in refs} == {"order", "customer"}


def test_plural_hint_resolves_to_singular_fsm():
    """Guards often use plural instance names: `orders.status` → Order FSM."""
    invoice = _fsm("Invoice", [_trans("Invoice", "orders.status == 'paid'")])
    order = _fsm("Order")
    assert parse_cross_entity_guards([invoice, order]) == 1
    refs = invoice.transitions[0].metadata["cross_entity_guards"]
    assert refs[0]["resolved_entity_id"] == "mod.Order"


def test_unresolvable_hint_kept_with_none():
    """Hint that matches no known FSM is still annotated — traceability over silence."""
    invoice = _fsm("Invoice", [_trans("Invoice", "mystery.foo == 'bar'")])
    assert parse_cross_entity_guards([invoice]) == 1
    refs = invoice.transitions[0].metadata["cross_entity_guards"]
    assert refs[0]["entity_hint"] == "mystery"
    assert refs[0]["resolved_entity_id"] is None


# --- value shapes -----------------------------------------------------------


def test_single_and_double_quoted_strings_stripped():
    invoice = _fsm("Invoice", [
        _trans("Invoice", "order.status == 'paid'"),
        _trans("Invoice", 'order.status == "shipped"'),
    ])
    order = _fsm("Order")
    parse_cross_entity_guards([invoice, order])
    assert invoice.transitions[0].metadata["cross_entity_guards"][0]["value"] == "paid"
    assert invoice.transitions[1].metadata["cross_entity_guards"][0]["value"] == "shipped"


def test_numeric_value_preserved_unquoted():
    invoice = _fsm("Invoice", [_trans("Invoice", "order.total > 100")])
    order = _fsm("Order")
    parse_cross_entity_guards([invoice, order])
    refs = invoice.transitions[0].metadata["cross_entity_guards"]
    assert refs[0]["value"] == "100"
    assert refs[0]["operator"] == ">"


def test_bare_identifier_value_preserved():
    invoice = _fsm("Invoice", [_trans("Invoice", "order.status == APPROVED")])
    order = _fsm("Order")
    parse_cross_entity_guards([invoice, order])
    assert invoice.transitions[0].metadata["cross_entity_guards"][0]["value"] == "APPROVED"


def test_boolean_literal_value_preserved():
    invoice = _fsm("Invoice", [_trans("Invoice", "order.is_paid == True")])
    order = _fsm("Order")
    parse_cross_entity_guards([invoice, order])
    assert invoice.transitions[0].metadata["cross_entity_guards"][0]["value"] == "True"


# --- scope limits -----------------------------------------------------------


def test_method_call_not_parsed():
    """`order.is_approved()` is a method call — out of scope for v1."""
    invoice = _fsm("Invoice", [_trans("Invoice", "order.is_approved() and x > 0")])
    order = _fsm("Order")
    # No comparison operator attaches to `order.is_approved` → not captured
    assert parse_cross_entity_guards([invoice, order]) == 0


def test_deep_attribute_chain_skipped():
    """`a.b.c == 'x'` should NOT match as two pairs (a.b and b.c) — negative
    lookbehind prevents drift. Only the topmost ref should even be considered."""
    invoice = _fsm("Invoice", [_trans("Invoice", "order.address.city == 'NYC'")])
    order = _fsm("Order")
    parse_cross_entity_guards([invoice, order])
    # The regex captures `address.city` (since `.address` is preceded by `.`),
    # not `order.address`. So the hint is `address` — which doesn't match
    # any FSM but is still annotated for traceability.
    refs = invoice.transitions[0].metadata.get("cross_entity_guards", [])
    assert all(r["entity_hint"] != "order" for r in refs)


# --- idempotence / dedup ----------------------------------------------------


def test_repeated_run_does_not_duplicate():
    """Running twice should not add duplicate entries."""
    invoice = _fsm("Invoice", [_trans("Invoice", "order.status == 'approved'")])
    order = _fsm("Order")
    parse_cross_entity_guards([invoice, order])
    parse_cross_entity_guards([invoice, order])
    refs = invoice.transitions[0].metadata["cross_entity_guards"]
    assert len(refs) == 1


def test_multiple_transitions_annotated_independently():
    invoice = _fsm("Invoice", [
        _trans("Invoice", "order.status == 'approved'"),
        _trans("Invoice", "customer.tier == 'gold'"),
        _trans("Invoice", None),
    ])
    order = _fsm("Order")
    customer = _fsm("Customer")
    assert parse_cross_entity_guards([invoice, order, customer]) == 2
    assert "cross_entity_guards" in invoice.transitions[0].metadata
    assert "cross_entity_guards" in invoice.transitions[1].metadata
    assert "cross_entity_guards" not in invoice.transitions[2].metadata


def test_empty_fsms_returns_zero():
    assert parse_cross_entity_guards([]) == 0
