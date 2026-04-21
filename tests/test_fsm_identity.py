"""Phase 2.4 tests: `fsm_identity.consolidate_entities`.

The contract under test:
- FSMs with the same stem + high-Jaccard field sets merge into one.
- Parent/child classes never merge (inheritance-aware).
- Mixin-contributed fields are subtracted so sibling classes don't falsely merge.
- ORM meta (`__tablename__`, `Meta`, `objects`, …) never drives merges.
- Asymmetric field sets register as projection links, not merges.
- Classless FSMs (Redux-style) merge among themselves when stems align.
- A class + classless pair with the same stem + subset fields merges (cross pass).
- Distinct entities sharing a short name get display-disambiguated after consolidation.
- Provenance metadata records the merge rule + original names.
"""

from __future__ import annotations

from ai_discovery.graph.fsm_identity import consolidate_entities
from ai_discovery.graph.models import CodeNode, EntityStateMachine, StateTransition


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _class(
    name: str,
    qualified: str | None = None,
    fields: list[str] | None = None,
    bases: list[str] | None = None,
    file_path: str | None = None,
    node_type: str = "class",
) -> CodeNode:
    return CodeNode(
        file_path=file_path or f"src/{name.lower()}.py",
        language="python",
        node_type=node_type,
        name=name,
        qualified_name=qualified or f"src.{name.lower()}.{name}",
        source_code="",
        line_start=1,
        line_end=10,
        fields=fields or [],
        bases=bases or [],
    )


def _fsm(
    entity: str,
    entity_id: str | None = None,
    transitions: list[StateTransition] | None = None,
) -> EntityStateMachine:
    eid = entity_id or f"src.{entity.lower()}.{entity}"
    return EntityStateMachine(
        entity=entity,
        entity_id=eid,
        transitions=transitions or [
            StateTransition(
                entity=entity, entity_id=eid, field="status", to_state="ACTIVE",
                trigger_function=f"{eid}.activate",
            )
        ],
        fields={"status"},
    )


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty():
    consolidated, links = consolidate_entities([], [])
    assert consolidated == []
    assert links == []


def test_single_fsm_passes_through_unchanged():
    nodes = [_class("Order", fields=["status", "total", "customer_id"])]
    fsms = [_fsm("Order")]
    consolidated, links = consolidate_entities(fsms, nodes)
    assert len(consolidated) == 1
    assert consolidated[0].entity == "Order"
    assert consolidated[0].metadata == {}
    assert links == []


# ---------------------------------------------------------------------------
# Pass 1: classful merge
# ---------------------------------------------------------------------------

def test_merges_order_and_orderentity_via_identical_fields():
    """Classic case: `Order` (domain) + `OrderEntity` (persistence) → one FSM."""
    fields = ["status", "total", "customer_id", "created_at"]
    nodes = [
        _class("Order", qualified="src.billing.order.Order", fields=fields),
        _class(
            "OrderEntity",
            qualified="src.billing.persistence.OrderEntity",
            fields=fields,
            node_type="db_model",
        ),
    ]
    fsms = [
        _fsm("Order", entity_id="src.billing.order.Order"),
        _fsm("OrderEntity", entity_id="src.billing.persistence.OrderEntity"),
    ]
    consolidated, links = consolidate_entities(fsms, nodes)

    assert len(consolidated) == 1
    merged = consolidated[0]
    assert merged.entity == "Order"  # unsuffixed name wins canonical
    meta = merged.metadata
    assert sorted(meta["consolidated_from"]) == ["Order", "OrderEntity"]
    assert meta["merge_rule"].startswith("pass1:")
    assert "class" in meta["source_node_types"]
    assert "db_model" in meta["source_node_types"]


def test_inheritance_relationship_blocks_merge():
    """Parent/child classes must never merge even when stems align."""
    nodes = [
        _class(
            "Order",
            qualified="src.base.Order",
            fields=["status", "id", "total"],
        ),
        _class(
            "SpecialOrder",
            qualified="src.special.SpecialOrder",
            fields=["status", "id", "total"],
            bases=["Order"],
        ),
    ]
    fsms = [
        _fsm("Order", entity_id="src.base.Order"),
        _fsm("SpecialOrder", entity_id="src.special.SpecialOrder"),
    ]
    consolidated, _ = consolidate_entities(fsms, nodes)
    # Stems differ enough that they shouldn't pair-match anyway, but even if
    # they did, the inheritance guard is the backstop.
    assert len(consolidated) == 2


