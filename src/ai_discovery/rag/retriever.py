"""Semantic search against discovery_vectors using sqlite-vec."""

from __future__ import annotations

from pathlib import Path

from .embedder import _get_vec_conn, _load_sqlite_vec, _serialize_f32
from .doc_embedder import search_docs


def dual_search(
    query: str,
    db_path: Path,
    llm_client,
    top_k: int = 5,
) -> list[dict]:
    """Search both code chunks and generated docs, merge and rank by distance.

    Returns up to top_k*2 results tagged with source='code' or source='doc'.
    """
    query_vec = llm_client.get_embedding(query)
    code_results = search(query, db_path, llm_client, top_k=top_k, query_vec=query_vec)
    for r in code_results:
        r["source"] = "code"
    doc_results = search_docs(query_vec, db_path, top_k=top_k)
    combined = sorted(code_results + doc_results, key=lambda r: r["distance"])
    return combined


def search(
    query: str,
    db_path: Path,
    llm_client,
    top_k: int = 5,
    query_vec: list[float] | None = None,  # skip embedding if provided
) -> list[dict]:
    """Semantic search against discovery_vectors.

    1. Embed query text (or use pre-computed query_vec if provided)
    2. MATCH against vec table with k=top_k
    3. JOIN with discovery_chunk_meta
    4. Return list of dicts with: qualified_name, chunk_text, domain,
       file_path, distance, chunk_type
    """
    conn = _get_vec_conn(db_path)
    try:
        # Check tables exist
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='discovery_chunk_meta'"
        ).fetchone()
        if not row:
            return []

        _load_sqlite_vec(conn)
        if query_vec is None:
            query_vec = llm_client.get_embedding(query)
        rows = conn.execute(
            """SELECT v.rowid, v.distance,
                      m.qualified_name, m.chunk_text, m.domain,
                      m.file_path, m.chunk_type
               FROM discovery_vectors v
               JOIN discovery_chunk_meta m ON m.rowid = v.rowid
               WHERE v.embedding MATCH ?
                 AND k = ?
               ORDER BY v.distance
               LIMIT ?""",
            (_serialize_f32(query_vec), top_k, top_k),
        ).fetchall()

        return [
            {
                "qualified_name": r["qualified_name"],
                "chunk_text": r["chunk_text"],
                "domain": r["domain"],
                "file_path": r["file_path"],
                "distance": r["distance"],
                "chunk_type": r["chunk_type"],
            }
            for r in rows
        ]
    finally:
        conn.close()
