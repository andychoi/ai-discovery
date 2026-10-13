"""Migration 0003 (M1.12.a): edge_kind / evidence_kind on call_edges."""

import sqlite3

from ai_discovery.db import init_db
from ai_discovery.graph.origins import ORIGINS


def test_init_db_adds_edge_kind_columns_with_defaults(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    conn = sqlite3.connect(db)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(call_edges)")}
    assert cols["edge_kind"][4] == "'calls'" and cols["evidence_kind"][4] == "'static'"
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] >= 3


def test_origin_table_is_closed_and_consistent():
    from ai_discovery.graph.origins import EDGE_KINDS, EVIDENCE_KINDS

    for o in ORIGINS.values():
        assert 0 < o.confidence <= 1
        assert o.edge_kind in EDGE_KINDS and o.evidence_kind in EVIDENCE_KINDS
    assert ORIGINS["dispatch"].edge_kind == "dispatches_to"
    assert ORIGINS["framework"].edge_kind == "framework_binds"
