"""`discover scan` v1 (roadmap item M2.3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.cli.commands.scan import _only_to_skip
from ai_discovery.core import hooks
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])


def _scan(repo: Path, *args: str, json_out: bool = True):
    pre = ["--json"] if json_out else []
    return runner.invoke(app, [*pre, "-C", str(repo), "scan", *args])


def _result(res) -> dict:
    return json.loads(res.stdout)  # the whole stdout must be ONE JSON document


# ── default keyless scan into .discovery/ ────────────────────────────────

def test_keyless_scan_writes_v1_layout(repo: Path) -> None:
    res = _scan(repo)
    assert res.exit_code == 0, res.output
    out = _result(res)
    assert out["ok"] and out["status"] == "completed" and out["prose"] is False
    assert (repo / ".discovery" / "discovery.db").exists()
    assert (repo / ".discovery" / ".gitignore").exists()
    c = out["counts"]
    assert c["files"] > 0 and c["symbols"] > 0 and c["edges"] > 0 and c["screens"] > 0
    assert c["docs"] == 0 and out["spend"]["usd"] == 0  # no LLM in keyless mode
    assert out["next"][0] == "discover scan --prose" and len(out["next"]) <= 2
    conn = sqlite3.connect(repo / ".discovery" / "discovery.db")
    assert conn.execute("SELECT scan_id FROM current_scan").fetchone()[0] == out["scan_id"]


def test_rescan_same_commit_is_unchanged_and_full_rescans(repo: Path) -> None:
    first = _result(_scan(repo))
    again = _result(_scan(repo))
    assert again["status"] == "unchanged" and again["scan_id"] == first["scan_id"]
    full = _result(_scan(repo, "--full"))
    assert full["status"] == "completed" and full["scan_id"] != first["scan_id"]


def test_state_dir_keeps_repo_clean(repo: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    res = runner.invoke(app, ["--json", "-C", str(repo), "--state-dir", str(state), "scan"])
    assert res.exit_code == 0, res.output
    assert (state / "discovery.db").exists() and not (repo / ".discovery").exists()


def test_status_sees_the_v1_scan(repo: Path) -> None:
    scanned = _result(_scan(repo))
    status = json.loads(runner.invoke(app, ["--json", "-C", str(repo), "status"]).stdout)
    assert status["index"]["layout"] == "v1" and status["run"]["id"] == scanned["scan_id"]


# ── output modes ──────────────────────────────────────────────────────────

def test_progress_json_is_ndjson(repo: Path) -> None:
    res = runner.invoke(app, ["--progress", "json", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    events = [json.loads(line) for line in res.stdout.splitlines() if line.startswith("{")]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "stage" and kinds[-1] == "done" and events[-1]["outcome"] == "completed"


def test_quiet_human_output_has_end_panel(repo: Path) -> None:
    res = runner.invoke(app, ["-q", "--no-color", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    text = " ".join(res.stdout.split())
    assert "scan completed" in text and "What's next:" in text and "discover scan --prose" in text
    assert "Phase 6" not in res.stdout  # pipeline chatter silenced by -q


# ── flags ─────────────────────────────────────────────────────────────────

def test_only_translates_to_skip_list() -> None:
    from ai_discovery.pipeline import _SKIPPABLE_PHASES

    skip = _only_to_skip("parse,domain_classify")
    assert "6" not in skip and "7" not in skip
    assert len(skip) == len(_SKIPPABLE_PHASES) - 2


@pytest.mark.parametrize("args", [["--only", "render_markdown"], ["--only", "nope"], ["--quality", "best"]])
def test_bad_flags_exit_2(repo: Path, args) -> None:
    res = _scan(repo, *args)
    assert res.exit_code == 2 and _result(res)["error"]["reason"] == "contradictory_flags"


def test_prose_and_budget_and_quality_reach_pipeline(repo: Path) -> None:
    seen = {}

    def fake(**kwargs):
        seen.update(cfg=kwargs["config"], layout=kwargs["layout"], rescan=kwargs["rescan"])
        return "completed"

    with patch("ai_discovery.pipeline.run_pipeline", side_effect=fake):
        assert _scan(repo, "--prose", "--budget", "4", "--quality", "final", "--full").exit_code == 0
    cfg = seen["cfg"]
    assert (cfg.no_llm, cfg.budget_limit_usd, cfg.prod, seen["rescan"]) == (False, 4.0, True, True)
    assert seen["layout"].state_dir == repo.resolve() / ".discovery"


def test_config_prose_is_the_default(repo: Path) -> None:
    (repo / ".discovery").mkdir()
    (repo / ".discovery" / "config.yaml").write_text("prose: true\n")
    with patch("ai_discovery.pipeline.run_pipeline", return_value="completed") as run:
        _scan(repo)
    assert run.call_args.kwargs["config"].no_llm is False


def test_budget_exceeded_exits_3(repo: Path) -> None:
    with patch("ai_discovery.pipeline.run_pipeline", return_value="budget_exceeded"):
        res = _scan(repo, "--prose")
    assert res.exit_code == 3 and _result(res)["error"]["reason"] == "budget_exceeded"


# ── post-scan hooks ───────────────────────────────────────────────────────

def test_post_scan_hooks_run_and_failures_degrade(repo: Path) -> None:
    calls = []
    hooks.register_post_scan("test-ok", lambda ctx: calls.append((ctx.scan_id, ctx.status)) or "built 0 pages")
    hooks.register_post_scan("test-bad", lambda ctx: 1 / 0)
    try:
        res = _scan(repo)
    finally:
        hooks.unregister_post_scan("test-ok")
        hooks.unregister_post_scan("test-bad")
    assert res.exit_code == 0, res.output
    out = _result(res)
    assert calls and calls[0][1] == "completed"
    assert {h["name"]: h["ok"] for h in out["hooks"]} == {"test-ok": True, "test-bad": False}
    assert any(d["kind"] == "post_scan_hook" for d in out["degradations"])
    assert "discover doctor" in out["next"]


def test_hook_registry_rejects_duplicates() -> None:
    hooks.register_post_scan("dup", lambda ctx: None)
    try:
        with pytest.raises(ValueError):
            hooks.register_post_scan("dup", lambda ctx: None)
    finally:
        hooks.unregister_post_scan("dup")
