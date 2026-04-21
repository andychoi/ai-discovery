"""Phase 2.5.2: classify each EntityStateMachine by its role in the system.

Every entity is annotated with:
  metadata["entity_kind"]: one of
      {key, master, transactional, event, summary, junction, config, unknown}
  metadata["entity_kind_confidence"]: float 0.0–1.0
  metadata["entity_kind_signals"]: dict of the positive signals that fired,
      preserved for explainability.

Why this matters — downstream generators consume the kind, not the raw FSM:
  - BPMN: only `transactional` entities get swim-lane activities; `key` /
    `master` appear as data objects, `summary` as report blocks.
  - DMN: rules key off transactional state; `key` values become enum inputs.
  - Impact analysis: `master` changes ripple at runtime; `key` changes require
    a deploy, so they rank higher on risk.

`key` vs `master` (the distinction the user called out): key data is seeded
from code (migrations, enums, fixtures) and changes require a deploy. Master
data is user-/interface-maintained at runtime. They look identical in schema,
so we lean on two signals: name suffix (`_type`, `_status`, `_category`) and
whether any `source_files` path looks like a migration/seed/fixture.

Implementation is a deterministic decision-tree cascade — no LLM. Cheap to
run, cheap to explain, cheap to tune.

Known blind spot — denormalization:
Real schemas often copy fields across entities for historical accuracy or
read-path performance. This breaks structural heuristics (a denormalized
junction like `order_item(order_id, product_id, product_name, product_price,
quantity)` no longer matches the "2+ FKs and nothing else" shape and falls
out of `junction`). Lifecycle heuristics (status, timestamps, transitions)
are unaffected — the cascade leans on those first, which makes it more
denorm-tolerant than a pure structural classifier would be. A proper fix
requires a cross-entity pass that detects field-name overlap across FSMs
and flags denormalized copies; that's Phase 3 work, sharing plumbing with
the cross-entity transition correlator.
"""

from __future__ import annotations

import re

from .models import EntityStateMachine


# Canonical lifecycle signal — presence marks an entity as *potentially*
# transactional even without parsed transitions.
_STATUS_FIELDS: frozenset[str] = frozenset({
    "status", "state", "workflow_state", "phase", "stage", "lifecycle_state",
})

# Append-only / audit signatures.
_EVENT_FIELDS: frozenset[str] = frozenset({
    "event_type", "logged_at", "occurred_at", "emitted_at", "recorded_at",
    "actor_id", "actor", "action",
})

# Reference / lookup morphology. Requires BOTH a code-like and a label-like
# field to count as reference — keeps "Customer {id, name, email}" from
# falsely matching.
_CODE_FIELDS: frozenset[str] = frozenset({
    "code", "key", "slug", "short_code", "abbreviation",
})
_LABEL_FIELDS: frozenset[str] = frozenset({
    "name", "label", "title", "display_name", "description",
})

# Aggregate column morphology — strong summary signal.
_AGG_PREFIXES: tuple[str, ...] = (
    "total_", "sum_", "count_", "avg_", "min_", "max_", "num_",
)
_AGG_SUFFIXES: tuple[str, ...] = (
    "_count", "_total", "_sum", "_avg", "_min", "_max", "_amount",
)

# Path fragments that indicate a source file is a migration / seed / fixture.
# The SQL extractor records the file where the SQL literal lived, so an
# INSERT inside `db/migrations/0001_seed_countries.py` shows up here.
_MIGRATION_PATH_HINTS: tuple[str, ...] = (
    "migration", "migrations/", "alembic/", "flyway/", "liquibase/",
    "/seed", "seeds/", "fixture", "/db/seeds", "seed_data",
)

# Entity-name suffixes that strongly suggest a key/code table.
_KEY_NAME_SUFFIXES: tuple[str, ...] = (
    "_type", "_status", "_kind", "_category", "_class",
)

# Append-only table name suffixes.
_EVENT_NAME_SUFFIXES: tuple[str, ...] = (
    "_log", "_logs", "_event", "_events", "_audit", "_history", "_journal",
)

# Config / settings name patterns.
_CONFIG_NAME_PAT: re.Pattern[str] = re.compile(
    r"(?:^|_)(config|settings?|preferences?|properties)(?:$|_)", re.I,
)

# Timestamp suffixes — second half of the column-only transactional signal.
_TIMESTAMP_SUFFIXES: tuple[str, ...] = ("_at", "_on", "_date", "_time")

# Fields a pure junction table may include in addition to its FKs.
_JUNCTION_ALLOWED_EXTRA: frozenset[str] = frozenset({
    "id", "uuid", "created_at", "updated_at",
})


def classify_entities(fsms: list[EntityStateMachine]) -> list[EntityStateMachine]:
    """Annotate each FSM's metadata with entity_kind / confidence / signals.

    Mutates in place and returns the same list for call-chaining.
    """
    for fsm in fsms:
        kind, confidence, signals = _classify_one(fsm)
        fsm.metadata["entity_kind"] = kind
        fsm.metadata["entity_kind_confidence"] = confidence
        fsm.metadata["entity_kind_signals"] = signals
    return fsms


