"""Tests for the RAG embedder and retriever modules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_discovery.graph.models import CodeChunk
from ai_discovery.rag.embedder import embed_chunks
from ai_discovery.rag.retriever import search
from ai_discovery.db import init_db


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


def test_resume_skips_without_calling_embedding_endpoint(tmp_path: Path):
    """Resume path must not call get_embedding (so Bedrock outages don't
    force a re-embed of already-stored vectors)."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [
        _make_chunk("class Foo {}", 0, "com.Foo"),
        _make_chunk("class Bar {}", 1, "com.Bar"),
    ]

    # First run: populate the vec tables.
    embed_chunks(chunks, db, _make_llm_client())

    # Second run: endpoint is "down" — any get_embedding call raises.
    dead_client = MagicMock()
    dead_client.get_embedding.side_effect = RuntimeError(
        "Bedrock endpoint unreachable"
    )

    result = embed_chunks(chunks, db, dead_client)

    assert result["resumed"] is True
    assert result["embedded"] == 2
    assert result["dim"] == DIM
    dead_client.get_embedding.assert_not_called()


def test_parallel_embed_preserves_order_and_count(tmp_path: Path):
    """Parallel embedding must place each vector in the correct slot even
    when futures complete out of order (as_completed does not preserve
    submission order)."""
    db = tmp_path / "test.db"
    init_db(db)

    n = 20
    chunks = [
        _make_chunk(f"chunk text {i}", i, f"mod.C{i}")
        for i in range(n)
    ]
    # Each chunk gets a unique vector whose i-th slot is 1.0 (requires
    # dim >= n so there are no collisions).
    dim = n
    vectors: dict[str, list[float]] = {}
    for i in range(n):
        vec = [0.0] * dim
        vec[i] = 1.0
        vectors[f"chunk text {i}"] = vec

    client = MagicMock()
    client.get_embedding.side_effect = lambda text: vectors[text]

    result = embed_chunks(chunks, db, client, max_workers=8)

    assert result["embedded"] == n
    assert result["dim"] == dim
    assert result["resumed"] is False

    # Verify stored vectors match expected text→vector mapping: each chunk's
    # nearest neighbour (via its own vector) must be itself.
    for i in range(n):
        hits = search(f"chunk text {i}", db, client, top_k=1)
        assert hits, f"no hit for chunk {i}"
        assert hits[0]["qualified_name"] == f"mod.C{i}", (
            f"chunk {i} mis-mapped to {hits[0]['qualified_name']}"
        )


def test_progress_callback_fires_per_chunk(tmp_path: Path):
    """progress_callback must be invoked once per chunk, ending at total."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [_make_chunk(f"c{i}", i, f"mod.C{i}") for i in range(5)]
    calls: list[tuple[int, int]] = []

    embed_chunks(
        chunks, db, _make_llm_client(),
        max_workers=3,
        progress_callback=lambda done, total: calls.append((done, total)),
    )

    assert len(calls) == 5
    assert all(total == 5 for _, total in calls)
    assert calls[-1][0] == 5


def test_force_reembeds_even_when_rows_exist(tmp_path: Path):
    """force=True must re-embed regardless of stored rows."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [_make_chunk("class Foo {}", 0, "com.Foo")]

    embed_chunks(chunks, db, _make_llm_client())

    client = _make_llm_client()
    result = embed_chunks(chunks, db, client, force=True)

    assert result["resumed"] is False
    assert result["embedded"] == 1
    client.get_embedding.assert_called()


def test_incremental_adds_new_chunks_preserves_existing(tmp_path: Path):
    """Adding one chunk to an existing index must embed only the new chunk,
    not re-embed the two that haven't changed."""
    db = tmp_path / "test.db"
    init_db(db)

    first = [
        _make_chunk("class Foo {}", 0, "com.Foo"),
        _make_chunk("class Bar {}", 1, "com.Bar"),
    ]
    embed_chunks(first, db, _make_llm_client())

    # Second run: same two chunks PLUS a new one. Only the new chunk's text
    # should hit the embedding endpoint.
    second = first + [_make_chunk("class Baz {}", 2, "com.Baz")]
    client = _make_llm_client()
    result = embed_chunks(second, db, client)

    assert result["resumed"] is False
    assert result["added"] == 1
    assert result["removed"] == 0
    assert result["kept"] == 2
    assert result["embedded"] == 3
    # Exactly one call — for the new chunk's text.
    called_texts = [c.args[0] for c in client.get_embedding.call_args_list]
    assert called_texts == ["class Baz {}"]


def test_incremental_deletes_stale_chunks(tmp_path: Path):
    """When a chunk disappears from the desired set, its row must be removed
    rather than left stale (stale vectors poison semantic search)."""
    db = tmp_path / "test.db"
    init_db(db)

    original = [
        _make_chunk("class Foo {}", 0, "com.Foo"),
        _make_chunk("class Bar {}", 1, "com.Bar"),
        _make_chunk("class Baz {}", 2, "com.Baz"),
    ]
    embed_chunks(original, db, _make_llm_client())

    # Second run drops com.Bar
    second = [
        _make_chunk("class Foo {}", 0, "com.Foo"),
        _make_chunk("class Baz {}", 2, "com.Baz"),
    ]
    result = embed_chunks(second, db, _make_llm_client())

    assert result["removed"] == 1
    assert result["kept"] == 2
    assert result["added"] == 0
    assert result["embedded"] == 2

    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    qnames = {r["qualified_name"] for r in conn.execute(
        "SELECT qualified_name FROM discovery_chunk_meta"
    ).fetchall()}
    conn.close()
    assert qnames == {"com.Foo", "com.Baz"}


def test_incremental_reembeds_changed_text(tmp_path: Path):
    """When a chunk's text changes, the old vector is dropped and a new one
    is embedded (hash covers content)."""
    db = tmp_path / "test.db"
    init_db(db)

    v1 = [_make_chunk("class Foo { old }", 0, "com.Foo")]
    embed_chunks(v1, db, _make_llm_client())

    v2 = [_make_chunk("class Foo { new }", 0, "com.Foo")]
    client = _make_llm_client()
    result = embed_chunks(v2, db, client)

    assert result["added"] == 1
    assert result["removed"] == 1
    assert result["kept"] == 0
    called = [c.args[0] for c in client.get_embedding.call_args_list]
    assert called == ["class Foo { new }"]


def test_batch_embed_is_used_when_client_supports_it(tmp_path: Path):
    """If llm_client exposes get_embeddings, the embedder should use it to
    batch requests — cutting HTTP round-trips from N to ceil(N/batch)."""
    db = tmp_path / "test.db"
    init_db(db)

    chunks = [_make_chunk(f"c{i}", i, f"mod.C{i}") for i in range(5)]

    client = MagicMock()
    default_vec = [0.1] * DIM
    client.get_embedding.side_effect = lambda _text: default_vec
    client.get_embeddings.side_effect = lambda texts: [default_vec for _ in texts]

    result = embed_chunks(chunks, db, client)
    assert result["embedded"] == 5
    # Probe (1 call) uses get_embedding; the rest should go through get_embeddings.
    assert client.get_embeddings.called
    # Only chunk 0 goes through get_embedding (probe).
    assert client.get_embedding.call_count == 1
