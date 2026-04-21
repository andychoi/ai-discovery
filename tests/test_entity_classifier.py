"""Tests for Phase 2.5.2 entity-kind classifier."""

from __future__ import annotations

from ai_discovery.graph.entity_classifier import classify_entities
from ai_discovery.graph.models import EntityStateMachine, StateTransition


def _fsm(
    *,
    entity: str = "Thing",
    entity_id: str | None = None,
    transitions: list[StateTransition] | None = None,
    fields: set[str] | None = None,
    source_files: set[str] | None = None,
    metadata: dict | None = None,
) -> EntityStateMachine:
    return EntityStateMachine(
        entity=entity,
        entity_id=entity_id or f"mod.{entity}",
        transitions=transitions or [],
        states=set(),
        fields=fields or set(),
        source_files=source_files or set(),
        metadata=metadata or {},
    )


def _transition(from_state: str, to_state: str, field: str = "status") -> StateTransition:
    return StateTransition(
        entity="Order",
        entity_id="mod.Order",
        field=field,
        from_state=from_state,
        to_state=to_state,
        trigger_function="mod.transition",
    )


# --- staging ----------------------------------------------------------------

def test_staging_prefix_classified_as_staging():
    fsm = _fsm(
        entity="stg_orders",
        fields={"id", "status", "created_at", "updated_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "staging"
    assert out.metadata["entity_kind_signals"]["staging_name_prefix"] == "stg_"


def test_staging_suffix_classified_as_staging():
    fsm = _fsm(
        entity="customer_inbound",
        fields={"id", "payload", "received_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "staging"


def test_staging_with_delete_ops_higher_confidence():
    """Staging tables routinely cleared → raise confidence."""
    fsm = _fsm(
        entity="inbound_events",
        fields={"id", "payload"},
        metadata={"sql_ops": ["INSERT", "DELETE"]},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "staging"
    assert out.metadata["entity_kind_confidence"] == 0.95


def test_staging_columns_alone_classified_as_staging():
    """Processing-specific columns detect staging even without a name hint."""
    fsm = _fsm(
        entity="order_queue_records",
        fields={"id", "processing_status", "retry_count", "error_message"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "staging"


def test_staging_wins_over_transactional():
    """stg_* name + status + timestamps must classify as staging, not transactional."""
    fsm = _fsm(
        entity="staging_invoices",
        fields={"id", "status", "created_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "staging"


# --- summary ----------------------------------------------------------------

def test_sql_view_classified_as_summary():
    fsm = _fsm(
        entity="order_summary",
        fields={"id", "status", "total"},
        metadata={"node_type": "sql_view"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "summary"
    assert out.metadata["entity_kind_confidence"] == 0.95


def test_aggregate_columns_classified_as_summary():
    fsm = _fsm(
        entity="sales_rollup",
        fields={"region", "total_sales", "order_count"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "summary"
    assert "aggregate_columns" in out.metadata["entity_kind_signals"]


def test_aggregate_columns_but_with_status_not_summary():
    """A status field vetoes summary even if aggregate-looking columns exist."""
    fsm = _fsm(
        entity="pending_total",
        fields={"id", "status", "total_amount", "updated_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "transactional"


# --- junction ---------------------------------------------------------------

def test_two_fks_only_is_junction():
    fsm = _fsm(entity="order_item", fields={"order_id", "product_id"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "junction"


def test_fks_with_id_and_timestamps_still_junction():
    fsm = _fsm(
        entity="user_role",
        fields={"user_id", "role_id", "id", "created_at", "updated_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "junction"


def test_fks_plus_quantity_not_junction():
    """Extra business columns disqualify junction — this is a real entity."""
    fsm = _fsm(
        entity="cart_item",
        fields={"cart_id", "product_id", "quantity", "id"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] != "junction"


# --- config -----------------------------------------------------------------

def test_config_name_matches():
    fsm = _fsm(entity="app_settings", fields={"host", "port", "timeout"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "config"


def test_key_value_fields_are_config():
    fsm = _fsm(entity="preferences", fields={"key", "value"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "config"


# --- transactional ----------------------------------------------------------

def test_parsed_transitions_classified_transactional():
    fsm = _fsm(
        entity="Order",
        transitions=[
            _transition("draft", "submitted"),
            _transition("submitted", "approved"),
            _transition("approved", "shipped"),
        ],
        fields={"id", "status", "customer_id", "created_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "transactional"
    assert out.metadata["entity_kind_confidence"] == 0.9


def test_status_plus_timestamps_transactional_without_parsed_transitions():
    """SQL-only entity with lifecycle columns still classified correctly."""
    fsm = _fsm(
        entity="invoice",
        fields={"id", "status", "created_at", "updated_at"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "transactional"


def test_update_only_entity_is_transactional():
    """UPDATE-only is real — long-lived rows mutated in place. Must not fall
    to unknown or event just because INSERT isn't observed."""
    fsm = _fsm(
        entity="feature_flag",
        fields={"id", "status"},
        metadata={"sql_ops": ["UPDATE"]},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "transactional"
    assert out.metadata["entity_kind_signals"]["sql_update"] is True


def test_status_plus_timestamps_and_update_ops_highest_confidence():
    """Both signals present → stronger than either alone."""
    fsm = _fsm(
        entity="job",
        fields={"id", "status", "updated_at"},
        metadata={"sql_ops": ["UPDATE"]},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "transactional"
    assert out.metadata["entity_kind_confidence"] == 0.75


# --- event / audit ----------------------------------------------------------

def test_event_name_suffix():
    fsm = _fsm(entity="audit_log", fields={"id", "actor_id", "action", "logged_at"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "event"


def test_event_columns_without_status():
    fsm = _fsm(
        entity="activity",
        fields={"id", "event_type", "occurred_at", "actor_id"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "event"


def test_insert_only_sql_is_event():
    fsm = _fsm(
        entity="page_view",
        fields={"id", "url", "viewed_at"},
        metadata={"sql_ops": ["INSERT"]},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "event"


def test_insert_plus_update_not_event():
    """INSERT+UPDATE is not append-only — falls through event."""
    fsm = _fsm(
        entity="mystery",
        fields={"id", "value"},
        metadata={"sql_ops": ["INSERT", "UPDATE"]},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] != "event"


# --- key (seeded reference data) --------------------------------------------

def test_name_suffix_type_is_key():
    fsm = _fsm(entity="order_type", fields={"code", "name"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "key"
    assert out.metadata["entity_kind_signals"]["key_name_suffix"] is True


def test_name_suffix_status_is_key():
    fsm = _fsm(entity="order_status", fields={"code", "label"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "key"


def test_reference_columns_with_migration_source_is_key():
    """Country in a codebase where the only writer is a seed migration."""
    fsm = _fsm(
        entity="country",
        fields={"code", "name"},
        source_files={"db/migrations/0001_seed_countries.py", "src/models.py"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "key"
    assert "migration_sources" in out.metadata["entity_kind_signals"]


# --- master -----------------------------------------------------------------

def test_reference_columns_without_migration_is_master():
    """Same schema as a key table, but no seeding evidence → master."""
    fsm = _fsm(
        entity="vendor",
        fields={"code", "name"},
        source_files={"src/admin/vendors.py"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "master"


def test_label_only_is_master_lower_confidence():
    """Customer-style: has name but no code. Master at lower confidence."""
    fsm = _fsm(
        entity="customer",
        fields={"id", "name", "email", "phone"},
    )
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "master"
    assert out.metadata["entity_kind_confidence"] == 0.55


# --- unknown ----------------------------------------------------------------

def test_no_signals_is_unknown():
    fsm = _fsm(entity="thing", fields={"id", "blob"})
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "unknown"
    assert out.metadata["entity_kind_confidence"] == 0.4


def test_empty_fsm_is_unknown():
    fsm = _fsm(entity="x", fields=set())
    out = classify_entities([fsm])[0]
    assert out.metadata["entity_kind"] == "unknown"


# --- ordering / batch -------------------------------------------------------

def test_classify_multiple_and_preserve_existing_metadata():
    fsms = [
        _fsm(
            entity="Order",
            transitions=[_transition("draft", "submitted")],
            fields={"id", "status"},
            metadata={"discovered_by": "python_parser"},
        ),
        _fsm(entity="order_type", fields={"code", "name"}),
        _fsm(entity="order_summary", metadata={"node_type": "sql_view"}, fields={"id", "total"}),
    ]
    out = classify_entities(fsms)
    kinds = {f.entity: f.metadata["entity_kind"] for f in out}
    assert kinds == {
        "Order": "transactional",
        "order_type": "key",
        "order_summary": "summary",
    }
    # Existing metadata keys are preserved alongside the new annotations.
    assert out[0].metadata["discovered_by"] == "python_parser"
