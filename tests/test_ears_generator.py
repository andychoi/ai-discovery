"""Tests for the Phase 3 EARS requirements generator."""

from __future__ import annotations

from ai_discovery.generators.ears_generator import generate_entity_ears_markdown
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
    to_state: str | None,
    *,
    field: str = "status",
    trigger: str | None = "submit",
    guard_expr: str | None = None,
    entry_points: list[dict] | None = None,
    cross_guards: list[dict] | None = None,
) -> StateTransition:
    t = StateTransition(
        entity=entity,
        entity_id=f"mod.{entity}",
        field=field,
        from_state=from_state,
        to_state=to_state,
        trigger_function=trigger,
        guard_expr=guard_expr,
        entry_points=entry_points or [],
    )
    if cross_guards:
        t.metadata["cross_entity_guards"] = cross_guards
    return t


def _condition(
    target: str, to_state: str, ctx_entity: str, ctx_state: str, *,
    support: int = 3,
) -> EntityConditionCorrelation:
    return EntityConditionCorrelation(
        target_entity_id=f"mod.{target}",
        target_entity=target,
        target_field="status",
        target_to_state=to_state,
        context_entity_id=f"mod.{ctx_entity}",
        context_entity=ctx_entity,
        context_field="status",
        context_state=ctx_state,
        support=support,
        consistency=1.0,
    )


# --- basics -----------------------------------------------------------------


def test_empty_returns_placeholder():
    out = generate_entity_ears_markdown([], [])
    assert "# Entity requirements (EARS)" in out
    assert "No transitions found" in out


def test_transition_without_to_state_skipped():
    """A parse-degraded transition with no to_state isn't a behavioral fact."""
    fsm = _fsm("Order", [_t("Order", "draft", None)])
    out = generate_entity_ears_markdown([fsm], [])
    assert "No transitions found" in out


def test_minimal_transition_emits_req_with_trigger_fallback():
    """No entry_point, no guard → trigger_function fills the WHEN slot."""
    fsm = _fsm("Order", [_t("Order", "draft", "submitted", trigger="submit_order")])
    out = generate_entity_ears_markdown([fsm], [])
    assert "REQ-Order-status-001" in out
    assert "`submit_order` is invoked" in out
    assert "SHALL transition from `draft` to `submitted`" in out
    assert "**IF**" not in out  # no guard


def test_ubiquitous_phrasing_when_from_state_missing():
    """No from_state → the `from` clause is dropped, but the requirement stands."""
    fsm = _fsm("Order", [_t("Order", None, "submitted", trigger="create_order")])
    out = generate_entity_ears_markdown([fsm], [])
    # Should be "SHALL transition to `submitted`" with no "from ..."
    assert "SHALL transition  to `submitted`" in out or "SHALL transition to `submitted`" in out
    assert "from `" not in out


# --- entry-point handling ---------------------------------------------------


def test_api_entry_point_rendered_naturally():
    fsm = _fsm("Order", [_t(
        "Order", "draft", "submitted",
        entry_points=[{
            "kind": "API", "qualified_name": "routes.POST /orders",
            "confidence": 0.85, "hop_count": 2,
        }],
    )])
    out = generate_entity_ears_markdown([fsm], [])
    assert "API endpoint `routes.POST /orders` is invoked" in out
    assert "entry-point confidence: 0.85" in out


def test_multiple_entry_points_best_wins():
    """The first entry (lowest hop_count per linker contract) is used."""
    fsm = _fsm("Order", [_t(
        "Order", "draft", "submitted",
        entry_points=[
            {"kind": "API", "qualified_name": "routes.create", "confidence": 0.9, "hop_count": 1},
            {"kind": "CLI", "qualified_name": "cli.create", "confidence": 0.95, "hop_count": 3},
        ],
    )])
    out = generate_entity_ears_markdown([fsm], [])
    assert "routes.create" in out
    # CLI shouldn't appear as the primary WHEN — only the first entry is used.
    assert "CLI command `cli.create`" not in out


def test_all_entry_point_kinds_phrased():
    """Every documented entry kind gets a human phrase, not the raw code."""
    kinds = [
        ("API", "API endpoint"),
        ("UI", "UI action"),
        ("Batch", "batch job"),
        ("Event", "event"),
        ("CLI", "CLI command"),
    ]
    for kind, phrase in kinds:
        fsm = _fsm("X", [_t(
            "X", "a", "b",
            entry_points=[{"kind": kind, "qualified_name": "q", "confidence": 1.0, "hop_count": 0}],
        )])
        out = generate_entity_ears_markdown([fsm], [])
        assert phrase in out, f"kind {kind} missing phrasing {phrase!r}"


