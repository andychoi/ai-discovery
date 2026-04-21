"""Phase 2.2: persist EntityStateMachine + StateTransition to SQLite.

The spec (Part 5, exit criterion 1) requires FSMs to survive the pipeline run
and be queryable externally — SQL joins on `state_transitions` unlock impact
analysis ("which scenarios touch Order.status?") without re-running the
pipeline.

Idempotency: both tables carry a `UNIQUE(scan_id, …)` constraint that matches
the rollup's dedup key, so re-running phase 8.7 replaces rather than
duplicates. Re-running is the expected behavior when the scan is resumed
after a later-phase failure.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..db import get_conn, retry_on_locked
from .models import EntityStateMachine


@retry_on_locked
def persist_entity_state_machines(
    db_path: Path,
    scan_id: int,
    fsms: list[EntityStateMachine],
) -> None:
    """Write the FSM artifact to SQLite.

    One row per FSM in `entity_state_machines`; one row per transition in
    `state_transitions`. Both use `INSERT OR REPLACE` so re-running the phase
    overwrites cleanly. `entry_points` and `metadata` serialize to JSON blobs
    — they're already-structured dicts the downstream consumer wants whole.
    """
    if not fsms:
        return

    conn = get_conn(db_path)
    try:
        # The rollup is the full picture for this scan; wipe prior rows so a
        # re-run with refined resolution doesn't leave stale transitions behind.
        # SQLite treats NULL as distinct in UNIQUE constraints, so we can't rely
        # on INSERT OR REPLACE for rows whose `from_state` / `field` are NULL.
        conn.execute("DELETE FROM state_transitions WHERE scan_id = ?", (scan_id,))
        conn.execute("DELETE FROM entity_state_machines WHERE scan_id = ?", (scan_id,))

        for fsm in fsms:
            conn.execute(
                """INSERT INTO entity_state_machines
                   (scan_id, entity, entity_id, states_json, fields_json,
                    source_files_json, confidence, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    fsm.entity,
                    fsm.entity_id or fsm.entity,
                    json.dumps(sorted(fsm.states)),
                    json.dumps(sorted(fsm.fields)),
                    json.dumps(sorted(fsm.source_files)),
                    fsm.confidence,
                    json.dumps(fsm.metadata) if fsm.metadata else None,
                ),
            )
            for t in fsm.transitions:
                conn.execute(
                    """INSERT INTO state_transitions
                       (scan_id, entity, entity_id, field, from_state, to_state,
                        trigger_function, guard_expr, confidence, entry_points_json,
                        metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scan_id,
                        t.entity,
                        t.entity_id or t.entity,
                        t.field,
                        t.from_state,
                        t.to_state,
                        t.trigger_function,
                        t.guard_expr,
                        t.confidence,
                        json.dumps(t.entry_points) if t.entry_points else None,
                        json.dumps(t.metadata) if t.metadata else None,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def load_entity_state_machines(db_path: Path, scan_id: int) -> list[dict]:
    """Read FSMs + their transitions back from SQLite.

    Returns a list of dicts with the same shape as `fsm_export.fsm_to_dict`
    (sans computed `round(...)` — stored values are authoritative). This is
    the external query surface: callers can build BPMN/DMN views without
    re-parsing.
    """
    conn = get_conn(db_path)
    try:
        fsm_rows = conn.execute(
            """SELECT entity, entity_id, states_json, fields_json, source_files_json,
                      confidence, metadata_json
               FROM entity_state_machines
               WHERE scan_id = ?
               ORDER BY entity_id, entity""",
            (scan_id,),
        ).fetchall()

        result: list[dict] = []
        for row in fsm_rows:
            trans_rows = conn.execute(
                """SELECT entity, entity_id, field, from_state, to_state, trigger_function,
                          guard_expr, confidence, entry_points_json, metadata_json
                   FROM state_transitions
                   WHERE scan_id = ? AND entity_id = ?
                   ORDER BY field, from_state, to_state, trigger_function""",
                (scan_id, row["entity_id"]),
            ).fetchall()
            result.append({
                "entity": row["entity"],
                "entity_id": row["entity_id"],
                "states": json.loads(row["states_json"] or "[]"),
                "fields": json.loads(row["fields_json"] or "[]"),
                "source_files": json.loads(row["source_files_json"] or "[]"),
                "confidence": row["confidence"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "transitions": [
                    {
                        "entity": t["entity"],
                        "entity_id": t["entity_id"],
                        "field": t["field"],
                        "from_state": t["from_state"],
                        "to_state": t["to_state"],
                        "trigger_function": t["trigger_function"],
                        "guard_expr": t["guard_expr"],
                        "confidence": t["confidence"],
                        "entry_points": json.loads(t["entry_points_json"] or "[]"),
                        "metadata": json.loads(t["metadata_json"] or "{}"),
                    }
                    for t in trans_rows
                ],
            })
        return result
    finally:
        conn.close()
