"""0005 — cost_estimates: the cost gate's quote vs what the run spent (roadmap item M2.4).

One row per gated ``--prose`` scan: the estimate shown before any spend (expected, range, per-stage
breakdown and the repository shape it was priced from), the gate's decision, and — once the run ends —
the actual spend summed from ``llm_costs``. Used to recalibrate ``llm/estimate.py``.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cost_estimates (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id       INTEGER,
            created_at    TEXT NOT NULL,
            source        TEXT NOT NULL,
            decision      TEXT NOT NULL,
            expected_usd  REAL NOT NULL,
            low_usd       REAL NOT NULL,
            high_usd      REAL NOT NULL,
            stages_json   TEXT NOT NULL DEFAULT '[]',
            shape_json    TEXT NOT NULL DEFAULT '{}',
            actual_usd    REAL,
            actual_json   TEXT
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_estimates_scan ON cost_estimates(scan_id)")
