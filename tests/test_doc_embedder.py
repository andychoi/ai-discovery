"""Tests for rag.doc_embedder — doc-store embedding + resume guard.

Prior to W0-6 the doc store keyed each chunk on the bare filename stem, so
ASIS/orders.md, ASD/orders.md and ASSC/orders.md collapsed to one doc_id.
That made COUNT(DISTINCT doc_id) undercount files (so the resume guard never
fired) and made chat citations ambiguous. These tests pin the folder-qualified
doc_id and a working resume guard.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_discovery.rag.doc_embedder import embed_docs, search_docs


class _FakeLLM:
    """Deterministic 4-dim embeddings; counts calls to observe resume."""

    def __init__(self):
        self.calls = 0

    def get_embedding(self, text: str) -> list[float]:
        self.calls += 1
        h = sum(ord(c) for c in text[:32])
        return [float(h % 7), float(len(text) % 5), 1.0, 0.0]


def _make_docs(root: Path) -> None:
    for folder in ("ASIS", "ASD", "ASSC"):
        d = root / folder
        d.mkdir(parents=True)
        (d / "orders.md").write_text(
            f"---\ndomain: orders\n---\n\n## Overview\n{folder} orders doc body.\n"
        )


def _vec_conn(db_path: Path):
    import ai_discovery.rag.embedder as emb
    return emb._get_vec_conn(db_path)


def test_doc_ids_are_folder_qualified_not_colliding(tmp_path: Path):
    docs = tmp_path / "docs"
    _make_docs(docs)
    db = tmp_path / "d.db"

    result = embed_docs(docs, db, _FakeLLM())
    assert result["files"] == 3

    conn = _vec_conn(db)
    try:
        from ai_discovery.rag.embedder import _load_sqlite_vec
        _load_sqlite_vec(conn)
        ids = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT doc_id FROM discovery_doc_chunk_meta"
            ).fetchall()
        }
    finally:
        conn.close()

    # Three distinct, folder-qualified ids — no collision on the shared stem.
    assert ids == {"ASIS/orders", "ASD/orders", "ASSC/orders"}


def test_resume_guard_fires_second_run(tmp_path: Path):
    docs = tmp_path / "docs"
    _make_docs(docs)
    db = tmp_path / "d.db"

    first = embed_docs(docs, db, _FakeLLM())
    assert first["resumed"] is False

    second_llm = _FakeLLM()
    second = embed_docs(docs, db, second_llm)
    # With unique doc_ids, COUNT(DISTINCT doc_id) == file count, so the guard
    # resumes and the second run makes no embedding calls.
    assert second["resumed"] is True
    assert second_llm.calls == 0


def test_search_docs_returns_qualified_doc_id(tmp_path: Path):
    docs = tmp_path / "docs"
    _make_docs(docs)
    db = tmp_path / "d.db"
    llm = _FakeLLM()
    embed_docs(docs, db, llm)

    hits = search_docs(llm.get_embedding("orders doc body"), db, top_k=3)
    assert hits
    assert all("/" in h["doc_id"] for h in hits)
