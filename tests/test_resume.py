"""Resume correctness (roadmap item M0.6)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.pipeline import _iter_with_deadline, _repo_keys
from tests.golden.stub_llm import StubLLM
from tests.golden.test_golden import _GIT_ENV, GOLDEN_REPOS, _build_repo

SLUG = "resume"


def _run(repo: str, out: Path, **kwargs):
    from ai_discovery.ai.self_review import ReviewClaim

    stub = kwargs.pop("stub", None) or StubLLM()
    with (
        patch("ai_discovery.ai.llm_client.LLMClient", return_value=stub),
        patch("ai_discovery.rag.embedder.embed_chunks", return_value={"embedded": 0, "dim": 256}),
        patch("ai_discovery.rag.retriever.search", return_value=[]),
        patch("ai_discovery.ai.self_review.review_document",
              return_value=[ReviewClaim(claim_text="c", status="verified")]),
        patch("ai_discovery.ai.self_review.persist_claims"),
    ):
        from ai_discovery.pipeline import run_pipeline

        return run_pipeline(
            repo=repo, branch="main", project_slug=SLUG, output_dir=out / "out",
            docs_root=out / "docs",
            config=DiscoveryConfig(provider="bedrock", budget_limit_usd=100.0, max_concurrent=1),
            **kwargs,
        )


def _scan_rows(out: Path) -> list[tuple]:
    conn = sqlite3.connect(out / "out" / f"output-{SLUG}" / f"discovery-{SLUG}.db")
    try:
        return conn.execute("SELECT id, status, commit_sha FROM scan_runs ORDER BY id").fetchall()
    finally:
        conn.close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _build_repo(tmp_path / "work" / "repo", GOLDEN_REPOS["java-spring-jsp"])


# ── 1. canonical repo matching ────────────────────────────────────────────

def test_repo_keys_cover_relative_and_absolute(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "r").mkdir()
    monkeypatch.chdir(tmp_path)
    assert set(_repo_keys("r")) & set(_repo_keys(str(tmp_path / "r")))
    assert _repo_keys("https://example.com/a.git") == ["https://example.com/a.git"]


def test_relative_then_absolute_path_is_same_scan(repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo.parent)
    _run("./repo", tmp_path)
    _run(str(repo), tmp_path)  # same commit: must hit "already scanned", not rescan
    rows = _scan_rows(tmp_path)
    assert len(rows) == 1 and rows[0][1] == "completed"


# ── 2. refuse resume across commits ───────────────────────────────────────

def test_resume_refused_when_head_moved(repo: Path, tmp_path: Path) -> None:
    _run(str(repo), tmp_path)
    (repo / "NEW.md").write_text("change\n", encoding="utf-8")
    env = {**os.environ, **_GIT_ENV}
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, env=env, check=True)
    assert _run(str(repo), tmp_path, resume_from="14") == "resume_refused"
    assert len(_scan_rows(tmp_path)) == 1  # nothing new created


def test_cli_exit_code_for_resume_refused(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ai_discovery.cli import app

    with patch("ai_discovery.pipeline.run_pipeline", return_value="resume_refused"):
        result = CliRunner().invoke(app, ["scan", str(tmp_path), "-p", "x", "-o", str(tmp_path / "o")])
    assert result.exit_code == 2


# ── 3. phase 13 rehydrates without the LLM ────────────────────────────────

def test_resume_past_phase_13_loads_flows_from_db(repo: Path, tmp_path: Path) -> None:
    _run(str(repo), tmp_path)
    from ai_discovery.ai.flow_analyzer import ScenarioFlowInference

    with patch.object(ScenarioFlowInference, "infer_flow",
                      side_effect=AssertionError("phase 13 re-ran the LLM on resume")) as infer:
        status = _run(str(repo), tmp_path, resume_from="14")
    assert status == "completed"
    infer.assert_not_called()
    conn = sqlite3.connect(tmp_path / "out" / f"output-{SLUG}" / f"discovery-{SLUG}.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM scenario_flows").fetchone()[0] > 0
    finally:
        conn.close()


# ── 4. --skip-phases rejects ungated phases ───────────────────────────────

@pytest.mark.parametrize("spec", ["14", "16", "self_review", "19"])
def test_skip_phases_rejects_ungated_phases(spec: str, repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be skipped"):
        _run(str(repo), tmp_path, skip_phases=[spec])


def test_skip_phases_accepts_gated_phase(repo: Path, tmp_path: Path) -> None:
    assert _run(str(repo), tmp_path, skip_phases=["10"]) == "completed"


# ── 5. per-item deadline actually fires ───────────────────────────────────

def test_iter_with_deadline_expires_stuck_item() -> None:
    started: dict[int, float] = {}

    def work(i: int, secs: float) -> int:
        started[i] = time.monotonic()
        time.sleep(secs)
        return i

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(work, 0, 0.0): 0, pool.submit(work, 1, 3.0): 1}
        t0 = time.monotonic()
        results = {futures[f]: expired for f, expired in
                   _iter_with_deadline(futures, lambda f: started.get(futures[f]), 0.3, poll=0.05)}
        elapsed = time.monotonic() - t0
    assert results == {0: False, 1: True}
    assert elapsed < 2.0, "deadline did not fire; waited for the stuck item"
