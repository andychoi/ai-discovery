"""`discover scan --watch` and dirty-working-tree rescans (roadmap item M2.8)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

runner = CliRunner()
SERVICE = "src/main/java/com/example/service/OrderService.java"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])


def _rows(repo: Path, sql: str) -> list:
    db = repo / ".discovery" / "discovery.db"
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _scan(repo: Path) -> dict:
    res = runner.invoke(app, ["--json", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def _edit_when_first_scan_done(repo: Path, text: str) -> threading.Thread:
    def go() -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not _rows(repo, "SELECT 1 FROM scan_runs WHERE status = 'completed'"):
            time.sleep(0.1)
        time.sleep(0.3)
        with (repo / SERVICE).open("a") as fh:
            fh.write(text)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


# ── dirty working tree ────────────────────────────────────────────────────────

def test_watch_dirty_tree_rescans_and_revert_rescans_again(repo: Path) -> None:
    assert _scan(repo)["status"] == "completed"
    assert _scan(repo)["status"] == "unchanged"                       # clean and already scanned
    original = (repo / SERVICE).read_text()
    (repo / SERVICE).write_text(original + "// uncommitted\n")
    assert _scan(repo)["status"] == "completed"                       # HEAD did not move, the code did
    assert _scan(repo)["status"] == "unchanged"                       # same dirty state: nothing to do
    (repo / SERVICE).write_text(original)
    assert _scan(repo)["status"] == "completed"                       # reverting is a change too
    worktrees = [r[0] for r in _rows(repo, "SELECT worktree FROM scan_runs ORDER BY id")]
    assert worktrees[0] == "" and worktrees[1] != "" and worktrees[2] == ""
    stamps = {json.loads(r[0])["dirty"] for r in _rows(
        repo, "SELECT stamp_json FROM pages WHERE scan_id = (SELECT MAX(id) FROM scan_runs) LIMIT 5")}
    assert stamps == {False}


def test_watch_pages_of_a_dirty_scan_say_so(repo: Path) -> None:
    _scan(repo)
    (repo / SERVICE).write_text((repo / SERVICE).read_text() + "// uncommitted\n")
    _scan(repo)
    stamps = {json.loads(r[0])["dirty"] for r in _rows(
        repo, "SELECT stamp_json FROM pages WHERE scan_id = (SELECT scan_id FROM current_scan)")}
    assert stamps == {True}


# ── --watch ───────────────────────────────────────────────────────────────────

def test_watch_rescans_after_a_change(repo: Path) -> None:
    edit = _edit_when_first_scan_done(repo, "// edited while watching\n")
    res = runner.invoke(app, ["--json", "-C", str(repo), "scan", "--watch", "--debounce", "0.3",
                              "--watch-cycles", "1"])
    edit.join(timeout=5)
    assert res.exit_code == 0, res.output
    lines = [json.loads(x) for x in res.stdout.strip().splitlines()]
    assert [x["trigger"] for x in lines] == ["scan", "watch"]
    assert lines[1]["status"] == "completed" and lines[1]["changed"] == [SERVICE]
    assert len(_rows(repo, "SELECT id FROM scan_runs WHERE status = 'completed'")) == 2


def test_watch_progress_json_emits_one_event_per_rescan(repo: Path) -> None:
    edit = _edit_when_first_scan_done(repo, "// again\n")
    res = runner.invoke(app, ["--progress", "json", "-C", str(repo), "scan", "--watch", "--debounce", "0.3",
                              "--watch-cycles", "1"])
    edit.join(timeout=5)
    assert res.exit_code == 0, res.output
    events = [json.loads(x) for x in res.stdout.strip().splitlines() if x.startswith("{")]
    rescans = [e for e in events if e.get("stage") == "watch"]
    assert len(rescans) == 1 and rescans[0]["status"] == "rescan"
    assert rescans[0]["changed"] == 1 and rescans[0]["outcome"] == "completed"


@pytest.mark.parametrize("args", [["--watch", "--prose"], ["--watch", "--only", "parse"], ["--watch", "--dry-run"]])
def test_watch_rejects_spend_and_partial_runs(repo: Path, args) -> None:
    res = runner.invoke(app, ["--json", "-C", str(repo), "scan", *args])
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["reason"] == "contradictory_flags"