def _classify_one(fsm: EntityStateMachine) -> tuple[str, float, dict]:
    fields_lc: set[str] = {f.lower() for f in fsm.fields}
    name_lc: str = (fsm.entity or "").lower()
    node_type: str = fsm.metadata.get("node_type") or ""
    sql_ops: set[str] = {op.upper() for op in fsm.metadata.get("sql_ops") or []}
    has_transitions: bool = len(fsm.transitions) > 0
    has_status_field: bool = bool(fields_lc & _STATUS_FIELDS)

    signals: dict[str, object] = {}

    # 1. summary — sql_view or aggregate columns without a status field.
    if node_type == "sql_view":
        signals["sql_view"] = True
        return "summary", 0.95, signals
    agg_cols = sorted(f for f in fields_lc if _is_aggregate_col(f))
    if agg_cols and not has_status_field:
        signals["aggregate_columns"] = agg_cols
        return "summary", 0.75, signals

    # 2. junction — two or more FKs and no other meaningful columns.
    #    Denormalized fields (copies from other entities, detected by
    #    `entity_correlator.detect_denormalization_links`) don't count
    #    as "other meaningful columns", so `order_item(order_id,
    #    product_id, product_name, product_price, quantity)` still
    #    classifies as junction when `product_name` / `product_price`
    #    are flagged copies from `Product`.
    denorm_fields = {
        (d.get("field") or "").lower()
        for d in fsm.metadata.get("denormalized_fields") or []
    }
    fk_cols = sorted(f for f in fields_lc if f.endswith("_id") and f != "id")
    if len(fk_cols) >= 2:
        non_fk_non_meta = (
            fields_lc
            - _JUNCTION_ALLOWED_EXTRA
            - set(fk_cols)
            - denorm_fields
        )
        if not non_fk_non_meta and not has_status_field:
            signals["foreign_keys"] = fk_cols
            if denorm_fields:
                signals["denormalized_fields"] = sorted(denorm_fields)
            return "junction", 0.85, signals

    # 3. config — name pattern or key/value field signature.
    if _CONFIG_NAME_PAT.search(name_lc):
        signals["config_name"] = name_lc
        return "config", 0.8, signals
    if fields_lc in ({"key", "value"}, {"name", "value"}, {"key", "value", "description"}):
        signals["key_value_pair"] = sorted(fields_lc)
        return "config", 0.8, signals

    # 4. transactional — parsed transitions (strong), or a status field
    #    combined with any lifecycle evidence (timestamps or UPDATE ops).
    #    UPDATE-only is real: singletons / long-lived aggregates are created
    #    elsewhere (seed, migration, upstream flow) and only mutated here.
    #    Treat UPDATE against a status field as a first-class transactional
    #    signal so those entities don't fall to `unknown`.
    if has_transitions:
        distinct_to_states = {t.to_state for t in fsm.transitions if t.to_state}
        if distinct_to_states:
            signals["transitions"] = len(fsm.transitions)
            signals["distinct_states"] = len(distinct_to_states)
            return "transactional", 0.9, signals
    timestamps = sorted(f for f in fields_lc if f.endswith(_TIMESTAMP_SUFFIXES))
    has_update_op = "UPDATE" in sql_ops
    if has_status_field and (timestamps or has_update_op):
        signals["status_field"] = sorted(fields_lc & _STATUS_FIELDS)
        if timestamps:
            signals["timestamp_fields"] = timestamps
        if has_update_op:
            signals["sql_update"] = True
        # Both signals present → the entity is almost certainly transactional.
        confidence = 0.75 if (timestamps and has_update_op) else 0.65
        return "transactional", confidence, signals

    # 5. event — append-only signatures.
    if any(name_lc.endswith(s) for s in _EVENT_NAME_SUFFIXES):
        signals["event_name_suffix"] = True
        return "event", 0.85, signals
    event_cols = fields_lc & _EVENT_FIELDS
    if event_cols and not has_status_field:
        signals["event_columns"] = sorted(event_cols)
        return "event", 0.8, signals
    if sql_ops and "INSERT" in sql_ops and "UPDATE" not in sql_ops and not has_transitions:
        signals["insert_only"] = sorted(sql_ops)
        return "event", 0.7, signals

    # 6. key — seeded reference data. Name suffix wins outright; migration-
    #    path evidence plus reference-like columns is the secondary signal.
    if any(name_lc.endswith(s) for s in _KEY_NAME_SUFFIXES):
        signals["key_name_suffix"] = True
        return "key", 0.8, signals
    is_reference_like = bool((fields_lc & _CODE_FIELDS) and (fields_lc & _LABEL_FIELDS))
    migration_files = sorted(p for p in fsm.source_files if _looks_like_migration(p))
    if is_reference_like and migration_files:
        signals["reference_columns"] = True
        signals["migration_sources"] = migration_files
        return "key", 0.75, signals

    # 7. master — reference-like or label-bearing, no lifecycle signals.
    if is_reference_like:
        signals["reference_columns"] = True
        return "master", 0.65, signals
    has_label = bool(fields_lc & _LABEL_FIELDS)
    if has_label and not has_status_field and not event_cols and not agg_cols:
        signals["label_fields"] = sorted(fields_lc & _LABEL_FIELDS)
        return "master", 0.55, signals

    # 8. unknown — no signature matched. Record why so it's easy to improve.
    signals["reason"] = "no_matching_signature"
    return "unknown", 0.4, signals


def _is_aggregate_col(field: str) -> bool:
    return field.startswith(_AGG_PREFIXES) or field.endswith(_AGG_SUFFIXES)


def _looks_like_migration(path: str) -> bool:
    p = path.lower()
    return any(h in p for h in _MIGRATION_PATH_HINTS)
