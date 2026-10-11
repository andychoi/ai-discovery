"""Benchmark harness (roadmap item M0.11): repo pins, metrics, sampler, scorer.

Network-free: the scan test uses the in-repo spring-boot fixture as a stand-in
source; pinned external repos are only checked for well-formedness here.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bench.__main__ import main as bench_main
from bench.precision.sampler import (
    COLUMNS,
    emitted_edges,
    stratified_sample,
    write_sheet,
)
from bench.precision.score import score_sheet, wilson
from bench.repos import ROOT, Repo, Source, load_repos
from bench.run_ai_discovery import run

FIXTURE = ROOT / "tests" / "fixtures" / "projects" / "spring-boot-app"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


# ── repos.toml ────────────────────────────────────────────────────────────

def test_repos_are_pinned_and_well_formed() -> None:
    repos = load_repos()
    assert {"spring-petclinic", "eshop", "microblog", "node-express-realworld"} <= set(repos)
    for r in repos.values():
        assert len(r.sha) == 40 and all(c in "0123456789abcdef" for c in r.sha), r.id
        assert r.url.startswith("https://") and r.status in {"active", "candidate"} and r.license
        if r.ground_truth:
            assert (ROOT / r.ground_truth).is_file() and r.ground_truth_sha, r.id


def test_ground_truth_pins_match_the_corpus_manifest() -> None:
    manifest = json.loads((ROOT / "tests/corpus/external/manifest.json").read_text())
    pins = {m["url"]: m["sha"] for m in manifest["repos"]}
    for r in load_repos().values():
        if r.url in pins:
            assert r.sha == pins[r.url] == r.ground_truth_sha, r.id


def test_bad_pins_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "repos.toml"
    bad.write_text('[[repo]]\nid="x"\nurl="https://e/x"\nsha="abc"\nstack="s"\nlicense="MIT"\nstatus="active"\n')
    with pytest.raises(ValueError, match="40-char"):
        load_repos(bad)


def test_dry_run_prints_a_plan_and_scans_nothing(capsys) -> None:
    assert bench_main(["run", "--repo", "spring-petclinic", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["repo"] == "spring-petclinic" and plan["sha"].startswith("753d35c")
    assert "--state-dir" in plan["command"] and "--no-prose" in plan["command"]


# ── scan + metrics (fixture stand-in) ─────────────────────────────────────

@pytest.fixture(scope="module")
def fixture_run(tmp_path_factory) -> tuple[dict, Path, Path]:
    repo_dir = tmp_path_factory.mktemp("bench") / "repo"
    shutil.copytree(FIXTURE, repo_dir)
    (repo_dir / "node_modules" / "x").mkdir(parents=True)
    (repo_dir / "node_modules" / "x" / "index.js").write_text("module.exports = 1\n")
    (repo_dir / "README.txt").write_text("hi\n")
    env = {**os.environ, **GIT_ENV}
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "f"]):
        subprocess.run(["git", *cmd], cwd=repo_dir, env=env, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True).stdout.strip()
    state = tmp_path_factory.mktemp("state")
    repo = Repo(id="fixture", url="https://example.invalid/x.git", sha=sha, stack="java-spring",
                license="MIT", status="candidate")
    result = run(repo, Source(repo_dir, "local"), keep_state=state)
    return result, repo_dir, state


def test_run_emits_the_protocol_metrics(fixture_run) -> None:
    result, repo_dir, _ = fixture_run
    assert result["ok"] and not result["void"] and result["cost_usd"] == 0
    assert result["files"]["indexed"] == 11
    assert result["files"]["skipped"]["skip_dir"] == 1                # node_modules/x/index.js
    assert result["files"]["skipped"]["unsupported_extension"] >= 1   # README.txt, .gitignore …
    assert result["nodes_by_kind"]["endpoint"] >= 1 and result["screens"] == 11
    assert result["edges_total"] == sum(result["edges_by_resolved_by"].values())
    assert result["unresolved"]["edges_without_target"] == result["edges_by_resolved_by"].get("unresolved", 0)
    assert result["ground_truth"] == {"status": "no_ground_truth"}
    assert not (repo_dir / ".discovery").exists()  # state went to --state-dir only


def test_ground_truth_is_not_scored_at_another_sha(fixture_run, tmp_path: Path) -> None:
    from bench.metrics import ground_truth_recall

    _, _, state = fixture_run
    conn = sqlite3.connect(state / "discovery.db")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps({"key_call_edges": [
        {"caller_suffix": "OrderController.create", "callee_suffix": "OrderService.createOrder"}]}))
    try:
        assert ground_truth_recall(conn, 1, gt, "a" * 40, "b" * 40)["status"] == "sha_mismatch"
        ok = ground_truth_recall(conn, 1, gt, "a" * 40, "a" * 40)
    finally:
        conn.close()
    assert ok["status"] == "ok" and ok["labelled"] == 1 and ok["call_edge_recall"] == 1.0


# ── precision sampler + scorer ────────────────────────────────────────────

def test_sample_is_seeded_stratified_and_capped(fixture_run) -> None:
    _, repo_dir, state = fixture_run
    edges = emitted_edges(state / "discovery.db", 1, repo_dir)
    a = stratified_sample(edges, seed=7, per_stratum=5)
    b = stratified_sample(list(reversed(edges)), seed=7, per_stratum=5)
    assert a == b  # independent of row order
    strata = {(e["language"], e["origin"]) for e in edges}
    counts = {s: sum(1 for e in a if (e["language"], e["origin"]) == s) for s in strata}
    assert all(c == min(5, sum(1 for e in edges if (e["language"], e["origin"]) == s)) for s, c in counts.items())
    assert all(not e["caller_file"].startswith("/") for e in a)  # repo-relative for graders
    assert stratified_sample(edges, seed=8, per_stratum=5) != a or len(edges) <= 5 * len(strata)


def test_graded_sheet_scores_per_stratum_with_ci(tmp_path: Path) -> None:
    sheet = tmp_path / "s.csv"
    rows = [{"language": "java", "origin": "short_name", "caller": f"c{i}"} for i in range(10)]
    rows += [{"language": "java", "origin": "receiver_type", "caller": "r"}]
    write_sheet(rows, sheet)
    graded = list(csv.DictReader(sheet.open()))
    for i, row in enumerate(graded[:10]):
        row["verdict"] = "correct" if i < 7 else ("wrong" if i < 9 else "unknown")
    with sheet.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(graded)
    res = {(s["language"], s["origin"]): s for s in score_sheet(sheet)["strata"]}
    sn = res[("java", "short_name")]
    assert (sn["graded"], sn["correct"], sn["precision"], sn["unknown"]) == (9, 7, 0.778, 1)
    assert sn["ci95"][0] < 0.778 < sn["ci95"][1]
    assert res[("java", "receiver_type")]["ungraded"] == 1 and res[("java", "receiver_type")]["precision"] is None


def test_bad_verdict_is_rejected(tmp_path: Path) -> None:
    sheet = tmp_path / "s.csv"
    write_sheet([{"language": "java", "origin": "x"}], sheet)
    sheet.write_text(sheet.read_text().replace(",,\n", ",maybe,\n"))
    with pytest.raises(ValueError, match="bad verdict"):
        score_sheet(sheet)


def test_wilson_interval() -> None:
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(27, 30)
    assert 0.73 < lo < 0.75 and 0.96 < hi < 0.98


def test_cli_entry_point_runs() -> None:
    out = subprocess.run([sys.executable, "-m", "bench", "list"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and "spring-petclinic" in out.stdout