# --- guards & conditions ----------------------------------------------------


def test_raw_guard_expr_rendered():
    fsm = _fsm("Order", [_t("Order", "draft", "submitted", guard_expr="self.total > 0")])
    out = generate_entity_ears_markdown([fsm], [])
    assert "**IF** `self.total > 0`" in out


def test_cross_entity_guard_takes_precedence_over_raw():
    """When both exist, cross_entity_guards is the parsed form — use it."""
    fsm = _fsm("Invoice", [_t(
        "Invoice", "pending", "paid",
        guard_expr="order.status == 'approved'",
        cross_guards=[{
            "entity_hint": "order", "resolved_entity_id": "mod.Order",
            "field": "status", "operator": "==", "value": "approved",
        }],
    )])
    out = generate_entity_ears_markdown([fsm], [])
    assert "`order.status` == `approved`" in out


def test_condition_rendered_as_while():
    """3.1d conditions produce a WHILE precondition clause."""
    fsm = _fsm("Invoice", [_t("Invoice", None, "pending")])
    ctx = [_condition("Invoice", "pending", "Order", "submitted", support=5)]
    out = generate_entity_ears_markdown([fsm], ctx)
    assert "**WHILE** `Order.status` IS `submitted`" in out
    assert "context support: 5" in out


def test_multiple_conditions_joined_with_and():
    fsm = _fsm("Ship", [_t("Ship", "pending", "dispatched")])
    ctx = [
        _condition("Ship", "dispatched", "Invoice", "paid", support=4),
        _condition("Ship", "dispatched", "Order", "approved", support=7),
    ]
    out = generate_entity_ears_markdown([fsm], ctx)
    # Both contexts present, joined with " AND "
    assert "`Invoice.status` IS `paid` AND `Order.status` IS `approved`" in out
    # Support annotation uses max — most-observed context carries the block
    assert "context support: 7" in out


def test_full_stack_while_when_then_if():
    """The complex EARS form: all four keywords appear in the right order."""
    fsm = _fsm("Order", [_t(
        "Order", "draft", "submitted",
        guard_expr="total > 0",
        entry_points=[{"kind": "API", "qualified_name": "POST /orders",
                       "confidence": 0.8, "hop_count": 1}],
    )])
    ctx = [_condition("Order", "submitted", "Customer", "active")]
    out = generate_entity_ears_markdown([fsm], ctx)
    # Keywords must appear in EARS canonical order: WHILE, WHEN, THEN, IF
    while_pos = out.index("**WHILE**")
    when_pos = out.index("**WHEN**")
    then_pos = out.index("**THEN**")
    if_pos = out.index("**IF**")
    assert while_pos < when_pos < then_pos < if_pos


# --- structure & determinism ------------------------------------------------


def test_req_ids_are_stable_and_numbered():
    fsm = _fsm("Order", [
        _t("Order", "draft", "submitted"),
        _t("Order", "submitted", "approved"),
    ])
    out = generate_entity_ears_markdown([fsm], [])
    assert "REQ-Order-status-001" in out
    assert "REQ-Order-status-002" in out


def test_multiple_fields_produce_multiple_sections():
    fsm = _fsm("Order", [
        _t("Order", "draft", "submitted", field="status"),
        _t("Order", "none", "auth", field="payment_status"),
    ])
    out = generate_entity_ears_markdown([fsm], [])
    assert "## Order.status" in out
    assert "## Order.payment_status" in out


def test_output_is_deterministic():
    fsms_a = [
        _fsm("Order", [_t("Order", "a", "b")]),
        _fsm("Invoice", [_t("Invoice", "a", "b")]),
    ]
    fsms_b = [
        _fsm("Invoice", [_t("Invoice", "a", "b")]),
        _fsm("Order", [_t("Order", "a", "b")]),
    ]
    assert generate_entity_ears_markdown(fsms_a, []) == \
           generate_entity_ears_markdown(fsms_b, [])


def test_no_entry_point_produces_no_confidence_annotation():
    """When the WHEN clause is just trigger_function, there's no confidence."""
    fsm = _fsm("Order", [_t("Order", "draft", "submitted", trigger="submit")])
    out = generate_entity_ears_markdown([fsm], [])
    assert "entry-point confidence" not in out
