"""v1 store tables, current_scan pointer, GC, FTS, embedder pinning (roadmap item M1.5)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.db import get_conn, init_db
from ai_discovery.store import current_version, discover_migrations, migrate
from ai_discovery.store.fts import create_fts, fts5_available, rebuild_fts
from ai_discovery.store.scans import gc_snapshots, get_current_scan, set_current_scan
from ai_discovery.store.vectors import EmbedderMismatch, check_embedder, pin_embedder, pinned_embedder

V1_TABLES = {"current_scan", "stage_runs", "unresolved_calls", "degradations", "file_parse_cache",
             "llm_cache", "pages", "page_links", "page_tombstones", "page_notes", "vec_meta"}


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _db(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "d.db"
    init_db(db)
    return get_conn(db)


def _add_scan(conn, status="completed") -> int:
    cur = conn.execute("INSERT INTO scan_runs (repo_path, started_at, status) VALUES ('r', 'now', ?)", (status,))
    conn.commit()
    return cur.lastrowid


# ── schema ────────────────────────────────────────────────────────────────

def test_head_includes_v1_tables(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "x.db")
    migrate(conn)
    assert current_version(conn) == discover_migrations()[-1].version >= 2
    assert V1_TABLES <= _tables(conn)


def test_fts_table_when_available(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "x.db")
    migrate(conn)
    if not fts5_available(conn):
        pytest.skip("SQLite build has no FTS5 — pages_fts skipped by design (doctor reports sqlite_fts5)")
    assert "pages_fts" in _tables(conn)


def test_migration_without_fts5_still_succeeds(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "x.db")
    with patch("ai_discovery.store.fts.fts5_available", return_value=False):
        migrate(conn)
    assert V1_TABLES <= _tables(conn) and "pages_fts" not in _tables(conn)
    if fts5_available(conn):
        assert create_fts(conn) and "pages_fts" in _tables(conn)  # can be added later


# ── current_scan + GC ─────────────────────────────────────────────────────

def test_current_scan_is_a_single_row_pointer(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    a, b = _add_scan(conn), _add_scan(conn)
    assert get_current_scan(conn) is None
    set_current_scan(conn, a)
    set_current_scan(conn, b)
    conn.commit()
    assert get_current_scan(conn) == b
    assert conn.execute("SELECT COUNT(*) FROM current_scan").fetchone()[0] == 1


def test_gc_keeps_newest_current_and_running(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    ids = [_add_scan(conn) for _ in range(5)]
    running = _add_scan(conn, status="running")
    set_current_scan(conn, ids[0])  # an old scan is still what readers see
    conn.execute("INSERT INTO code_nodes (scan_id, file_path, node_type, name, qualified_name) "
                 "VALUES (?, 'f', 'class', 'A', 'A')", (ids[1],))
    conn.commit()

    deleted = gc_snapshots(conn, keep=2)
    conn.commit()
    left = {r[0] for r in conn.execute("SELECT id FROM scan_runs")}
    # newest 2 finished (ids[3], ids[4]) + current (ids[0]) + running survive
    assert left == {ids[0], ids[3], ids[4], running}
    assert sorted(deleted) == [ids[1], ids[2]]
    # child rows cascade
    assert conn.execute("SELECT COUNT(*) FROM code_nodes WHERE scan_id = ?", (ids[1],)).fetchone()[0] == 0


def test_gc_rejects_keep_zero(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        gc_snapshots(_db(tmp_path), keep=0)


# ── pipeline integration ──────────────────────────────────────────────────

def _scan(repo: Path, out: Path, *, cost: float = 0.0, keep: int = 3):
    from ai_discovery.ai.self_review import ReviewClaim
    from ai_discovery.pipeline import run_pipeline
    from tests.golden.stub_llm import StubLLM

    stub = StubLLM()
    stub.total_cost_usd = lambda: cost
    cfg = DiscoveryConfig(provider="openai", budget_limit_usd=1.0, max_concurrent=1)
    cfg.store.keep_snapshots = keep
    with (
        patch("ai_discovery.ai.llm_client.LLMClient", return_value=stub),
        patch("ai_discovery.rag.embedder.embed_chunks", return_value={"embedded": 0, "dim": 256}),
        patch("ai_discovery.rag.retriever.search", return_value=[]),
        patch("ai_discovery.ai.self_review.review_document",
              return_value=[ReviewClaim(claim_text="c", status="verified")]),
        patch("ai_discovery.ai.self_review.persist_claims"),
    ):
        return run_pipeline(repo=str(repo), branch="main", project_slug="s", output_dir=out / "out",
                            docs_root=out / "docs", config=cfg)


def _commit(repo: Path, name: str) -> None:
    from tests.golden.test_golden import _GIT_ENV

    (repo / name).write_text("x\n")
    env = {**os.environ, **_GIT_ENV}
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", name], cwd=repo, env=env, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

    return _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])


def _pipeline_db(tmp_path: Path) -> sqlite3.Connection:
    return get_conn(tmp_path / "out" / "output-s" / "discovery-s.db")


def test_completed_scan_flips_budget_stop_does_not(repo: Path, tmp_path: Path) -> None:
    assert _scan(repo, tmp_path) == "completed"
    conn = _pipeline_db(tmp_path)
    first = get_current_scan(conn)
    assert first is not None

    _commit(repo, "change.txt")
    assert _scan(repo, tmp_path, cost=5.0) == "budget_exceeded"
    conn = _pipeline_db(tmp_path)
    assert get_current_scan(conn) == first  # readers keep seeing the last good scan
    assert conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0] == 2


def test_pipeline_gc_respects_keep(repo: Path, tmp_path: Path) -> None:
    for i in range(3):
        if i:
            _commit(repo, f"c{i}.txt")
        assert _scan(repo, tmp_path, keep=1) == "completed"
    conn = _pipeline_db(tmp_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM scan_runs")]
    assert ids == [get_current_scan(conn)]


# ── FTS rebuild ───────────────────────────────────────────────────────────

def test_rebuild_fts_indexes_pages(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    if not fts5_available(conn):
        pytest.skip("SQLite build has no FTS5")
    sid = _add_scan(conn)
    conn.execute(
        "INSERT INTO pages (scan_id, page_id, type, title, body_md, facts_json) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, "screen:order-cancel", "screen", "Order cancel", "Cancels an order",
         json.dumps({"tables": ["orders"], "korean": "주문 취소"})),
    )
    assert rebuild_fts(conn, sid) == 1
    hits = [r[0] for r in conn.execute("SELECT page_id FROM pages_fts WHERE pages_fts MATCH 'orders'")]
    assert hits == ["screen:order-cancel"]
    assert [r[0] for r in conn.execute("SELECT page_id FROM pages_fts WHERE pages_fts MATCH '주문'")] == hits


# ── embedder pinning ──────────────────────────────────────────────────────

def test_pin_and_check_embedder(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    check_embedder(conn, "code", "aigw:pplx-embed", 1024)  # nothing pinned yet
    pin_embedder(conn, "code", "aigw:pplx-embed", 1024)
    check_embedder(conn, "code", "aigw:pplx-embed", 1024)
    with pytest.raises(EmbedderMismatch, match="reindex"):
        check_embedder(conn, "code", "openai:text-embedding-3-small", 1024)
    with pytest.raises(EmbedderMismatch):
        check_embedder(conn, "code", "aigw:pplx-embed", 768)


def _pinned(db: Path):
    # Read through a short-lived connection: never keep a stdlib connection open
    # while the embedder's pysqlite3 connection writes the same DB.
    from contextlib import closing

    with closing(get_conn(db)) as conn:
        return pinned_embedder(conn, "code")


def test_embed_chunks_reindexes_on_model_change(tmp_path: Path) -> None:
    pytest.importorskip("sqlite_vec")
    from ai_discovery.graph.models import CodeChunk
    from ai_discovery.rag.embedder import embed_chunks

    db = tmp_path / "d.db"
    init_db(db)
    chunk = CodeChunk(text="def f(): pass", chunk_index=0, chunk_type="method",
                      file_path="a.py", language="python", qualified_name="a.f")

    def client(model: str, value: float):
        cfg = DiscoveryConfig(provider="aigw")
        cfg.rag.embedding_model = model
        return SimpleNamespace(_config=cfg, _embedding_provider=lambda: "aigw",
                               _embedding_model_for=lambda p: model,
                               get_embedding=lambda text: [value] * 8)

    try:
        first = embed_chunks([chunk], db, client("model-a", 0.1))
    except Exception as exc:  # sqlite-vec not loadable in this build
        pytest.skip(f"sqlite-vec unavailable: {exc}")
    assert first["added"] == 1
    assert _pinned(db) == ("aigw:model-a", 8)

    again = embed_chunks([chunk], db, client("model-a", 0.1))
    assert again["resumed"] is True  # same model: reuse

    switched = embed_chunks([chunk], db, client("model-b", 0.9))
    assert switched["added"] == 1 and switched["embedded"] == 1  # rebuilt, not mixed
    assert _pinned(db) == ("aigw:model-b", 8)
