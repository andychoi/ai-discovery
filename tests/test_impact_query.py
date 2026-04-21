"""Tests for the Phase 3 entity-impact query."""

from __future__ import annotations

import pytest

from ai_discovery.graph.impact import (
    EntityNotFound,
    find_entity,
    query_entity_impact,
)
from ai_discovery.graph.models import (
    CrossEntityTransitionLink,
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


def _fsm(
    entity: str, *,
    transitions: list[StateTransition] | None = None,
    states: set[str] | None = None,
    fields: set[str] | None = None,
    sources: set[str] | None = None,
    kind: str = "transactional",
    consolidated_from: list[str] | None = None,
) -> EntityStateMachine:
    meta: dict = {"entity_kind": kind}
    if consolidated_from:
        meta["consolidated_from"] = consolidated_from
    return EntityStateMachine(
        entity=entity,
        entity_id=f"mod.{entity}",
        transitions=transitions or [],
        states=states or set(),
        fields=fields or {"status"},
        source_files=sources or {f"src/{entity.lower()}.py"},
        metadata=meta,
    )


def _t(
    entity: str, from_state: str | None, to_state: str,
    *, field: str = "status", trigger: str | None = None,
    guard_expr: str | None = None,
    entry_points: list[dict] | None = None,
    cross_guards: list[dict] | None = None,
) -> StateTransition:
    t = StateTransition(
        entity=entity, entity_id=f"mod.{entity}",
        field=field, from_state=from_state, to_state=to_state,
        trigger_function=trigger or f"mod.{entity.lower()}_trans",
        guard_expr=guard_expr,
        entry_points=entry_points or [],
    )
    if cross_guards:
        t.metadata["cross_entity_guards"] = cross_guards
    return t


def _link(frm: str, frm_state: str, to: str, to_state: str, *,
          support: int = 3, conf: float = 0.9) -> CrossEntityTransitionLink:
    return CrossEntityTransitionLink(
        from_entity_id=f"mod.{frm}", from_entity=frm, from_field="status",
        from_state=frm_state,
        to_entity_id=f"mod.{to}", to_entity=to, to_field="status",
        to_state=to_state, support=support, directional_confidence=conf,
    )


def _cond(target: str, to_state: str, ctx: str, ctx_state: str,
          *, support: int = 3, consistency: float = 1.0
          ) -> EntityConditionCorrelation:
    return EntityConditionCorrelation(
        target_entity_id=f"mod.{target}", target_entity=target,
        target_field="status", target_to_state=to_state,
        context_entity_id=f"mod.{ctx}", context_entity=ctx,
        context_field="status", context_state=ctx_state,
        support=support, consistency=consistency,
    )


# --- find_entity ------------------------------------------------------------


def test_find_entity_exact_id():
    o = _fsm("Order")
    assert find_entity("mod.Order", [o]) is o


def test_find_entity_exact_name():
    o = _fsm("Order")
    assert find_entity("Order", [_fsm("Invoice"), o]) is o


def test_find_entity_case_insensitive_name():
    o = _fsm("Order")
    assert find_entity("order", [o]) is o
    assert find_entity("ORDER", [o]) is o


def test_find_entity_unknown_lists_candidates():
    with pytest.raises(EntityNotFound) as exc:
        find_entity("Nope", [_fsm("Order"), _fsm("Invoice")])
    msg = str(exc.value)
    assert "Order" in msg and "Invoice" in msg


def test_find_entity_id_wins_over_name():
    """When both id match and a name match exist, id match is preferred."""
    a = _fsm("Order")  # entity_id=mod.Order
    b = EntityStateMachine(entity="Order", entity_id="mod.shadow.Order")
    # Querying by the id should find exactly that entry, not stop at "Order".
    assert find_entity("mod.shadow.Order", [a, b]) is b


# --- report structure -------------------------------------------------------


def test_header_shows_core_metadata():
    f = _fsm("Order", states={"draft", "submitted"},
             fields={"status", "total"}, kind="transactional")
    out = query_entity_impact("Order", [f], [], [])
    assert "# Impact report: Order" in out
    assert "`mod.Order`" in out
    assert "transactional" in out
    assert "status" in out and "total" in out
    assert "draft" in out and "submitted" in out


def test_consolidated_from_surfaced_when_present():
    f = _fsm("Order", consolidated_from=["OrderEntity", "Orders"])
    out = query_entity_impact("Order", [f], [], [])
    assert "Consolidated from" in out
    assert "OrderEntity" in out and "Orders" in out


def test_no_transitions_renders_placeholder():
    out = query_entity_impact("Order", [_fsm("Order")], [], [])
    assert "No transitions recorded" in out


def test_transitions_table_includes_all_fields():
    f = _fsm("Order", transitions=[_t(
        "Order", "draft", "submitted",
        guard_expr="total > 0",
        entry_points=[{"kind": "API", "qualified_name": "routes.create",
                       "confidence": 0.9, "hop_count": 1}],
    )])
    out = query_entity_impact("Order", [f], [], [])
    assert "## Transitions" in out
    assert "draft" in out and "submitted" in out
    assert "total > 0" in out
    assert "API:create" in out


def test_cross_entity_guards_preferred_in_impact_report():
    f = _fsm("Invoice", transitions=[_t(
        "Invoice", "pending", "paid",
        guard_expr="order.status == 'approved'",
        cross_guards=[{
            "entity_hint": "order", "field": "status",
            "operator": "==", "value": "approved",
        }],
    )])
    out = query_entity_impact("Invoice", [f], [], [])
    assert "order.status == approved" in out


# --- cross-entity links -----------------------------------------------------


def test_inbound_links_only_when_entity_is_target():
    f = _fsm("Invoice")
    links = [
        _link("Order", "submitted", "Invoice", "pending"),  # inbound
        _link("Invoice", "paid", "Shipment", "dispatched"),  # outbound (irrelevant)
    ]
    out = query_entity_impact("Invoice", [_fsm("Order"), f, _fsm("Shipment")], links, [])
    assert "## Inbound sequence links" in out
    # Inbound must show Order → Invoice
    inbound_section = out.split("## Inbound sequence links")[1].split("## Outbound")[0]
    assert "Order.status" in inbound_section
    assert "Invoice.status" in inbound_section


def test_outbound_links_only_when_entity_is_source():
    f = _fsm("Order")
    links = [_link("Order", "submitted", "Invoice", "pending")]
    out = query_entity_impact("Order", [f, _fsm("Invoice")], links, [])
    assert "## Outbound sequence links" in out
    assert "Invoice.status" in out
    # No inbound section expected — no link ends at Order.
    assert "## Inbound sequence links" not in out


def test_link_annotation_shows_support_and_confidence():
    f = _fsm("Order")
    links = [_link("Order", "submitted", "Invoice", "pending", support=5, conf=0.82)]
    out = query_entity_impact("Order", [f, _fsm("Invoice")], links, [])
    assert "support=5" in out
    assert "confidence=0.82" in out


def test_no_links_section_omitted():
    """If the entity has neither inbound nor outbound links, those sections
    disappear entirely — no empty headers."""
    out = query_entity_impact("Order", [_fsm("Order")], [], [])
    assert "Inbound sequence links" not in out
    assert "Outbound sequence links" not in out


# --- conditions -------------------------------------------------------------


def test_conditions_on_entity_shown_as_precondition():
    f = _fsm("Invoice")
    conds = [_cond("Invoice", "pending", "Order", "submitted",
                   support=5, consistency=1.0)]
    out = query_entity_impact("Invoice", [f, _fsm("Order")], [], conds)
    assert "Conditions on Invoice transitions" in out
    assert "Invoice.status → pending" in out
    assert "`Order.status` = `submitted`" in out
    assert "support=5" in out


def test_entity_as_context_shown_separately():
    """When Order is the *context* (not target), it appears in the context
    section — with different wording."""
    f = _fsm("Order")
    conds = [_cond("Invoice", "pending", "Order", "submitted",
                   support=4, consistency=1.0)]
    out = query_entity_impact("Order", [f, _fsm("Invoice")], [], conds)
    assert "Order as context for other entities" in out
    assert "Invoice.status → pending" in out
    assert "`Order.status` = `submitted`" in out


def test_same_entity_as_both_target_and_context_in_different_sections():
    """Sanity: sections don't cross-pollinate."""
    f = _fsm("Order")
    conds = [
        _cond("Order", "approved", "Customer", "verified"),  # Order is target
        _cond("Invoice", "pending", "Order", "submitted"),    # Order is context
    ]
    out = query_entity_impact("Order", [f, _fsm("Customer"), _fsm("Invoice")], [], conds)
    # Target section mentions Customer (the context)
    target_section = out.split("Conditions on Order transitions")[1].split("##")[0]
    assert "Customer.status" in target_section
    # Context section mentions Invoice (the target)
    ctx_section = out.split("Order as context for other entities")[1]
    assert "Invoice.status" in ctx_section


# --- determinism ------------------------------------------------------------


def test_output_is_deterministic():
    fsms = [_fsm("Order", transitions=[
        _t("Order", "draft", "submitted"),
        _t("Order", "submitted", "approved"),
    ]), _fsm("Invoice"), _fsm("Customer")]
    links = [
        _link("Customer", "new", "Order", "draft"),
        _link("Order", "submitted", "Invoice", "pending"),
    ]
    conds = [
        _cond("Order", "approved", "Customer", "verified"),
        _cond("Invoice", "pending", "Order", "submitted"),
    ]
    a = query_entity_impact("Order", fsms, links, conds)
    # Re-query with shuffled inputs → same bytes
    b = query_entity_impact("Order", list(reversed(fsms)),
                            list(reversed(links)), list(reversed(conds)))
    assert a == b
