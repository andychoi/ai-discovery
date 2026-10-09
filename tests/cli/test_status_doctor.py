"""`discover status` and `discover doctor [--repair]` (roadmap item M2.5)."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.config import DiscoveryConfig
from ai_discovery.core.paths import ProjectLayout
from ai_discovery.db import init_db
from tests.golden.test_golden import _GIT_ENV, GOLDEN_REPOS, _build_repo

runner = CliRunner()


def _json(result) -> dict:
    return json.loads(result.output.strip().splitlines()[-1])


def _scan_legacy(repo: Path) -> Path:
    """Keyless scan writing the legacy layout under <repo>/data/discovery-output."""
    from ai_discovery.pipeline import run_pipeline

    cfg = DiscoveryConfig(max_concurrent=1)
    cfg.no_llm = True
    run_pipeline(repo=str(repo), branch="main", project_slug="p",
                 output_dir=repo / "data" / "discovery-output", docs_root=repo / "data", config=cfg)
    return repo / "data" / "discovery-output" / "output-p" / "discovery-p.db"


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text("x\n", encoding="utf-8")
    env = {**os.environ, **_GIT_ENV}
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", name], cwd=repo, env=env, check=True)


@pytest.fixture
def scanned_repo(tmp_path: Path) -> Path:
    repo = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    (repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    _commit(repo, ".keep")
    _scan_legacy(repo)
    return repo


def _v1_index(repo: Path) -> ProjectLayout:
    layout = ProjectLayout.for_repo(repo).ensure()
    init_db(layout.db_path)
    return layout


def _write_lock(path: Path, **fields) -> None:
    info = {"pid": os.getpid(), "hostname": socket.gethostname(), "token": "t",
            "started_at": "2026-01-01T00:00:00+00:00", "purpose": "scan", "proc_start": None}
    info.update(fields)
    path.write_text(json.dumps(info), encoding="utf-8")


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ── status ────────────────────────────────────────────────────────────────

def test_status_not_indexed_exits_5(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = runner.invoke(app, ["--json", "-C", str(tmp_path), "status"])
    assert result.exit_code == 5
    assert _json(result)["error"]["reason"] == "not_indexed"


def test_status_reports_index_freshness_and_counts(scanned_repo: Path) -> None:
    result = runner.invoke(app, ["--json", "-C", str(scanned_repo), "status"])
    assert result.exit_code == 0, result.output
    res = _json(result)
    assert res["index"]["layout"] == "legacy" and res["run"]["status"] == "completed"
    assert res["counts"]["code_nodes"] > 0 and res["counts"]["call_edges"] > 0
    assert res["freshness"] == {**res["freshness"], "behind": 0, "dirty": False}
    assert res["costs"]["usd"] == 0 and res["lock"]["state"] == "none"


def test_status_tracks_new_commits_and_dirty_tree(scanned_repo: Path) -> None:
    _commit(scanned_repo, "a.txt")
    _commit(scanned_repo, "b.txt")
    (scanned_repo / "c.txt").write_text("uncommitted\n", encoding="utf-8")
    res = _json(runner.invoke(app, ["--json", "-C", str(scanned_repo), "status"]))
    assert res["freshness"]["behind"] == 2 and res["freshness"]["dirty"] is True


def test_status_human_output(scanned_repo: Path) -> None:
    result = runner.invoke(app, ["--no-color", "-C", str(scanned_repo), "status"])
    text = " ".join(result.output.split())
    assert result.exit_code == 0 and "up to date" in text and "nodes" in text


def test_status_prefers_v1_index(scanned_repo: Path) -> None:
    layout = _v1_index(scanned_repo)
    res = _json(runner.invoke(app, ["--json", "-C", str(scanned_repo), "status"]))
    assert res["index"] == {"db": str(layout.db_path), "layout": "v1"}


# ── doctor ────────────────────────────────────────────────────────────────

def _checks(result) -> dict[str, dict]:
    return {c["name"]: c for c in _json(result)["checks"]}


def test_doctor_json_lists_all_checks(scanned_repo: Path) -> None:
    result = runner.invoke(app, ["--json", "-C", str(scanned_repo), "doctor"])
    assert result.exit_code == 0, result.output
    checks = _checks(result)
    assert {"python", "git", "sqlite_fts5", "sqlite_vec", "grammars", "config",
            "index", "lock", "legacy_layout"} <= set(checks)
    assert checks["grammars"]["status"] == "ok" and checks["index"]["status"] == "ok"
    assert checks["legacy_layout"]["status"] == "warn"  # legacy tree detected, with advice


def test_doctor_repair_with_json_is_refused(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "-C", str(tmp_path), "doctor", "--repair"])
    assert result.exit_code == 2 and _json(result)["error"]["reason"] == "contradictory_flags"


def test_doctor_reports_bad_config_and_fails(tmp_path: Path) -> None:
    (tmp_path / "discovery.yaml").write_text("provder: openai\n", encoding="utf-8")
    result = runner.invoke(app, ["--json", "-C", str(tmp_path), "doctor"])
    assert result.exit_code == 1
    assert "did you mean 'provider'" in _checks(result)["config"]["detail"]


def test_doctor_repair_clears_stale_lock(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    layout = _v1_index(repo)
    _write_lock(layout.lock_path, pid=_dead_pid())
    assert _checks(runner.invoke(app, ["--json", "-C", str(repo), "doctor"]))["lock"]["status"] == "warn"

    result = runner.invoke(app, ["--no-color", "-C", str(repo), "doctor", "--repair"])
    assert "removed stale lock" in result.output
    assert not layout.lock_path.exists()


def test_doctor_repair_needs_yes_for_foreign_lock(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    layout = _v1_index(repo)
    _write_lock(layout.lock_path, hostname="build-agent-7")

    result = runner.invoke(app, ["--no-color", "-C", str(repo), "doctor", "--repair"])
    assert "re-run with --yes" in " ".join(result.output.split()) and layout.lock_path.exists()
    runner.invoke(app, ["-C", str(repo), "doctor", "--repair", "--yes"])
    assert not layout.lock_path.exists()


def test_doctor_never_touches_a_live_lock(tmp_path: Path) -> None:
    from ai_discovery.core.lock import RunLock

    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    layout = _v1_index(repo)
    with RunLock(layout.lock_path):
        runner.invoke(app, ["-C", str(repo), "doctor", "--repair", "--yes"])
        assert layout.lock_path.exists()


def test_doctor_repair_applies_pending_migrations(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    layout = _v1_index(repo)
    conn = sqlite3.connect(layout.db_path)
    conn.execute("DELETE FROM schema_migrations")
    conn.commit()
    conn.close()
    assert _checks(runner.invoke(app, ["--json", "-C", str(repo), "doctor"]))["index"]["status"] == "warn"

    runner.invoke(app, ["-C", str(repo), "doctor", "--repair"])
    after = _checks(runner.invoke(app, ["--json", "-C", str(repo), "doctor"]))["index"]
    assert after["status"] == "ok" and after["data"]["at"] == after["data"]["head"]


def test_doctor_llm_probes(tmp_path: Path) -> None:
    client = MagicMock()
    client.invoke.return_value = MagicMock(text="ok")
    client.get_embedding.side_effect = RuntimeError("no embedder")
    with patch("ai_discovery.ai.llm_client.LLMClient", return_value=client):
        checks = _checks(runner.invoke(app, ["--json", "-C", str(tmp_path), "doctor", "--llm"]))
    assert checks["llm_generation"]["status"] == "ok"
    assert checks["llm_embedding"]["status"] == "warn"
