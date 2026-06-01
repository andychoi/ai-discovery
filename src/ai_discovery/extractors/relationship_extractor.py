"""Entity-relationship (foreign-key) extraction coordinator.

Phase 1 of FK-aware table docs (docs/specs/2026-05-31-fk-aware-table-docs.md).
Combines the two deterministic FK sources into one deduplicated edge list:

- SQL DDL ``FOREIGN KEY``/inline ``REFERENCES`` — `extract_sql_relationships`.
- JPA/EF association annotations the parsers stamp on db_model nodes under
  ``framework_hints["relationships"]`` (e.g. `@ManyToOne`+`@JoinColumn`).

Name-convention inference (`order_id` → `orders` with no declared FK) is
deliberately NOT done here: Phase 1 emits only declared, confidence-1.0 edges so
nothing fabricated reaches the schema docs. Inference is a later, clearly-flagged
addition (see the proposal's faithfulness section).
"""

from __future__ import annotations

from ..graph.models import CodeNode, EntityRelationship
from .sql_extractor import extract_sql_relationships


def _entity_name(node: CodeNode) -> str:
    """Bare entity name for a db_model node: @Table/[Table] name if present,
    otherwise the class name (schema/package stripped)."""
    hints = node.framework_hints or {}
    return str(hints.get("table") or node.name or "").split(".")[-1]


def extract_relationships(nodes: list[CodeNode]) -> list[EntityRelationship]:
    """Return deduplicated foreign-key edges across SQL + ORM sources."""
    # A foreign-key column references exactly one target, so an edge is
    # identified by (from_entity, from_field, to_entity). SQL and ORM often
    # describe the same edge with complementary detail (SQL knows the referenced
    # column; JPA knows the cardinality) — merge them into one richest edge
    # rather than emitting duplicates.
    by_key: dict[tuple[str, str, str], EntityRelationship] = {}
    order: list[tuple[str, str, str]] = []

    def _add(rel: EntityRelationship) -> None:
        if not rel.from_entity or not rel.to_entity:
            return
        k = (rel.from_entity, rel.from_field, rel.to_entity)
        existing = by_key.get(k)
        if existing is None:
            by_key[k] = rel
            order.append(k)
            return
        if not existing.to_field and rel.to_field:
            existing.to_field = rel.to_field
        if not existing.cardinality and rel.cardinality:
            existing.cardinality = rel.cardinality
        existing.inferred = existing.inferred and rel.inferred

    # 1. SQL-declared foreign keys.
    for rel in extract_sql_relationships(nodes):
        _add(rel)

    # Class-name → table-name map so an association's target (a class type like
    # `Customer`) resolves to the same table name (`customers`) the from-side
    # uses — keeping every edge table-to-table consistent for downstream joins.
    class_to_table: dict[str, str] = {}
    for node in nodes:
        if node.node_type == "db_model":
            cls = str(node.name or "").split(".")[-1]
            table = _entity_name(node)
            if cls and table:
                class_to_table[cls] = table

    # 2. ORM association annotations stamped by the parsers.
    for node in nodes:
        stamped = (node.framework_hints or {}).get("relationships") or []
        if not stamped:
            continue
        from_entity = _entity_name(node)
        source = {"java": "jpa", "csharp": "ef"}.get(node.language, node.language or "orm")
        for r in stamped:
            raw_to = str(r.get("to_entity", "")).split(".")[-1]
            _add(EntityRelationship(
                from_entity=from_entity,
                to_entity=class_to_table.get(raw_to, raw_to),
                from_field=str(r.get("from_field", "")),
                to_field=str(r.get("to_field", "")),
                cardinality=str(r.get("cardinality", "")),
                source=source,
                source_file=node.file_path,
                source_line=node.line_start,
                confidence=1.0,
                inferred=False,
            ))
    return [by_key[k] for k in order]