def test_mixin_fields_subtracted_prevents_false_merge():
    """Two sibling classes sharing a mixin's fields must NOT merge."""
    mixin = _class(
        "TimestampedMixin",
        qualified="src.common.TimestampedMixin",
        fields=["created_at", "updated_at", "version"],
    )
    # Order and Customer each inherit `created_at, updated_at, version` from
    # the mixin. Their *own* fields are disjoint — they're distinct entities.
    order = _class(
        "Order",
        qualified="src.orders.Order",
        fields=["created_at", "updated_at", "version", "total"],
        bases=["TimestampedMixin"],
    )
    customer = _class(
        "Customer",
        qualified="src.customers.Customer",
        fields=["created_at", "updated_at", "version", "email"],
        bases=["TimestampedMixin"],
    )
    # A second descendant so the mixin gets detected as a mixin (≥2 uses).
    invoice = _class(
        "Invoice",
        qualified="src.invoices.Invoice",
        fields=["created_at", "updated_at", "version", "amount"],
        bases=["TimestampedMixin"],
    )
    fsms = [
        _fsm("Order", entity_id="src.orders.Order"),
        _fsm("Customer", entity_id="src.customers.Customer"),
        _fsm("Invoice", entity_id="src.invoices.Invoice"),
    ]
    consolidated, _ = consolidate_entities(fsms, [mixin, order, customer, invoice])
    # The mixin itself should NOT be in the output (it's not an entity).
    entities = {f.entity for f in consolidated}
    assert "TimestampedMixin" not in entities
    # All three descendants kept distinct.
    assert entities == {"Order", "Customer", "Invoice"}


def test_orm_meta_fields_dont_drive_merge():
    """__tablename__/Meta/objects are filtered before computing similarity."""
    # Two unrelated models share only ORM boilerplate. Without filtering they'd
    # have Jaccard 1.0 on filtered sets; with filtering they have 0.0.
    nodes = [
        _class(
            "Widget",
            qualified="src.catalog.Widget",
            fields=["__tablename__", "Meta", "objects", "id", "name"],
        ),
        _class(
            "Sprocket",
            qualified="src.parts.Sprocket",
            fields=["__tablename__", "Meta", "objects", "id", "sku"],
        ),
    ]
    fsms = [
        _fsm("Widget", entity_id="src.catalog.Widget"),
        _fsm("Sprocket", entity_id="src.parts.Sprocket"),
    ]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert len(consolidated) == 2


# ---------------------------------------------------------------------------
# Projection links
# ---------------------------------------------------------------------------

def test_dto_records_projection_link_not_merge():
    """OrderDto has a small subset of Order's fields → projection link."""
    nodes = [
        _class(
            "Order",
            qualified="src.orders.Order",
            fields=["status", "total", "customer_id", "created_at",
                    "shipping_address", "billing_address", "items"],
        ),
        _class(
            "OrderDto",
            qualified="src.api.OrderDto",
            fields=["status", "total"],
        ),
    ]
    fsms = [
        _fsm("Order", entity_id="src.orders.Order"),
        _fsm("OrderDto", entity_id="src.api.OrderDto"),
    ]
    consolidated, links = consolidate_entities(fsms, nodes)
    # Two FSMs kept; one projection link.
    assert len(consolidated) == 2
    assert len(links) == 1
    pl = links[0]
    assert pl["entity"] == "src.orders.Order"
    assert pl["projected_by"] == "src.api.OrderDto"
    assert pl["coverage"] == 1.0
    # Order's FSM carries the projection in metadata.
    order_fsm = next(f for f in consolidated if f.entity == "Order")
    assert order_fsm.metadata["projection_links"] == ["src.api.OrderDto"]


# ---------------------------------------------------------------------------
# Generic-name drop
# ---------------------------------------------------------------------------

def test_generic_named_fsm_dropped_when_no_backing_class():
    """Duck-typed `def f(thing): thing.status = 'x'` produces parser noise."""
    fsms = [
        _fsm("thing", entity_id="src.util.process::thing"),
        _fsm("Order", entity_id="src.orders.Order"),
    ]
    nodes = [_class("Order", qualified="src.orders.Order", fields=["status"])]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert {f.entity for f in consolidated} == {"Order"}


def test_generic_named_fsm_kept_if_real_class_exists():
    """A real class named `Data` is legitimate — don't drop it."""
    nodes = [_class("Data", qualified="src.schema.Data", fields=["status", "payload"])]
    fsms = [_fsm("Data", entity_id="src.schema.Data")]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert {f.entity for f in consolidated} == {"Data"}


# ---------------------------------------------------------------------------
# Pass 2: classless
# ---------------------------------------------------------------------------

def test_classless_fsms_merge_on_matching_stems():
    """Two Redux reducers or utility-function FSMs with matching stem+fields."""
    fsms = [
        EntityStateMachine(
            entity="orderReducer",
            entity_id="src.store.orderReducer",
            transitions=[
                StateTransition(
                    entity="orderReducer",
                    entity_id="src.store.orderReducer",
                    field="status", to_state="LOADING",
                    trigger_function="src.store.orderReducer",
                ),
                StateTransition(
                    entity="orderReducer",
                    entity_id="src.store.orderReducer",
                    field="total", to_state="0",
                    trigger_function="src.store.orderReducer",
                ),
            ],
        ),
        EntityStateMachine(
            entity="orderSlice",
            entity_id="src.slices.orderSlice",
            transitions=[
                StateTransition(
                    entity="orderSlice",
                    entity_id="src.slices.orderSlice",
                    field="status", to_state="LOADING",
                    trigger_function="src.slices.orderSlice",
                ),
                StateTransition(
                    entity="orderSlice",
                    entity_id="src.slices.orderSlice",
                    field="total", to_state="0",
                    trigger_function="src.slices.orderSlice",
                ),
            ],
        ),
    ]
    # No backing classes.
    consolidated, _ = consolidate_entities(fsms, [])
    assert len(consolidated) == 1
    assert consolidated[0].metadata["merge_rule"].startswith("pass2:")


