"""Agent-loop eval harness (roadmap item M5.7.a) — everything except spending on model runs."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from bench.agent import arms, runner, score
from bench.agent import tasks as T
from bench.agent.build_tasks import leaks
from bench.agent.metrics import file_scores, parse_claude_stream


def test_task_set_shape() -> None:
    repos, tasks = T.load()
    assert len(tasks) >= 15 and len({t.stack for t in tasks}) >= 3
    assert len({t.id for t in tasks}) == len(tasks)
    for t in tasks:
        assert 1 <= len(t.gold_files) <= 8 and re.fullmatch(r"[0-9a-f]{40}", t.base) is not None
        assert not any(leaks(t.prompt, f) for f in (*t.gold_files, *t.gold_test_files)), t.id


def test_task_set_matches_git_history_when_available() -> None:
    repos, tasks = T.load()
    if any(T.source_repo(r) is None for r in repos.values()):
        pytest.skip("full-history checkouts not present (run `python -m bench.agent fetch`)")
    assert T.validate(repos, tasks) == []


def test_prereg_block_and_bands_in_sync() -> None:
    cfg = runner.load_prereg()
    assert cfg.status in ("Draft", "Frozen") and cfg.reps >= 1 and set(cfg.arms) <= set(arms.ARMS)
    assert {"bare", "discover"} <= set(cfg.arms) and cfg.budget_total_usd > 0
    page = runner.PREREG.read_text()
    assert "**+0.10**" in page and score.BANDS["H1_recall_gain_min"] == 0.10
    assert "**−0.05**" in page and score.BANDS["H2_precision_loss_max"] == 0.05
    assert "**−0.10**" in page and score.BANDS["H2_ci_floor"] == -0.10
    assert "**1.25**" in page and score.BANDS["S1_output_token_ratio_max"] == 1.25
    assert "**0.80**" in page and score.BANDS["S3_tests_ran_min"] == 0.80


def test_plan_is_deterministic_and_complete() -> None:
    cfg = runner.load_prereg()
    _, tasks = T.load()
    a, b = runner.plan(cfg, tasks), runner.plan(cfg, tasks)
    assert a == b and len(a) == len(tasks) * len(cfg.arms) * cfg.reps
    assert len({c["run_id"] for c in a}) == len(a)
    assert [c["order"] for c in a] == list(range(1, len(a) + 1))


def test_run_refuses_without_frozen_prereg_and_confirmation(monkeypatch) -> None:
    cfg = runner.load_prereg()
    monkeypatch.setattr(runner, "frozen", lambda path=runner.PREREG: (False, "status is 'Draft'"))
    with pytest.raises(SystemExit, match="refusing to run: status is 'Draft'"):
        runner.run_all(cfg, confirm_spend=True)
    monkeypatch.setattr(runner, "frozen", lambda path=runner.PREREG: (True, "abc123"))
    with pytest.raises(SystemExit, match="--confirm-spend"):
        runner.run_all(cfg, confirm_spend=False)


def _repo_with_history(tmp_path: Path) -> tuple[T.RepoSpec, T.Task]:
    src = tmp_path / "src"
    src.mkdir()
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    (src / "a.js").write_text("module.exports = 1;\n")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run([*g, "commit", "-qm", "base"], cwd=src, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    (src / "secret_answer.js").write_text("module.exports = 'gold';\n")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run([*g, "commit", "-qm", "the answer"], cwd=src, check=True)
    gold = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    spec = T.RepoSpec("r", "node", "https://example.invalid/r.git", str(src))
    task = T.Task("t", "r", "node", base, gold, "do it", "the answer", ("secret_answer.js",), ())
    return spec, task


def test_workspace_has_no_history_and_tracks_changes(tmp_path) -> None:
    spec, task = _repo_with_history(tmp_path)
    ws = T.make_workspace(spec, task, tmp_path / "ws")
    log = subprocess.run(["git", "log", "--all", "--oneline"], cwd=ws, capture_output=True, text=True).stdout
    assert log.strip().endswith("base") and len(log.splitlines()) == 1     # the answer commit is not reachable
    assert not (ws / "secret_answer.js").exists()
    (ws / "b.js").write_text("x")
    (ws / ".discovery").mkdir()
    (ws / ".discovery" / "db").write_text("x")
    (ws / "CLAUDE.md").write_text("x")
    assert T.changed_files(ws) == ["b.js"]


def test_arms_differ_only_in_what_the_agent_gets(tmp_path) -> None:
    bare, _ = arms.command(arms.ARMS["bare"], prompt="P", ws=tmp_path, model="m", budget_usd=2, scratch=tmp_path / "a")
    disc, _ = arms.command(arms.ARMS["discover"], prompt="P", ws=tmp_path, model="m", budget_usd=2,
                           scratch=tmp_path / "b")
    auto, _ = arms.command(arms.ARMS["discover-auto"], prompt="P", ws=tmp_path, model="m", budget_usd=2,
                           scratch=tmp_path / "c")
    assert bare[2] == disc[2] == auto[2]                                  # byte-identical prompt
    for argv in (bare, disc, auto):
        assert {"--strict-mcp-config", "--no-session-persistence"} <= set(argv)
        assert argv[argv.index("--max-budget-usd") + 1] == "2.00"
    assert json.loads((tmp_path / "a" / "mcp.json").read_text()) == {"mcpServers": {}}
    assert "ai-discovery" in json.loads((tmp_path / "b" / "mcp.json").read_text())["mcpServers"]
    assert "--plugin-dir" not in bare and "--plugin-dir" in disc
    assert "--append-system-prompt" in auto and "--append-system-prompt" not in disc


def test_metrics() -> None:
    stream = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__ai-discovery__get_impact", "input": {}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "./mvnw -q test -Dtest=X"}}]}}),
        "not json", json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.5,
                                "num_turns": 3, "usage": {"output_tokens": 900}})]
    m = parse_claude_stream(stream)
    assert (m["finished"], m["output_tokens"], m["tool_calls"], m["discover_tool_calls"], m["ran_tests"]) == \
        (True, 900, 2, 1, True)
    assert parse_claude_stream([])["finished"] is False
    fs = file_scores(["src/A.java", "src/ATest.java", "README.md"], ["src/A.java", "src/B.java"], "java")
    assert fs["recall"] == 0.5 and fs["precision"] == 1.0 and fs["missed"] == ["src/B.java"]


def _run(task, arm, rep, recall, precision, tokens, ran_tests=False, stack="java"):
    return {"task": task, "arm": arm, "rep": rep, "stack": stack,
            "files": {"recall": recall, "precision": precision},
            "agent": {"output_tokens": tokens, "tool_calls": 5, "cost_usd": 0.3, "ran_tests": ran_tests,
                      "finished": True},
            "build": {"status": "pass"}, "test": {"status": "not_applicable"}}


def test_verdict_pass_and_fail() -> None:
    win = [r for i in range(15) for r in (_run(f"t{i}", "bare", 1, 0.5, 0.8, 1000),
                                          _run(f"t{i}", "discover", 1, 0.8, 0.8, 1100),
                                          _run(f"t{i}", "discover-auto", 1, 0.8, 0.8, 1300, ran_tests=True))]
    res = score.verdict(win, resamples=2000, seed=1)
    assert res["ga"] is True and res["hypotheses"]["H1"]["mean_diff"] == 0.3
    assert res["hypotheses"]["S1"]["pass"] and res["hypotheses"]["S3"]["tests_ran_share"] == 1.0
    lose = [r for i in range(15) for r in (_run(f"t{i}", "bare", 1, 0.8, 0.9, 1000),
                                           _run(f"t{i}", "discover", 1, 0.8, 0.6, 3000))]
    res = score.verdict(lose, resamples=2000, seed=1)
    assert res["ga"] is False and not res["hypotheses"]["H1"]["pass"] and not res["hypotheses"]["H2"]["pass"]
    assert not res["hypotheses"]["S1"]["pass"]
    assert score.bootstrap_ci([0.1, 0.2, 0.3], resamples=500, seed=3) == \
        score.bootstrap_ci([0.1, 0.2, 0.3], resamples=500, seed=3)


def test_report_with_no_runs(capsys) -> None:
    from bench.agent.__main__ import main

    assert main(["report"]) == 0
    out = capsys.readouterr().out
    assert "No runs yet" in out and "runs pending" in out
