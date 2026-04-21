"""Tests for Phase 3.1a cross-entity denormalization detector."""

from __future__ import annotations

from ai_discovery.graph.entity_classifier import classify_entities
from ai_discovery.graph.entity_correlator import detect_denormalization_links
from ai_discovery.graph.models import EntityStateMachine


def _fsm(name: str, fields: set[str], entity_id: str | None = None) -> EntityStateMachine:
    return EntityStateMachine(
        entity=name,
        entity_id=entity_id or f"mod.{name}",
        transitions=[],
        states=set(),
        fields=fields,
        source_files=set(),
    )


# --- basic detection --------------------------------------------------------

def test_simple_denorm_detected():
    """`order.customer_name` is a copy of `customer.name`."""
    order = _fsm("order", {"id", "status", "customer_id", "customer_name", "customer_email"})
    customer = _fsm("customer", {"id", "name", "email"})
    out = detect_denormalization_links([order, customer])
    links = {d["field"]: d for d in out[0].metadata.get("denormalized_fields", [])}
    assert "customer_name" in links
    assert links["customer_name"]["from_entity"] == "customer"
    assert links["customer_name"]["from_field"] == "name"
    assert "customer_email" in links


def test_fk_suffix_not_denorm():
    """`customer_id` is a normal FK, not a denormalized copy."""
    order = _fsm("order", {"id", "customer_id"})
    customer = _fsm("customer", {"id", "name"})
    out = detect_denormalization_links([order, customer])
    links = out[0].metadata.get("denormalized_fields", [])
    assert not any(link["field"] == "customer_id" for link in links)


def test_no_matching_entity_no_denorm():
    """`shipping_city` has no `shipping` entity — false negative, as designed."""
    order = _fsm("order", {"id", "status", "shipping_city"})
    out = detect_denormalization_links([order])
    assert "denormalized_fields" not in out[0].metadata


def test_matching_prefix_but_missing_field():
    """Prefix matches but tail doesn't exist on target — not a denorm."""
    order = _fsm("order", {"id", "customer_favorite_color"})
    customer = _fsm("customer", {"id", "name"})
    out = detect_denormalization_links([order, customer])
    assert "denormalized_fields" not in out[0].metadata


def test_own_stem_prefix_not_denorm():
    """`order.order_total` uses the entity's own stem — local field, not denorm."""
    order = _fsm("order", {"id", "order_total", "status"})
    # There's no other "order" entity, so this is purely a self-prefix case.
    out = detect_denormalization_links([order])
    assert "denormalized_fields" not in out[0].metadata


# --- stem normalization -----------------------------------------------------

def test_camel_case_entity_stem_detected():
    """`OrderItem` normalizes to `order_item`."""
    order_item = _fsm("OrderItem", {"order_id", "product_id", "order_item_notes"})
    # The field `order_item_notes` starts with own stem, so won't match.
    # Add a real denorm from Product:
    order_item.fields = {"order_id", "product_id", "product_name"}
    product = _fsm("Product", {"id", "name", "price"})
    out = detect_denormalization_links([order_item, product])
    links = out[0].metadata.get("denormalized_fields", [])
    assert any(link["field"] == "product_name" for link in links)


def test_depluralization_on_entity_name():
    """`customers` entity also matches `customer_name` fields."""
    order = _fsm("order", {"id", "customer_name"})
    customers = _fsm("customers", {"id", "name"})
    out = detect_denormalization_links([order, customers])
    links = out[0].metadata.get("denormalized_fields", [])
    assert any(link["field"] == "customer_name" for link in links)


def test_ies_plural_stem():
    """`companies` → `company` stem."""
    order = _fsm("order", {"id", "company_name"})
    companies = _fsm("companies", {"id", "name"})
    out = detect_denormalization_links([order, companies])
    links = out[0].metadata.get("denormalized_fields", [])
    assert any(link["field"] == "company_name" for link in links)


def test_entity_suffix_stripped():
    """`CustomerEntity` normalizes to `customer`."""
    order = _fsm("order", {"id", "customer_email"})
    ce = _fsm("CustomerEntity", {"id", "email"})
    out = detect_denormalization_links([order, ce])
    links = out[0].metadata.get("denormalized_fields", [])
    assert any(link["field"] == "customer_email" for link in links)


# --- multi-prefix disambiguation --------------------------------------------

def test_longer_prefix_preferred():
    """If both `customer` and `customer_account` exist, the longer matches first."""
    order = _fsm("order", {"id", "customer_account_number"})
    customer = _fsm("customer", {"id", "account_number"})
    customer_account = _fsm("customer_account", {"id", "number"})
    out = detect_denormalization_links([order, customer, customer_account])
    links = out[0].metadata.get("denormalized_fields", [])
    # Longer prefix wins — the link points to `customer_account.number`.
    assert any(
        link["field"] == "customer_account_number"
        and link["from_entity"] == "customer_account"
        and link["from_field"] == "number"
        for link in links
    )


# --- bookkeeping exclusions -------------------------------------------------

def test_bookkeeping_fields_not_denorm():
    """`created_at`, `updated_at`, `id` appear on every entity — never denorm."""
    order = _fsm("order", {"id", "created_at", "updated_at"})
    customer = _fsm("customer", {"id", "created_at"})
    out = detect_denormalization_links([order, customer])
    links = out[0].metadata.get("denormalized_fields", [])
    assert not any(link["field"] in {"id", "created_at", "updated_at"} for link in links)


# --- integration: denorm + classifier ---------------------------------------

def test_denormalized_junction_classifies_as_junction():
    """The canonical case — `order_item` with denorm product fields."""
    order_item = EntityStateMachine(
        entity="order_item",
        entity_id="mod.order_item",
        fields={"order_id", "product_id", "product_name", "product_price", "id"},
    )
    order = _fsm("order", {"id", "status", "customer_id"})
    product = _fsm("product", {"id", "name", "price"})
    fsms = [order_item, order, product]
    detect_denormalization_links(fsms)
    classify_entities(fsms)
    assert order_item.metadata["entity_kind"] == "junction"
    # Explainability: the denorm fields are recorded in the classifier signals.
    assert "denormalized_fields" in order_item.metadata["entity_kind_signals"]


def test_junction_without_denorm_still_works():
    """Regression check — pure junction without any denormalization."""
    join = _fsm("user_role", {"user_id", "role_id", "id"})
    user = _fsm("user", {"id", "email"})
    role = _fsm("role", {"id", "name"})
    fsms = [join, user, role]
    detect_denormalization_links(fsms)
    classify_entities(fsms)
    assert join.metadata["entity_kind"] == "junction"


def test_denorm_does_not_reclassify_transactional():
    """Order with denorm customer fields — still transactional, not junction."""
    order = _fsm("order", {"id", "status", "customer_id", "customer_name", "created_at"})
    customer = _fsm("customer", {"id", "name"})
    fsms = [order, customer]
    detect_denormalization_links(fsms)
    classify_entities(fsms)
    # Status + timestamps → transactional wins over any junction consideration.
    assert order.metadata["entity_kind"] == "transactional"


# --- edge cases -------------------------------------------------------------

def test_empty_fsm_list_no_error():
    out = detect_denormalization_links([])
    assert out == []


def test_self_entity_id_excluded():
    """Don't match an entity against itself even if stems collide (shouldn't happen
    post-consolidation, but be defensive)."""
    a = EntityStateMachine(
        entity="foo", entity_id="mod.foo", fields={"foo_bar", "bar"},
    )
    out = detect_denormalization_links([a])
    assert "denormalized_fields" not in a.metadata
