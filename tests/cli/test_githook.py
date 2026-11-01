"""Post-commit freshness hook (roadmap item M2.8)."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.core import githook
from ai_discovery.core.errors import DiscoveryError
from tests.golden.test_golden import _GIT_ENV, GOLDEN_REPOS, _build_repo

runner = CliRunner()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env={**os.environ, **_GIT_ENV}, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])


def _hook(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-commit"


def _cli(*args: str):
    return runner.invoke(app, ["--json", *args])


# ── install / chain / uninstall ──────────────────────────────────────────────

def test_githook_install_creates_executable_hook_and_uninstall_deletes_it(repo: Path) -> None:
    path, action = githook.install(repo)
    assert (path, action) == (_hook(repo), "created")
    text = path.read_text()
    assert text.startswith("#!/bin/sh\n") and text.count(githook.BEGIN) == 1
    assert os.access(path, os.X_OK)
    assert githook.status(repo).status == "installed"
    assert githook.install(repo)[1] == "updated" and path.read_text().count(githook.BEGIN) == 1
    assert githook.uninstall(repo) == (path, "deleted") and not path.exists()
    assert githook.status(repo).status == "missing"


def test_githook_chains_before_a_trailing_exit_and_uninstall_restores_the_original(repo: Path) -> None:
    original = "#!/bin/bash\necho existing hook\nexit 0\n"
    _hook(repo).write_text(original)
    _hook(repo).chmod(0o755)
    assert githook.install(repo)[1] == "appended"
    text = _hook(repo).read_text()
    assert text.index(githook.END) < text.index("exit 0") and text.startswith("#!/bin/bash\necho existing hook\n")
    assert githook.uninstall(repo)[1] == "removed"
    assert _hook(repo).read_text() == original


def test_githook_refuses_to_chain_into_a_non_shell_hook(repo: Path) -> None:
    _hook(repo).write_text("#!/usr/bin/env python3\nprint('hi')\n")
    with pytest.raises(DiscoveryError) as exc:
        githook.install(repo)
    assert exc.value.reason == "contradictory_flags" and "scan --from-hook" in exc.value.hint
    assert _hook(repo).read_text() == "#!/usr/bin/env python3\nprint('hi')\n"


def test_githook_status_reports_stale_blocks(repo: Path, tmp_path: Path) -> None:
    githook.install(repo, command=[str(tmp_path / "gone" / "discover")])
    assert githook.status(repo).status == "stale" and "no longer exists" in githook.status(repo).detail
    githook.install(repo)
    _hook(repo).write_text(_hook(repo).read_text().replace("hook-version: 1", "hook-version: 0"))
    assert githook.status(repo).status == "stale"
    githook.install(repo)
    _hook(repo).chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert "not executable" in githook.status(repo).detail
    assert githook.status(tmp_path).status == "not_git"


# ── init / doctor ─────────────────────────────────────────────────────────────

def test_githook_init_installs_lists_and_removes(repo: Path) -> None:
    res = _cli("-C", str(repo), "init", "--yes", "--git-hook")
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert any("post-commit" in w for w in out["written"])               # init's inventory lists the hook
    doctor = json.loads(_cli("-C", str(repo), "doctor").stdout)
    assert {c["name"]: c["status"] for c in doctor["checks"]}["git hook"] == "ok"
    # on a set-up project the flag alone adds or removes the hook (no --force needed)
    res = _cli("-C", str(repo), "init", "--no-git-hook")
    assert res.exit_code == 0 and json.loads(res.stdout)["removed"]
    assert not _hook(repo).exists()
    doctor = json.loads(_cli("-C", str(repo), "doctor").stdout)
    assert {c["name"]: c["status"] for c in doctor["checks"]}["git hook"] == "skip"


# ── the hook at commit time ─────────────────────────────────────────────────

def _scans(repo: Path) -> list[tuple]:
    conn = sqlite3.connect(repo / ".discovery" / "discovery.db")
    try:
        return conn.execute("SELECT commit_sha, status FROM scan_runs ORDER BY id").fetchall()
    finally:
        conn.close()


def test_githook_commit_starts_a_detached_scan_and_logs_it(repo: Path) -> None:
    assert _cli("-C", str(repo), "init", "--yes", "--git-hook").exit_code == 0
    assert _cli("-C", str(repo), "scan").exit_code == 0
    (repo / "src/main/java/com/example/service/OrderService.java").open("a").write("// touched\n")
    started = time.monotonic()
    _git(repo, "commit", "-qam", "edit")
    assert time.monotonic() - started < 10                                 # the commit does not wait for the scan
    head = _git(repo, "rev-parse", "HEAD").strip()
    log = repo / ".discovery" / "logs" / "hook.log"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not (log.exists() and '"trigger": "hook"' in log.read_text()):
        time.sleep(0.5)
    line = json.loads(log.read_text().strip().splitlines()[-1])
    assert line["ok"] and line["trigger"] == "hook" and line["prose"] is False
    assert (head, "completed") in _scans(repo)


def test_githook_skips_during_merge_and_never_fails(repo: Path) -> None:
    githook.install(repo, command=["/nonexistent/discover"])
    (repo / ".discovery").mkdir()
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
    res = subprocess.run(["sh", str(_hook(repo))], cwd=repo, capture_output=True, timeout=10)
    assert res.returncode == 0
    time.sleep(0.5)
    assert not (repo / ".discovery" / "logs" / "hook.log").exists()
    (repo / ".git" / "MERGE_HEAD").unlink()
    res = subprocess.run(["sh", str(_hook(repo))], cwd=repo, capture_output=True, timeout=10)
    assert res.returncode == 0                                              # a broken command never fails a commit


def test_githook_worktree_without_state_is_skipped(repo: Path, tmp_path: Path) -> None:
    githook.install(repo, command=["/nonexistent/discover"])
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert githook.hook_path(wt) == _hook(repo)                           # worktrees share the hook
    res = subprocess.run(["sh", str(_hook(repo))], cwd=wt, capture_output=True, timeout=10)
    assert res.returncode == 0 and not (wt / ".discovery").exists()


def test_githook_scan_from_hook_skips_when_the_lock_is_held(repo: Path) -> None:
    from ai_discovery.core.lock import RunLock
    from ai_discovery.core.paths import ProjectLayout

    layout = ProjectLayout.for_repo(repo).ensure()
    with RunLock(layout.lock_path, purpose="scan"):
        res = _cli("-C", str(repo), "scan", "--from-hook")
    assert res.exit_code == 0
    assert json.loads(res.stdout) | {"at": None} == {"ok": True, "status": "skipped", "reason": "lock_held",
                                                      "trigger": "hook", "at": None}


def test_githook_never_spends_without_config_prose_and_gate(repo: Path) -> None:
    res = _cli("-C", str(repo), "scan", "--from-hook", "--prose")
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["reason"] == "contradictory_flags"
    (repo / ".discovery").mkdir()
    (repo / ".discovery" / "config.yaml").write_text("prose: true\nprovider: bedrock\ncost_gate:\n  ask_above_usd: 0\n")
    res = _cli("-C", str(repo), "scan", "--from-hook")                 # config prose: the gate still applies
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["reason"] == "cost_gate_no_tty"
    conn = sqlite3.connect(repo / ".discovery" / "discovery.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM llm_costs").fetchone()[0] == 0
    finally:
        conn.close()