# ---------------------------------------------------------------------------
# Pass 3: cross (classful + classless with subset fields)
# ---------------------------------------------------------------------------

def test_cross_pass_merges_sql_style_classless_into_class():
    """Raw-SQL-style FSM's observed fields are a subset of a class → merge."""
    nodes = [
        _class(
            "Order",
            qualified="src.orders.Order",
            fields=["status", "total", "customer_id", "created_at"],
        ),
    ]
    fsms = [
        _fsm("Order", entity_id="src.orders.Order"),
        # A classless FSM for raw-SQL-touching code; only `status` observed.
        EntityStateMachine(
            entity="Order",
            entity_id="src.sql_helpers.update_order::Order",
            transitions=[
                StateTransition(
                    entity="Order",
                    entity_id="src.sql_helpers.update_order::Order",
                    field="status", to_state="SHIPPED",
                    trigger_function="src.sql_helpers.update_order",
                ),
            ],
        ),
    ]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert len(consolidated) == 1
    assert consolidated[0].metadata["merge_rule"].startswith("pass3:classless_subset")


# ---------------------------------------------------------------------------
# Display disambiguation
# ---------------------------------------------------------------------------

def test_same_short_name_different_modules_kept_apart_and_disambiguated():
    """billing.Order vs ecommerce.Order: distinct entities — display shows module."""
    nodes = [
        _class(
            "Order",
            qualified="src.billing.Order",
            fields=["status", "supplier_id", "po_number", "due_date"],
            file_path="src/billing/order.py",
        ),
        _class(
            "Order",
            qualified="src.ecommerce.Order",
            fields=["status", "cart_id", "shipping_address", "payment_method"],
            file_path="src/ecommerce/order.py",
        ),
    ]
    fsms = [
        EntityStateMachine(
            entity="Order",
            entity_id="src.billing.Order",
            transitions=[StateTransition(
                entity="Order", entity_id="src.billing.Order",
                field="status", to_state="APPROVED",
                trigger_function="src.billing.order.approve",
            )],
            source_files={"src/billing/order.py"},
        ),
        EntityStateMachine(
            entity="Order",
            entity_id="src.ecommerce.Order",
            transitions=[StateTransition(
                entity="Order", entity_id="src.ecommerce.Order",
                field="status", to_state="CHECKOUT",
                trigger_function="src.ecommerce.order.checkout",
            )],
            source_files={"src/ecommerce/order.py"},
        ),
    ]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert len(consolidated) == 2
    displays = sorted(f.entity for f in consolidated)
    assert displays == ["Order (billing)", "Order (ecommerce)"]
    # Entity IDs remain untouched; disambiguation only affects display.
    ids = sorted(f.entity_id for f in consolidated)
    assert ids == ["src.billing.Order", "src.ecommerce.Order"]


# ---------------------------------------------------------------------------
# Stability / determinism
# ---------------------------------------------------------------------------

def test_output_sorted_for_stable_diffs():
    nodes = [
        _class("Zeta", qualified="src.z.Zeta", fields=["status"]),
        _class("Alpha", qualified="src.a.Alpha", fields=["status"]),
        _class("Mu", qualified="src.m.Mu", fields=["status"]),
    ]
    fsms = [
        _fsm("Zeta", entity_id="src.z.Zeta"),
        _fsm("Alpha", entity_id="src.a.Alpha"),
        _fsm("Mu", entity_id="src.m.Mu"),
    ]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert [f.entity for f in consolidated] == ["Alpha", "Mu", "Zeta"]


def test_merged_transitions_adopt_canonical_entity_id():
    """Downstream joins need every transition to use the canonical id."""
    fields = ["status", "total", "customer_id"]
    nodes = [
        _class("Order", qualified="src.billing.Order", fields=fields),
        _class("OrderEntity", qualified="src.billing.OrderEntity",
               fields=fields, node_type="db_model"),
    ]
    t1 = StateTransition(
        entity="Order", entity_id="src.billing.Order",
        field="status", to_state="A", trigger_function="src.billing.order.a",
    )
    t2 = StateTransition(
        entity="OrderEntity", entity_id="src.billing.OrderEntity",
        field="status", to_state="B", trigger_function="src.billing.persistence.b",
    )
    fsms = [
        EntityStateMachine(
            entity="Order", entity_id="src.billing.Order", transitions=[t1],
        ),
        EntityStateMachine(
            entity="OrderEntity", entity_id="src.billing.OrderEntity",
            transitions=[t2],
        ),
    ]
    consolidated, _ = consolidate_entities(fsms, nodes)
    assert len(consolidated) == 1
    merged = consolidated[0]
    # Every transition under the merged FSM reports the canonical identity.
    assert all(t.entity == merged.entity for t in merged.transitions)
    assert all(t.entity_id == merged.entity_id for t in merged.transitions)
