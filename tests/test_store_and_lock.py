"""Numbered migrations + single-flight run lock (roadmap item M1.4)."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from ai_discovery.core import lock as lockmod
from ai_discovery.core.lock import LockHeld, RunLock
from ai_discovery.db import get_conn, init_db
from ai_discovery.store import MigrationError, current_version, discover_migrations, migrate
import importlib

migrate_mod = importlib.import_module("ai_discovery.store.migrate")


# ── store: numbered migrations ────────────────────────────────────────────

def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_empty_db_migrates_to_head(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "x.db")
    applied = migrate(conn)
    head = discover_migrations()[-1].version
    assert applied == list(range(1, head + 1)) and current_version(conn) == head
    assert {"scan_runs", "code_nodes", "call_edges", "schema_migrations"} <= _tables(conn)
    assert migrate(conn) == []  # idempotent


def test_init_db_records_baseline_and_keeps_pragmas(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    init_db(db)
    conn = get_conn(db)
    assert current_version(conn) == discover_migrations()[-1].version
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_legacy_v15_db_is_adopted_not_broken(tmp_path: Path) -> None:
    """A DB created before M1.4 (schema_version 15, no schema_migrations) keeps its data."""
    db = tmp_path / "legacy.db"
    init_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO scan_runs (repo_path, started_at, status) VALUES ('r', 'now', 'completed')")
    conn.execute("DROP TABLE schema_migrations")
    conn.commit()
    conn.close()

    init_db(db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 1
    assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchone()[0] == 1


def _fake_pkg(monkeypatch, modules: dict[str, ModuleType]) -> None:
    """Make discover_migrations() see *modules* (name -> module)."""
    import pkgutil

    class Info:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(pkgutil, "iter_modules", lambda path: [Info(n) for n in modules])
    monkeypatch.setattr(migrate_mod.importlib, "import_module",
                        lambda full: modules[full.rsplit(".", 1)[-1]])


def _mig(fn) -> ModuleType:
    m = ModuleType("m")
    m.up = fn
    return m


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path: Path, monkeypatch) -> None:
    def good(conn):
        conn.execute("CREATE TABLE a (x)")

    def bad(conn):
        conn.execute("CREATE TABLE b (x)")
        raise RuntimeError("boom")

    _fake_pkg(monkeypatch, {"m0001_good": _mig(good), "m0002_bad": _mig(bad)})
    conn = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(MigrationError, match="0002_bad failed: boom"):
        migrate(conn)
    assert current_version(conn) == 1
    assert "a" in _tables(conn) and "b" not in _tables(conn)


def test_gap_in_numbering_is_rejected(monkeypatch) -> None:
    _fake_pkg(monkeypatch, {"m0001_a": _mig(lambda c: None), "m0003_c": _mig(lambda c: None)})
    with pytest.raises(MigrationError, match="contiguously"):
        discover_migrations()


def test_target_stops_early(tmp_path: Path, monkeypatch) -> None:
    _fake_pkg(monkeypatch, {"m0001_a": _mig(lambda c: None), "m0002_b": _mig(lambda c: None)})
    conn = sqlite3.connect(tmp_path / "x.db")
    assert migrate(conn, target=1) == [1] and migrate(conn) == [2]


# ── run lock ──────────────────────────────────────────────────────────────

def _write_lock(path: Path, **fields) -> None:
    info = {"pid": os.getpid(), "hostname": socket.gethostname(), "token": "other",
            "started_at": "2026-01-01T00:00:00+00:00", "purpose": "scan", "proc_start": None}
    info.update(fields)
    path.write_text(json.dumps(info), encoding="utf-8")


def test_second_writer_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    with RunLock(lock):
        with pytest.raises(LockHeld) as exc:
            RunLock(lock).acquire()
        assert exc.value.reason == "lock_held"
    assert not lock.exists()


def test_dead_owner_is_reclaimed(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    lock = tmp_path / "lock"
    _write_lock(lock, pid=proc.pid)
    with RunLock(lock) as held:
        assert json.loads(lock.read_text())["token"] == held.token


def test_recycled_pid_is_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    _write_lock(lock, pid=os.getpid(), proc_start="Thu Jan  1 00:00:00 1970")  # alive, wrong start
    with RunLock(lock):
        pass


def test_live_owner_is_never_evicted_on_age(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    _write_lock(lock, pid=os.getpid(), proc_start=lockmod._proc_start(os.getpid()),
                started_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(LockHeld, match="long time"):
        RunLock(lock).acquire()
    assert lock.exists()


def test_foreign_host_lock_is_not_evicted(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    _write_lock(lock, pid=1, hostname="some-other-host")
    with pytest.raises(LockHeld):
        RunLock(lock).acquire()


def test_fresh_unreadable_lock_is_respected_old_one_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("", encoding="utf-8")  # writer may be mid-write
    with pytest.raises(LockHeld):
        RunLock(lock).acquire()
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with RunLock(lock):
        pass


def test_release_never_deletes_someone_elses_lock(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    mine = RunLock(lock).acquire()
    _write_lock(lock, token="taken-over")
    mine.release()
    assert lock.exists()


# ── integration: pipeline + CLI ───────────────────────────────────────────

def test_pipeline_refuses_concurrent_scan(tmp_path: Path) -> None:
    from ai_discovery.config import DiscoveryConfig
    from ai_discovery.core.paths import ProjectLayout
    from ai_discovery.pipeline import run_pipeline

    layout = ProjectLayout.legacy(tmp_path / "out", "p", tmp_path / "docs").ensure()
    with RunLock(layout.lock_path, purpose="scan"):
        with pytest.raises(LockHeld):
            run_pipeline(repo=str(tmp_path), branch="main", project_slug="p",
                         output_dir=tmp_path / "out", docs_root=tmp_path / "docs",
                         config=DiscoveryConfig())


def test_scan_cli_exits_4_when_locked(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ai_discovery.cli import app

    with patch("ai_discovery.pipeline.run_pipeline", side_effect=LockHeld(tmp_path / "lock", {"pid": 1})):
        result = CliRunner().invoke(app, ["scan", str(tmp_path), "-p", "x", "-o", str(tmp_path / "o")])
    assert result.exit_code == 4, result.output
