"""Embed CodeChunks into sqlite-vec for semantic search."""

from __future__ import annotations

import logging
import struct
from datetime import datetime, timezone
from pathlib import Path

from ..graph.models import CodeChunk

logger = logging.getLogger(__name__)

# Use pysqlite3 for extension loading support; fall back to stdlib
try:
    import pysqlite3 as _sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3 as _sqlite3  # type: ignore[no-redef,assignment]


def _get_vec_conn(db_path: Path):
    """Get a pysqlite3 connection suitable for sqlite-vec extension loading."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _load_sqlite_vec(conn) -> None:
    """Load sqlite-vec extension into connection."""
    if not hasattr(conn, "enable_load_extension"):
        raise RuntimeError(
            "sqlite3 was compiled without extension loading support. "
            "Install pysqlite3: pip install pysqlite3"
        )
    import sqlite_vec  # type: ignore[import-untyped]

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _serialize_f32(vec: list[float]) -> bytes:
    """Serialize float list to bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


def init_vec_tables(conn, dim: int, force_recreate: bool = False) -> None:
    """Create discovery_vectors virtual table + discovery_chunk_meta.

    force_recreate=True drops and recreates (used for fresh scans).
    Default (False) uses CREATE IF NOT EXISTS so existing data is preserved on resume.
    """
    _load_sqlite_vec(conn)
    if force_recreate:
        conn.execute("DROP TABLE IF EXISTS discovery_vectors")
        conn.execute("DROP TABLE IF EXISTS discovery_chunk_meta")
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS discovery_vectors USING vec0(
            embedding float[{dim}]
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovery_chunk_meta (
            rowid          INTEGER PRIMARY KEY,
            file_path      TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            domain         TEXT,
            chunk_type     TEXT,
            chunk_text     TEXT,
            language       TEXT,
            node_type      TEXT,
            chunk_index    INTEGER NOT NULL,
            embedded_at    TEXT NOT NULL
        )
    """)
    conn.commit()


def embed_chunks(chunks: list[CodeChunk], db_path: Path, llm_client) -> dict:
    """Embed a list of CodeChunks into sqlite-vec.

    On resume: if discovery_chunk_meta already has >= len(chunks) rows,
    skips all embedding work and returns the existing count.

    Fresh scan: probes embedding dimension, recreates vec tables, embeds all chunks.
    Returns {"embedded": N, "dim": dim, "resumed": bool}
    """
    if not chunks:
        return {"embedded": 0, "dim": 0, "resumed": False}

    # Resume guard: check if embeddings are already complete AND dim matches.
    # If the embedding model changed (e.g. Bedrock 1024-dim → MLX 768-dim),
    # force a full re-embed so query vectors match stored vectors.
    try:
        conn = _get_vec_conn(db_path)
        try:
            _load_sqlite_vec(conn)
            existing = conn.execute(
                "SELECT COUNT(*) FROM discovery_chunk_meta"
            ).fetchone()[0]
            if existing >= len(chunks):
                # Probe current model's dimension
                probe_vec = llm_client.get_embedding(chunks[0].text)
                current_dim = len(probe_vec)
                # Read one stored vector to check its dimension
                stored_row = conn.execute(
                    "SELECT embedding FROM discovery_vectors LIMIT 1"
                ).fetchone()
                if stored_row:
                    stored_dim = len(struct.unpack(
                        f"{len(stored_row[0]) // 4}f", stored_row[0]
                    ))
                    if stored_dim != current_dim:
                        logger.info(
                            "RAG embed: stored dim %d != current dim %d — re-embedding",
                            stored_dim, current_dim,
                        )
                        # Fall through to fresh embed below, reusing probe_vec
                        conn.close()
                        # Embed remaining chunks and write to DB
                        vecs: list[list[float]] = [probe_vec]
                        for chunk in chunks[1:]:
                            vecs.append(llm_client.get_embedding(chunk.text))
                        conn = _get_vec_conn(db_path)
                        try:
                            init_vec_tables(conn, current_dim, force_recreate=True)
                            now = datetime.now(timezone.utc).isoformat()
                            for chunk, vec in zip(chunks, vecs):
                                _insert_chunk(conn, chunk, vec, now)
                            conn.commit()
                            return {"embedded": len(chunks), "dim": current_dim, "resumed": False}
                        finally:
                            conn.close()
                logger.info("RAG embed: %d vectors already indexed (dim=%d), skipping (resume)",
                            existing, current_dim)
                return {"embedded": existing, "dim": current_dim, "resumed": True}
        except Exception:
            pass  # tables don't exist yet — proceed with fresh embed
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass

    # Embed all chunks first (API calls outside any DB transaction)
    probe_vec = llm_client.get_embedding(chunks[0].text)
    dim = len(probe_vec)
    vecs: list[list[float]] = [probe_vec]
    for chunk in chunks[1:]:
        vecs.append(llm_client.get_embedding(chunk.text))

    # Write to DB in one short transaction — lock held only during fast inserts
    conn = _get_vec_conn(db_path)
    try:
        init_vec_tables(conn, dim, force_recreate=True)
        now = datetime.now(timezone.utc).isoformat()
        for chunk, vec in zip(chunks, vecs):
            _insert_chunk(conn, chunk, vec, now)
        conn.commit()
        return {"embedded": len(chunks), "dim": dim, "resumed": False}
    finally:
        conn.close()


def _insert_chunk(conn, chunk: CodeChunk, vec: list[float], now: str) -> None:
    """Insert a single chunk's vector and metadata."""
    vec_bytes = _serialize_f32(vec)
    cursor = conn.execute(
        "INSERT INTO discovery_vectors (embedding) VALUES (?)",
        (vec_bytes,),
    )
    rid = cursor.lastrowid
    conn.execute(
        """INSERT INTO discovery_chunk_meta
           (rowid, file_path, qualified_name, domain, chunk_type,
            chunk_text, language, node_type, chunk_index, embedded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rid,
            chunk.file_path,
            chunk.qualified_name,
            chunk.domain,
            chunk.chunk_type,
            chunk.text,
            chunk.language,
            chunk.node_type,
            chunk.chunk_index,
            now,
        ),
    )
