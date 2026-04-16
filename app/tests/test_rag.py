"""Tests for the RAG embedder and retriever modules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.graph.models import CodeChunk
from app.rag.embedder import embed_chunks
from app.rag.retriever import search
from app.db import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIM = 8  # small dimension for tests


def _make_chunk(
    text: str,
    index: int,
    qualified_name: str,
    file_path: str = "src/Main.java",
    language: str = "java",
    chunk_type: str = "class",
    domain: str | None = "payments",
    node_type: str = "class",
) -> CodeChunk:
    return CodeChunk(
        text=text,
        chunk_index=index,
        chunk_type=chunk_type,
        file_path=file_path,
        language=language,
        qualified_name=qualified_name,
        domain=domain,
        node_type=node_type,
    )


def _make_vector(index: int, dim: int = DIM) -> list[float]:
    """Create a deterministic unit-ish vector with the given index hot."""
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


def _make_llm_client(vectors: dict[str, list[float]] | None = None) -> MagicMock:
    """Return a mock LLMClient whose get_embedding returns deterministic vectors.

    If *vectors* maps text → vector, those are used. Otherwise a default
    vector is returned.
    """
    client = MagicMock()
    default_vec = [0.1] * DIM

    def _get_embedding(text: str) -> list[float]:
        if vectors and text in vectors:
            return vectors[text]
        return default_vec

    client.get_embedding.side_effect = _get_embedding
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_embed_chunks_creates_tables(tmp_path: Path):
    """Embed 3 chunks, verify tables exist with correct row count."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [
        _make_chunk("class Foo {}", 0, "com.Foo"),
        _make_chunk("class Bar {}", 1, "com.Bar"),
        _make_chunk("class Baz {}", 2, "com.Baz"),
    ]
    client = _make_llm_client()
    result = embed_chunks(chunks, db, client)

    assert result["embedded"] == 3
    assert result["dim"] == DIM

    # Verify tables and row counts directly
    import sqlite3
    conn = sqlite3.connect(str(db))
    meta_count = conn.execute("SELECT COUNT(*) FROM discovery_chunk_meta").fetchone()[0]
    assert meta_count == 3
    conn.close()


def test_embed_chunks_stores_metadata(tmp_path: Path):
    """Embed chunks and verify metadata stored correctly."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [
        _make_chunk("class Order {}", 0, "com.Order", file_path="src/Order.java", domain="orders"),
        _make_chunk("class Payment {}", 1, "com.Payment", file_path="src/Payment.java", domain="payments"),
    ]
    client = _make_llm_client()
    embed_chunks(chunks, db, client)

    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT file_path, qualified_name, domain FROM discovery_chunk_meta ORDER BY chunk_index"
    ).fetchall()

    assert len(rows) == 2
    assert rows[0]["file_path"] == "src/Order.java"
    assert rows[0]["qualified_name"] == "com.Order"
    assert rows[0]["domain"] == "orders"
    assert rows[1]["file_path"] == "src/Payment.java"
    assert rows[1]["qualified_name"] == "com.Payment"
    assert rows[1]["domain"] == "payments"
    conn.close()


def test_search_returns_relevant_chunks(tmp_path: Path):
    """Embed chunks with different vectors, verify nearest is returned first."""
    db = tmp_path / "test.db"
    init_db(db)

    # Three chunks with distinct vectors
    chunks = [
        _make_chunk("alpha code", 0, "mod.Alpha"),
        _make_chunk("beta code", 1, "mod.Beta"),
        _make_chunk("gamma code", 2, "mod.Gamma"),
    ]

    vec_alpha = _make_vector(0)
    vec_beta = _make_vector(1)
    vec_gamma = _make_vector(2)

    vectors = {
        "alpha code": vec_alpha,
        "beta code": vec_beta,
        "gamma code": vec_gamma,
    }
    client = _make_llm_client(vectors)
    embed_chunks(chunks, db, client)

    # Query with vector close to beta
    query_text = "find beta"
    vectors["find beta"] = vec_beta
    results = search(query_text, db, client, top_k=3)

    assert len(results) >= 1
    assert results[0]["qualified_name"] == "mod.Beta"
    assert results[0]["distance"] == pytest.approx(0.0, abs=1e-5)


def test_search_respects_top_k(tmp_path: Path):
    """Embed 5 chunks, search with top_k=2, verify only 2 results."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [
        _make_chunk(f"chunk {i}", i, f"mod.C{i}")
        for i in range(5)
    ]
    client = _make_llm_client()
    embed_chunks(chunks, db, client)

    results = search("query", db, client, top_k=2)
    assert len(results) == 2


def test_search_empty_db_returns_empty(tmp_path: Path):
    """Search on DB with no vec tables returns empty list."""
    db = tmp_path / "test.db"
    init_db(db)

    client = _make_llm_client()
    results = search("anything", db, client, top_k=5)
    assert results == []
