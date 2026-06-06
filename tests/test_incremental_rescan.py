"""Tests for fingerprint-based incremental re-scan (assessment 07 A-2).

Phase 6 stamps a per-file SHA256 on every parsed node; on a NEW scan of the
same project, Tier-1 summaries are copied from the latest prior scan for
nodes whose (qualified_name, file_path, file_hash) are unchanged — only
changed/new nodes pay an LLM call.
"""

import hashlib
import json
import sqlite3
from unittest.mock import MagicMock

from ai_discovery.db import init_db
from ai_discovery.ai.summarizer import reuse_prior_summaries, summarize_chunks
from ai_discovery.graph.models import CodeNode
from ai_discovery.pipeline import _stamp_file_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(qname: str, file_path: str = "src/mod.py") -> CodeNode:
    return CodeNode(
        file_path=file_path,
        language="python",
        node_type="function",
        name=qname.rsplit(".", 1)[-1],
        qualified_name=qname,
        source_code="def f(): pass",
        line_start=1,
        line_end=2,
    )


def _seed_scan(conn, scan_id: int, nodes: list[tuple[str, str, str]]) -> dict[str, int]:
    """Insert a scan_run + code_nodes rows [(qname, file_path, file_hash)].
    Returns {qname: node_db_id}."""
    conn.execute(
        "INSERT INTO scan_runs (id, started_at, status) VALUES (?, '2026-01-01', 'completed')",
        (scan_id,),
    )
    ids = {}
    for qname, file_path, file_hash in nodes:
        cur = conn.execute(
            "INSERT INTO code_nodes (scan_id, file_path, language, node_type, name, "
            "qualified_name, file_hash) VALUES (?, ?, 'python', 'function', ?, ?, ?)",
            (scan_id, file_path, qname.rsplit(".", 1)[-1], qname, file_hash),
        )
        ids[qname] = cur.lastrowid
    return ids


def _seed_summary(conn, node_id: int, purpose: str = "Does things") -> None:
    conn.execute(
        "INSERT INTO node_summaries (node_id, tier, model_used, purpose, business_rules, "
        "io_summary, tech_debt_signals, raw_response, tokens_in, tokens_out, created_at) "
        "VALUES (?, 'tier1', 'haiku-test', ?, 'None detected', 'No I/O', 'None detected', "
        "'{}', 100, 50, '2026-01-01')",
        (node_id, purpose),
    )


# ---------------------------------------------------------------------------
# Parse-time hashing
# ---------------------------------------------------------------------------

def test_code_nodes_file_hash_column_exists(tmp_path):
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(code_nodes)").fetchall()}
    conn.close()
    assert "file_hash" in cols


def test_stamp_file_hash_sets_sha256_on_all_nodes(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("def f(): pass\n")
    nodes = [_node("mod.f", str(src)), _node("mod.g", str(src))]
    _stamp_file_hash(nodes, src)
    expected = hashlib.sha256(src.read_bytes()).hexdigest()
    assert all(n.file_hash == expected for n in nodes)


def test_stamp_file_hash_tolerates_unreadable_file(tmp_path):
    nodes = [_node("mod.f")]
    _stamp_file_hash(nodes, tmp_path / "missing.py")  # must not raise
    assert nodes[0].file_hash == ""


# ---------------------------------------------------------------------------
# Cross-scan summary reuse
# ---------------------------------------------------------------------------

def test_reuse_copies_summaries_for_unchanged_nodes(tmp_path):
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    old = _seed_scan(conn, 1, [("mod.f", "src/mod.py", "h1"), ("mod.g", "src/mod.py", "h1")])
    for node_id in old.values():
        _seed_summary(conn, node_id)
    new = _seed_scan(conn, 2, [("mod.f", "src/mod.py", "h1"), ("mod.g", "src/mod.py", "h1")])
    conn.commit(); conn.close()

    n = reuse_prior_summaries(db, 2)
    assert n == 2

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT ns.purpose, ns.tokens_in FROM node_summaries ns WHERE ns.node_id IN (?, ?)",
        tuple(new.values()),
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(r[0] == "Does things" for r in rows)
    # reused rows cost this scan nothing — tokens are zeroed, not double-counted
    assert all(r[1] == 0 for r in rows)


def test_reuse_skips_changed_and_new_files(tmp_path):
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    old = _seed_scan(conn, 1, [("mod.f", "src/mod.py", "h1")])
    _seed_summary(conn, old["mod.f"])
    new = _seed_scan(conn, 2, [
        ("mod.f", "src/mod.py", "h2"),       # changed content
        ("other.k", "src/other.py", "h9"),    # new file, no prior
    ])
    conn.commit(); conn.close()

    assert reuse_prior_summaries(db, 2) == 0
    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM node_summaries WHERE node_id IN (?, ?)",
        tuple(new.values()),
    ).fetchone()[0]
    conn.close()
    assert count == 0


def test_reuse_without_prior_scan_returns_zero(tmp_path):
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    _seed_scan(conn, 1, [("mod.f", "src/mod.py", "h1")])
    conn.commit(); conn.close()
    assert reuse_prior_summaries(db, 1) == 0


def test_reuse_ignores_blank_hashes(tmp_path):
    """Nodes without a hash (extractor-sourced, pre-migration scans) must
    never match — a blank == blank join would 'reuse' across real changes."""
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    old = _seed_scan(conn, 1, [("mod.f", "src/mod.py", "")])
    _seed_summary(conn, old["mod.f"])
    _seed_scan(conn, 2, [("mod.f", "src/mod.py", "")])
    conn.commit(); conn.close()
    assert reuse_prior_summaries(db, 2) == 0


def test_reuse_does_not_clobber_existing_summary(tmp_path):
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    old = _seed_scan(conn, 1, [("mod.f", "src/mod.py", "h1")])
    _seed_summary(conn, old["mod.f"], purpose="old version")
    new = _seed_scan(conn, 2, [("mod.f", "src/mod.py", "h1")])
    _seed_summary(conn, new["mod.f"], purpose="already here")
    conn.commit(); conn.close()

    reuse_prior_summaries(db, 2)
    conn = sqlite3.connect(str(db))
    purpose = conn.execute(
        "SELECT purpose FROM node_summaries WHERE node_id = ?", (new["mod.f"],)
    ).fetchone()[0]
    conn.close()
    assert purpose == "already here"


def test_reused_summaries_skip_llm_via_resume_guard(tmp_path):
    """End-to-end: after reuse, summarize_chunks' resume guard sees the copied
    rows and fires zero LLM calls for unchanged nodes."""
    from ai_discovery.graph.models import CodeChunk

    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    old = _seed_scan(conn, 1, [("mod.f", "src/mod.py", "h1")])
    _seed_summary(conn, old["mod.f"])
    _seed_scan(conn, 2, [("mod.f", "src/mod.py", "h1")])
    conn.commit(); conn.close()

    reuse_prior_summaries(db, 2)
    chunk = CodeChunk(
        text="def f(): pass", chunk_index=0, chunk_type="function",
        file_path="src/mod.py", language="python", qualified_name="mod.f",
    )
    client = MagicMock()
    results = summarize_chunks([chunk], client, db, scan_id=2, skip_rag=True)
    assert client.invoke.call_count == 0
    assert client.invoke_structured.call_count == 0
    assert results and results[0]["qualified_name"] == "mod.f"
