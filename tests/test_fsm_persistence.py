"""Phase 2.2 tests: persist + load EntityStateMachine in SQLite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_discovery.db import get_conn, init_db, now_iso
from ai_discovery.graph.fsm_persistence import (
    load_entity_state_machines,
    persist_entity_state_machines,
)
from ai_discovery.graph.models import EntityStateMachine, StateTransition


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    # Seed a scan_run so the FK has a target
    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO scan_runs (id, started_at, status) VALUES (?, ?, ?)",
        (1, now_iso(), "running"),
    )
    conn.execute(
        "INSERT INTO scan_runs (id, started_at, status) VALUES (?, ?, ?)",
        (2, now_iso(), "running"),
    )
    conn.commit()
    conn.close()
    return db_path


def _t(**kw) -> StateTransition:
    defaults = dict(
        entity="Order", field="status", to_state="ACTIVE",
        trigger_function="svc.activate", confidence=1.0,
    )
    defaults.update(kw)
    return StateTransition(**defaults)


def _fsm(entity: str = "Order", **kw) -> EntityStateMachine:
    defaults = dict(
        states={"A", "B"}, fields={"status"}, source_files={"a.py"},
        transitions=[_t(entity=entity)],
    )
    defaults.update(kw)
    return EntityStateMachine(entity=entity, **defaults)


def test_persist_then_load_roundtrip(db: Path):
    persist_entity_state_machines(db, scan_id=1, fsms=[_fsm()])
    loaded = load_entity_state_machines(db, scan_id=1)
    assert len(loaded) == 1
    assert loaded[0]["entity"] == "Order"
    assert sorted(loaded[0]["states"]) == ["A", "B"]
    assert loaded[0]["fields"] == ["status"]
    assert loaded[0]["transitions"][0]["trigger_function"] == "svc.activate"


def test_entry_points_and_metadata_roundtrip(db: Path):
    eps = [{"kind": "API", "qualified_name": "C.approve",
            "confidence": 0.9, "hop_count": 2}]
    meta = {"resolved_by": "import_scope", "receiver": "OrderService"}
    fsm = _fsm(transitions=[_t(entry_points=eps, guard_expr="x > 5")])
    fsm.transitions[0].metadata = meta

    persist_entity_state_machines(db, scan_id=1, fsms=[fsm])
    loaded = load_entity_state_machines(db, scan_id=1)
    t = loaded[0]["transitions"][0]
    assert t["entry_points"] == eps
    assert t["metadata"] == meta
    assert t["guard_expr"] == "x > 5"


def test_rerun_is_idempotent(db: Path):
    """Re-running phase 8.7 must not duplicate rows (scan resume path)."""
    persist_entity_state_machines(db, scan_id=1, fsms=[_fsm()])
    persist_entity_state_machines(db, scan_id=1, fsms=[_fsm()])

    conn = get_conn(db)
    try:
        fsm_count = conn.execute(
            "SELECT COUNT(*) FROM entity_state_machines WHERE scan_id=1"
        ).fetchone()[0]
        trans_count = conn.execute(
            "SELECT COUNT(*) FROM state_transitions WHERE scan_id=1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert fsm_count == 1
    assert trans_count == 1


def test_different_scans_keep_separate_rows(db: Path):
    persist_entity_state_machines(db, scan_id=1, fsms=[_fsm("Order")])
    persist_entity_state_machines(db, scan_id=2, fsms=[_fsm("Order")])

    loaded_1 = load_entity_state_machines(db, scan_id=1)
    loaded_2 = load_entity_state_machines(db, scan_id=2)
    assert len(loaded_1) == 1
    assert len(loaded_2) == 1


def test_cascade_delete_on_scan_removes_fsms(db: Path):
    persist_entity_state_machines(db, scan_id=1, fsms=[_fsm()])

    conn = get_conn(db)
    try:
        conn.execute("DELETE FROM scan_runs WHERE id = 1")
        conn.commit()
        fsm_rows = conn.execute(
            "SELECT * FROM entity_state_machines WHERE scan_id = 1"
        ).fetchall()
        trans_rows = conn.execute(
            "SELECT * FROM state_transitions WHERE scan_id = 1"
        ).fetchall()
    finally:
        conn.close()
    assert fsm_rows == []
    assert trans_rows == []


def test_empty_fsm_list_is_noop(db: Path):
    persist_entity_state_machines(db, scan_id=1, fsms=[])
    loaded = load_entity_state_machines(db, scan_id=1)
    assert loaded == []


def test_multiple_transitions_per_entity(db: Path):
    fsm = EntityStateMachine(
        entity="Order",
        states={"CREATED", "APPROVED", "SHIPPED"},
        fields={"status"},
        transitions=[
            _t(to_state="CREATED", trigger_function="svc.create"),
            _t(to_state="APPROVED", trigger_function="svc.approve"),
            _t(to_state="SHIPPED", trigger_function="svc.ship"),
        ],
    )
    persist_entity_state_machines(db, scan_id=1, fsms=[fsm])
    loaded = load_entity_state_machines(db, scan_id=1)
    triggers = {t["trigger_function"] for t in loaded[0]["transitions"]}
    assert triggers == {"svc.create", "svc.approve", "svc.ship"}


def test_load_returns_entities_sorted(db: Path):
    fsms = [
        _fsm("Zeta"),
        _fsm("Alpha"),
        _fsm("Mu"),
    ]
    persist_entity_state_machines(db, scan_id=1, fsms=fsms)
    loaded = load_entity_state_machines(db, scan_id=1)
    assert [f["entity"] for f in loaded] == ["Alpha", "Mu", "Zeta"]


def test_rerun_replaces_not_duplicates_with_null_fields(db: Path):
    """SQLite treats NULL as distinct in UNIQUE — persistence must wipe first.

    The rollup layer is responsible for dedup. Persistence's contract is to
    give each scan exactly the FSMs it was handed on the latest call.
    """
    t_with_null_from = _t(from_state=None, to_state="APPROVED", trigger_function="a")
    fsm_v1 = EntityStateMachine(entity="Order", transitions=[t_with_null_from])
    persist_entity_state_machines(db, scan_id=1, fsms=[fsm_v1])
    persist_entity_state_machines(db, scan_id=1, fsms=[fsm_v1])

    conn = get_conn(db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM state_transitions WHERE scan_id=1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_source_files_serialize_as_sorted_list(db: Path):
    fsm = _fsm(source_files={"b.py", "a.py", "c.py"})
    persist_entity_state_machines(db, scan_id=1, fsms=[fsm])

    conn = get_conn(db)
    try:
        row = conn.execute(
            "SELECT source_files_json FROM entity_state_machines WHERE scan_id=1"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["source_files_json"]) == ["a.py", "b.py", "c.py"]
