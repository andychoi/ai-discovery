"""Tests for the Phase 3 DMN decision-table generator."""

from __future__ import annotations

from ai_discovery.generators.dmn_generator import generate_entity_decisions_markdown
from ai_discovery.graph.models import (
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


def _fsm(entity: str, transitions: list[StateTransition]) -> EntityStateMachine:
    return EntityStateMachine(
        entity=entity,
        entity_id=f"mod.{entity}",
        transitions=transitions,
    )


def _t(
    entity: str,
    from_state: str | None,
    to_state: str,
    *,
    field: str = "status",
    trigger: str | None = None,
    guard_expr: str | None = None,
    cross_guards: list[dict] | None = None,
) -> StateTransition:
    t = StateTransition(
        entity=entity,
        entity_id=f"mod.{entity}",
        field=field,
        from_state=from_state,
        to_state=to_state,
        trigger_function=trigger or f"mod.{entity}_transition",
        guard_expr=guard_expr,
    )
    if cross_guards:
        t.metadata["cross_entity_guards"] = cross_guards
    return t


def _condition(
    target: str, to_state: str, ctx_entity: str, ctx_state: str,
    *, target_field: str = "status", ctx_field: str = "status",
) -> EntityConditionCorrelation:
    return EntityConditionCorrelation(
        target_entity_id=f"mod.{target}",
        target_entity=target,
        target_field=target_field,
        target_to_state=to_state,
        context_entity_id=f"mod.{ctx_entity}",
        context_entity=ctx_entity,
        context_field=ctx_field,
        context_state=ctx_state,
        support=3,
        consistency=1.0,
    )


# --- filtering --------------------------------------------------------------


def test_empty_inputs_returns_header_only():
    out = generate_entity_decisions_markdown([], [])
    assert "# Entity decision tables" in out
    assert "No guarded or conditioned transitions found" in out


def test_unguarded_unconditioned_transitions_skipped():
    """A transition with no guard and no mined condition isn't a DMN rule."""
    fsm = _fsm("Order", [_t("Order", "draft", "submitted")])
    out = generate_entity_decisions_markdown([fsm], [])
    assert "No guarded or conditioned transitions found" in out
    assert "Order.status" not in out


def test_guarded_transition_produces_row():
    fsm = _fsm("Order", [_t("Order", "draft", "submitted", guard_expr="self.total > 0")])
    out = generate_entity_decisions_markdown([fsm], [])
    assert "## Order.status" in out
    assert "self.total > 0" in out
    assert "draft" in out and "submitted" in out


def test_conditioned_transition_produces_row():
    fsm = _fsm("Invoice", [_t("Invoice", None, "pending")])
    conds = [_condition("Invoice", "pending", "Order", "submitted")]
    out = generate_entity_decisions_markdown([fsm], conds)
    assert "## Invoice.status" in out
    assert "Order.status = submitted" in out


def test_mixed_skip_and_include():
    """Among three transitions, only the two with evidence appear."""
    fsm = _fsm("Order", [
        _t("Order", "draft", "submitted"),  # skipped: bare
        _t("Order", "submitted", "approved", guard_expr="total > 100"),
        _t("Order", "approved", "fulfilled"),  # will be kept via condition
    ])
    conds = [_condition("Order", "fulfilled", "Invoice", "paid")]
    out = generate_entity_decisions_markdown([fsm], conds)
    data_rows = [l for l in out.splitlines() if l.startswith("| ") and l[2].isdigit()]
    assert len(data_rows) == 2
    assert "total > 100" in out
    assert "Invoice.status = paid" in out
    # The bare "draft → submitted" transition has no evidence → not a row.
    rows = [l for l in out.splitlines() if l.startswith("| ") and "draft" in l]
    assert rows == []


# --- formatting -------------------------------------------------------------


def test_cross_entity_guard_rendered_as_predicate():
    fsm = _fsm("Invoice", [_t(
        "Invoice", "pending", "paid",
        cross_guards=[{
            "raw": "order.status == 'approved'",
            "entity_hint": "order",
            "resolved_entity_id": "mod.Order",
            "field": "status",
            "operator": "==",
            "value": "approved",
        }],
    )])
    out = generate_entity_decisions_markdown([fsm], [])
    assert "order.status == approved" in out


def test_guard_and_condition_both_rendered():
    fsm = _fsm("Shipment", [_t(
        "Shipment", "pending", "dispatched",
        guard_expr="items > 0",
    )])
    conds = [_condition("Shipment", "dispatched", "Invoice", "paid")]
    out = generate_entity_decisions_markdown([fsm], conds)
    assert "items > 0" in out
    assert "Invoice.status = paid" in out


def test_multiple_cross_guards_joined_with_conjunction():
    fsm = _fsm("Invoice", [_t(
        "Invoice", "pending", "paid",
        cross_guards=[
            {"raw": "", "entity_hint": "order", "resolved_entity_id": "mod.Order",
             "field": "status", "operator": "==", "value": "approved"},
            {"raw": "", "entity_hint": "customer", "resolved_entity_id": "mod.Customer",
             "field": "active", "operator": "==", "value": "true"},
        ],
    )])
    out = generate_entity_decisions_markdown([fsm], [])
    assert "∧" in out
    assert "order.status == approved" in out
    assert "customer.active == true" in out


def test_none_from_state_rendered_as_dash():
    fsm = _fsm("Order", [_t("Order", None, "submitted", guard_expr="x > 0")])
    out = generate_entity_decisions_markdown([fsm], [])
    # from column for this row should be "-"
    row = [l for l in out.splitlines() if "submitted" in l and l.startswith("| ")][0]
    cells = [c.strip() for c in row.strip("|").split("|")]
    # "#", from, guard, context, → to, trigger
    assert cells[1] == "-"


def test_pipe_character_escaped_in_guard():
    fsm = _fsm("Order", [_t("Order", "draft", "done", guard_expr="a | b")])
    out = generate_entity_decisions_markdown([fsm], [])
    # Ensure the raw pipe doesn't survive unescaped (would break the table)
    assert "a \\| b" in out


# --- partitioning -----------------------------------------------------------


def test_separate_tables_per_field():
    fsm = _fsm("Order", [
        _t("Order", "draft", "submitted", field="status", guard_expr="x"),
        _t("Order", "none", "auth", field="payment_status", guard_expr="y"),
    ])
    out = generate_entity_decisions_markdown([fsm], [])
    assert "## Order.status" in out
    assert "## Order.payment_status" in out


def test_separate_tables_per_entity():
    out = generate_entity_decisions_markdown(
        [
            _fsm("Order", [_t("Order", "a", "b", guard_expr="g")]),
            _fsm("Invoice", [_t("Invoice", "a", "b", guard_expr="g")]),
        ],
        [],
    )
    assert "## Order.status" in out
    assert "## Invoice.status" in out


def test_output_is_deterministic():
    """Same inputs → byte-identical output, regardless of input order."""
    fsms_a = [
        _fsm("Order", [_t("Order", "a", "b", guard_expr="g1")]),
        _fsm("Invoice", [_t("Invoice", "a", "b", guard_expr="g2")]),
    ]
    fsms_b = [
        _fsm("Invoice", [_t("Invoice", "a", "b", guard_expr="g2")]),
        _fsm("Order", [_t("Order", "a", "b", guard_expr="g1")]),
    ]
    assert generate_entity_decisions_markdown(fsms_a, []) == \
           generate_entity_decisions_markdown(fsms_b, [])


def test_multiple_conditions_joined_alphabetically():
    """When a transition has multiple context correlations, all appear, sorted."""
    fsm = _fsm("Shipment", [_t("Shipment", "pending", "dispatched")])
    conds = [
        _condition("Shipment", "dispatched", "Invoice", "paid"),
        _condition("Shipment", "dispatched", "Order", "approved"),
    ]
    out = generate_entity_decisions_markdown([fsm], conds)
    context_cell = [l for l in out.splitlines() if "dispatched" in l and l.startswith("| ")][0]
    # Invoice sorts before Order
    inv_idx = context_cell.index("Invoice")
    ord_idx = context_cell.index("Order")
    assert inv_idx < ord_idx


def test_trigger_function_shortened():
    """Fully-qualified trigger names are displayed short for readability."""
    fsm = _fsm("Order", [_t(
        "Order", "draft", "submitted",
        trigger="src.billing.order.Order.submit",
        guard_expr="x",
    )])
    out = generate_entity_decisions_markdown([fsm], [])
    assert "submit" in out
    # full qualified path shouldn't appear in the rendered row
    row = [l for l in out.splitlines() if l.startswith("| 1 |")][0]
    assert "src.billing.order" not in row
