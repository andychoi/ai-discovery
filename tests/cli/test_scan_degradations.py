"""Stage degradations reach the scan end panel (roadmap item M0.13, audit #90)."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.cli.commands.scan import _summary
from ai_discovery.config import DiscoveryConfig
from ai_discovery.core.paths import ProjectLayout
from ai_discovery.db import get_conn, init_db
from ai_discovery.pipeline import _record_degradation


def test_recorded_degradations_are_reported(tmp_path: Path) -> None:
    layout = ProjectLayout.for_repo(tmp_path)
    layout.state_dir.mkdir(parents=True)
    init_db(layout.db_path)
    conn = get_conn(layout.db_path)
    conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (1, '2026-10-12', 'completed')")
    conn.commit()
    conn.close()
    _record_degradation(layout.db_path, 1, "tier1_summarize", "tier1_no_targets", 139)
    _record_degradation(layout.db_path, 1, "tier1_summarize", "tier1_failed", 3, ["a.f", "b.g"])
    out = _summary(layout, 1, DiscoveryConfig(), "completed")
    kinds = {d["kind"]: d for d in out["degradations"]}
    assert kinds["tier1_no_targets"]["count"] == 139 and "summarized nothing" in kinds["tier1_no_targets"]["message"]
    assert kinds["tier1_failed"]["message"].startswith("3 Tier-1 summaries failed")
