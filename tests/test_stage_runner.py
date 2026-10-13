"""StageRunner: fingerprints, reuse/force decisions, --only, stage_runs rows, degradations (M1.8)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.core.paths import ProjectLayout
from ai_discovery.pipeline import run_pipeline
from ai_discovery.stages.context import RunContext
from ai_discovery.stages.runner import STAGES, StageRunner, fingerprint, normalize_only, selectable

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "projects" / "spring-boot-app"


def _ctx(**config) -> RunContext:
    cfg = DiscoveryConfig()
    for k, v in config.items():
        setattr(cfg, k, v)
    layout = ProjectLayout.for_repo(Path("/tmp/x"), state_dir=Path("/tmp/x/.state"))
    ctx = RunContext(repo="r", branch="main", config=cfg, layout=layout)
    ctx.resolved = SimpleNamespace(commit_sha="abc")
    return ctx


def _spec(name):
    return next(s for s in STAGES if s.name == name)


# ── fingerprints ──────────────────────────────────────────────────────────

def test_fingerprint_tracks_consumed_config_commit_and_upstream() -> None:
    base = fingerprint(_spec("summaries"), _ctx(), "up")
    assert fingerprint(_spec("summaries"), _ctx(), "up") == base                        # stable
    assert fingerprint(_spec("summaries"), _ctx(semantic_batching=False), "up") != base  # consumed key
    assert fingerprint(_spec("summaries"), _ctx(max_concurrent=99), "up") == base       # not consumed
    assert fingerprint(_spec("summaries"), _ctx(), "other") != base                     # upstream changed
    moved = _ctx()
    moved.resolved = SimpleNamespace(commit_sha="def")
    assert fingerprint(_spec("summaries"), moved, "up") != base                         # new commit


# ── decisions ─────────────────────────────────────────────────────────────

def test_reuse_when_fingerprint_matches_force_when_it_changed() -> None:
    runner = StageRunner(_ctx())
    parse = _spec("parse")
    assert runner.decide(parse, "fp1", {}) == "run"
    assert runner.decide(parse, "fp1", {"parse": ("fp1", "complete")}) == "reuse"
    assert runner.decide(parse, "fp2", {"parse": ("fp1", "complete")}) == "forced"
    assert runner.decide(parse, "fp1", {"parse": ("fp1", "failed")}) == "run"
    # ungated stages have no stored form: always run
    assert runner.decide(_spec("sql"), "fp1", {"sql": ("fp1", "complete")}) == "run"


def test_only_forces_named_stages_and_reuses_other_reloadable_ones() -> None:
    ctx = _ctx()
    ctx.only = {"parse"}
    runner = StageRunner(ctx)
    stored = {"graph": ("old", "complete")}
    assert runner.decide(_spec("parse"), "fp", stored) == "forced"
    assert runner.decide(_spec("graph"), "fp", stored) == "reuse"   # stored output, any fingerprint
    assert runner.decide(_spec("slices"), "fp", stored) == "run"    # nothing stored yet: must run
    assert runner.decide(_spec("sql"), "fp", stored) == "run"       # ungated: still runs


def test_only_on_a_new_scan_still_builds_everything(tmp_path: Path) -> None:
    status, layout = _scan(tmp_path, only=["parse"])
    conn = sqlite3.connect(layout.db_path)
    try:
        nodes = conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM call_edges").fetchone()[0]
    finally:
        conn.close()
    assert status == "completed" and nodes > 0 and edges > 0


def test_normalize_only() -> None:
    assert normalize_only(["parse", "domains", "domain_classify", "6"]) == {"parse", "graph"}
    with pytest.raises(ValueError, match="not a selectable stage"):
        normalize_only(["render"])
    assert "render" not in selectable() and "parse" in selectable()


def test_phase_gate_honours_runner_sets() -> None:
    ctx = _ctx()
    ctx.start_phase = 9.0                 # resume: phases < 9 are "done"
    assert ctx.phase_runs(6) is False
    ctx.forced_phases.add(6)
    assert ctx.phase_runs(6) is True
    ctx.suppressed_phases.add(11)
    assert ctx.phase_runs(11) is False


# ── end to end on the fixture (keyless) ───────────────────────────────────

def _scan(tmp_path: Path, **kw):
    repo = tmp_path / "repo"
    if not repo.exists():
        shutil.copytree(FIXTURE, repo)
    cfg = DiscoveryConfig()
    cfg.no_llm = True
    for k, v in kw.pop("config", {}).items():
        setattr(cfg, k, v)
    layout = ProjectLayout.for_repo(repo, state_dir=tmp_path / "state")
    status = run_pipeline(str(repo), "main", "app", tmp_path / "o", cfg, layout=layout, **kw)
    return status, layout


def _rows(layout) -> dict[str, tuple]:
    conn = sqlite3.connect(layout.db_path)
    try:
        return {r[0]: r[1:] for r in conn.execute(
            "SELECT stage, fingerprint, status, started_at FROM stage_runs WHERE scan_id = 1")}
    finally:
        conn.close()


def test_every_stage_gets_a_complete_row(tmp_path: Path) -> None:
    status, layout = _scan(tmp_path)
    rows = _rows(layout)
    assert status == "completed"
    assert set(rows) == {s.name for s in STAGES}
    assert all(status == "complete" and len(fp) == 16 for fp, status, _ in rows.values())


def test_resume_reuses_matching_stages_and_reruns_changed_ones(tmp_path: Path) -> None:
    _, layout = _scan(tmp_path)
    before = _rows(layout)
    conn = sqlite3.connect(layout.db_path)
    conn.execute("UPDATE scan_runs SET status = 'running'")
    conn.commit()
    conn.close()

    # same config → gated stages are reused (their rows are not rewritten)
    _scan(tmp_path, resume=True)
    after = _rows(layout)
    for spec in STAGES:
        if spec.phases:
            assert after[spec.name] == before[spec.name], spec.name
        else:
            assert after[spec.name][2] >= before[spec.name][2], spec.name   # ungated: re-run

    # a consumed config key changed → that stage and everything downstream re-runs
    conn = sqlite3.connect(layout.db_path)
    conn.execute("UPDATE scan_runs SET status = 'running'")
    conn.commit()
    conn.close()
    _scan(tmp_path, resume=True, config={"semantic_batching": False})
    changed = _rows(layout)
    assert changed["parse"] == after["parse"]                          # upstream untouched
    assert changed["summaries"][0] != after["summaries"][0]            # new fingerprint
    assert changed["flows"][0] != after["flows"][0]                    # propagated downstream


def test_parser_errors_are_recorded_as_degradations(tmp_path: Path, monkeypatch) -> None:
    from ai_discovery.parsers.java import JavaParser

    real = JavaParser.parse_file

    def flaky(self, path):
        if Path(path).name == "OrderService.java":
            raise RuntimeError("boom")
        return real(self, path)

    monkeypatch.setattr(JavaParser, "parse_file", flaky)
    _, layout = _scan(tmp_path)
    conn = sqlite3.connect(layout.db_path)
    try:
        rows = conn.execute("SELECT stage, kind, count, sample_json FROM degradations").fetchall()
    finally:
        conn.close()
    parse_rows = [r for r in rows if r[1] == "parser_error"]
    assert parse_rows and parse_rows[0][0] == "parse" and parse_rows[0][2] == 1
    assert "OrderService.java" in parse_rows[0][3]


def test_llm_truncations_are_recorded_at_run_end(tmp_path: Path) -> None:
    _, layout = _scan(tmp_path)
    ctx = RunContext(repo="r", branch="main", config=DiscoveryConfig(), layout=layout)
    ctx.scan_id = 1
    ctx.llm_client = SimpleNamespace(truncation_counts=lambda: {"tier1": 2, "tier2": 1})
    StageRunner(ctx, stages=())._summarise()
    conn = sqlite3.connect(layout.db_path)
    try:
        row = conn.execute("SELECT count FROM degradations WHERE kind = 'llm_truncated'").fetchone()
    finally:
        conn.close()
    assert row == (3,)
