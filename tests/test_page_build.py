"""Page build hook, FTS, tombstones, idempotence, staleness (roadmap item M3.5)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.pages.build import nearest_parent, stale_pages
from ai_discovery.pages.notes import add_note, notes_for, resolve
from ai_discovery.store.fts import fts5_available
from tests.golden.test_golden import _GIT_ENV

FIXTURES = Path(__file__).resolve().parent / "fixtures"
runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env={**os.environ, **_GIT_ENV}, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", r)
    (r / ".gitignore").write_text(".discovery/\n")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _scan(repo: Path, *args: str) -> dict:
    res = runner.invoke(app, ["--json", "-C", str(repo), "scan", *args])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def _db(repo: Path) -> sqlite3.Connection:
    return sqlite3.connect(repo / ".discovery" / "discovery.db")


def _rows(conn, scan_id: int) -> list[tuple]:
    return conn.execute(
        "SELECT page_id, type, title, facts_json, body_md, links_json, citations_json, confidence, "
        "stamp_json, source_hashes_json, freshness FROM pages WHERE scan_id = ? ORDER BY page_id",
        (scan_id,)).fetchall()


# ── build hook ────────────────────────────────────────────────────────────

def test_scan_builds_pages_and_index(repo: Path) -> None:
    out = _scan(repo)
    pages_hook = next(h for h in out["hooks"] if h["name"] == "pages")
    assert pages_hook["ok"] and "pages" in pages_hook["detail"]
    conn = _db(repo)
    types = dict(conn.execute("SELECT type, COUNT(*) FROM pages WHERE scan_id = ? GROUP BY type", (out["scan_id"],)))
    assert {"overview", "module", "file", "symbol", "screen", "endpoint", "table"} <= set(types)
    assert conn.execute("SELECT COUNT(*) FROM page_links WHERE scan_id = ?", (out["scan_id"],)).fetchone()[0] > 0
    if fts5_available(conn):
        hits = {r[0] for r in conn.execute("SELECT page_id FROM pages_fts WHERE pages_fts MATCH 'orders'")}
        assert "table:orders" in hits


def test_two_full_scans_are_byte_identical(repo: Path) -> None:
    first = _scan(repo)
    second = _scan(repo, "--full")
    assert second["scan_id"] != first["scan_id"]
    conn = _db(repo)
    a, b = _rows(conn, first["scan_id"]), _rows(conn, second["scan_id"])
    assert a and a == b


# ── tombstones ────────────────────────────────────────────────────────────

def test_removed_pages_are_tombstoned_to_their_parent(repo: Path) -> None:
    _scan(repo)
    conn = _db(repo)
    gone = "file:src/main/java/com/example/batch/OrderExportJob.java"
    assert conn.execute("SELECT 1 FROM pages WHERE page_id = ?", (gone,)).fetchone()
    add_note(conn, gone, "nightly export is owned by finance")
    conn.commit()
    conn.close()

    (repo / "src/main/java/com/example/batch/OrderExportJob.java").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove export job")
    _scan(repo)

    conn = _db(repo)
    successor = conn.execute("SELECT successor FROM page_tombstones WHERE page_id = ?", (gone,)).fetchone()[0]
    assert successor in ("module:src/main/java/com/example/batch", "module:src/main/java/com/example")
    assert resolve(conn, gone) == successor
    assert [n.body for n in notes_for(conn, successor)] == ["nightly export is owned by finance"]


def test_nearest_parent_falls_back_to_overview() -> None:
    assert nearest_parent("file:a/b/C.java", {"module:a", "overview:system"}) == "module:a"
    assert nearest_parent("symbol:x.Y@a/B.java:3", {"file:a/B.java"}) == "file:a/B.java"
    assert nearest_parent("table:orders", set()) == "overview:system"


# ── staleness ─────────────────────────────────────────────────────────────

def test_staleness_propagates_up_the_hierarchy(repo: Path) -> None:
    out = _scan(repo)
    conn = _db(repo)
    assert stale_pages(conn, out["scan_id"], repo) == {}

    changed = repo / "src/main/java/com/example/service/OrderService.java"
    changed.write_text(changed.read_text() + "\n// edited after the scan\n")
    stale = stale_pages(conn, out["scan_id"], repo)
    file_page = "file:src/main/java/com/example/service/OrderService.java"
    assert stale[file_page].startswith("changed:")
    for up in ("module:src/main/java/com/example/service", "module:src/main/java/com/example", "module:src",
               "overview:system"):
        assert up in stale, up
    assert "file:src/main/java/com/example/service/CustomerService.java" not in stale


def test_status_reports_pages_and_stale_count(repo: Path) -> None:
    _scan(repo)
    (repo / "src/main/java/com/example/service/OrderService.java").write_text("// rewritten\n")
    res = json.loads(runner.invoke(app, ["--json", "-C", str(repo), "status"]).stdout)
    assert res["pages"]["total"] > 0 and res["pages"]["stale"] >= 4
