"""Embed CodeChunks into sqlite-vec for semantic search."""

from __future__ import annotations

import hashlib
import logging
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

from ..graph.models import CodeChunk

logger = logging.getLogger(__name__)

# Use pysqlite3 for extension loading support; fall back to stdlib
try:
    import pysqlite3 as _sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3 as _sqlite3  # type: ignore[no-redef,assignment]


# How many (vector, metadata) pairs to insert per transaction. Smaller batches
# bound memory (each vector is dim*4 bytes held in Python) and make partial
# failure less catastrophic — on a crash mid-embed, completed batches are kept.
_WRITE_BATCH_SIZE = 1000

# How many texts to send per batch-embed HTTP request when the client exposes
# get_embeddings(). Ollama handles 128–256 comfortably; staying at 64 keeps
# memory pressure modest and tolerates transient per-batch failures.
_EMBED_BATCH_SIZE = 64

# Probe retry: transient network blips on the very first call shouldn't
# terminate the whole phase. Full fan-out retries are still handled by
# boto3 (Bedrock) or upstream LLM client config.
_PROBE_MAX_ATTEMPTS = 3
_PROBE_BACKOFF_SEC = 1.5


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

    force_recreate=True drops and recreates (used for ``force=True`` re-embeds
    or when the stored vector dimension no longer matches the model).
    Default (False) uses CREATE IF NOT EXISTS so existing data is preserved
    for incremental updates.
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
            embedded_at    TEXT NOT NULL,
            chunk_hash     TEXT
        )
    """)
    _ensure_chunk_hash_column(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_meta_hash ON discovery_chunk_meta(chunk_hash)"
    )
    conn.commit()


def _ensure_chunk_hash_column(conn) -> None:
    """In-place migration: add chunk_hash to legacy DBs that predate it.

    Returns silently if the column already exists. The CREATE TABLE above is
    idempotent but won't add new columns to an existing table, so we do it
    explicitly here.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(discovery_chunk_meta)").fetchall()}
    if "chunk_hash" not in cols:
        conn.execute("ALTER TABLE discovery_chunk_meta ADD COLUMN chunk_hash TEXT")


def _chunk_hash(chunk: CodeChunk) -> str:
    """Stable content-addressed identifier for a chunk.

    Includes path + qualified_name + chunk_index so rename/move invalidates;
    includes chunk_text so body edits invalidate. Same hash ⇒ same embedding.
    """
    h = hashlib.sha256()
    h.update(chunk.file_path.encode("utf-8", errors="replace"))
    h.update(b"\0")
    h.update(chunk.qualified_name.encode("utf-8", errors="replace"))
    h.update(b"\0")
    h.update(str(chunk.chunk_index).encode("ascii"))
    h.update(b"\0")
    h.update(chunk.text.encode("utf-8", errors="replace"))
    return h.hexdigest()


def _embed_probe(llm_client, chunk: CodeChunk) -> list[float]:
    """First embedding, with retry. Surfaces config/auth errors early while
    tolerating transient network blips that would otherwise kill the phase."""
    last_exc: Exception | None = None
    for attempt in range(1, _PROBE_MAX_ATTEMPTS + 1):
        try:
            return llm_client.get_embedding(chunk.text)
        except Exception as exc:
            last_exc = exc
            if attempt < _PROBE_MAX_ATTEMPTS:
                delay = _PROBE_BACKOFF_SEC * (2 ** (attempt - 1))
                logger.warning(
                    "Embedding probe attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, _PROBE_MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)
    assert last_exc is not None  # unreachable: loop either returned or captured
    raise last_exc


def embed_chunks(
    chunks: list[CodeChunk],
    db_path: Path,
    llm_client,
    *,
    force: bool = False,
    max_workers: int = 1,
    progress_callback=None,
) -> dict:
    """Embed a list of CodeChunks into sqlite-vec, incrementally.

    Identity: each chunk has a content-addressed hash over
    ``(file_path, qualified_name, chunk_index, chunk_text)``. Hashes already
    present in the DB are kept as-is (no embedding call); hashes no longer
    present in the desired set are deleted; new hashes are embedded and
    inserted.

    This means a one-file edit re-embeds only the affected chunks, not the
    whole corpus — an important incremental-scan property.

    Resume: if every desired hash is already stored, no embedding endpoint is
    contacted and ``resumed=True`` is returned. Stored dimension is read from
    the vector blob itself, so this path survives the embedding provider
    being offline.

    Pass ``force=True`` to drop the tables and re-embed unconditionally
    (e.g. after switching embedding models, when stored dim no longer matches
    query-time dim).

    ``progress_callback(done: int, total: int)`` is invoked after each chunk
    completes, so callers can render a progress bar. When chunks are
    preserved from the DB, they count toward ``done`` immediately.

    Returns ``{"embedded": N, "dim": dim, "resumed": bool, "added": X,
    "removed": Y, "kept": Z}``. ``embedded`` is total count after the run;
    ``added``/``removed``/``kept`` are deltas.
    """
    if not chunks:
        return {"embedded": 0, "dim": 0, "resumed": False, "added": 0, "removed": 0, "kept": 0}

    desired_hashes = [_chunk_hash(c) for c in chunks]
    desired_set = set(desired_hashes)

    if force:
        existing_by_hash: dict[str, int] = {}
    else:
        existing_by_hash = _load_existing_hashes(db_path)

    to_embed_indexes = [i for i, h in enumerate(desired_hashes) if h not in existing_by_hash]
    stale_hashes = set(existing_by_hash) - desired_set
    kept = len(chunks) - len(to_embed_indexes)

    # Fast path: every desired hash is already stored and nothing is stale.
    if not to_embed_indexes and not stale_hashes and existing_by_hash:
        stored_dim = _read_stored_dim(db_path)
        logger.info(
            "RAG embed: %d vectors already indexed (dim=%d), skipping (resume)",
            len(chunks), stored_dim,
        )
        if progress_callback is not None:
            progress_callback(len(chunks), len(chunks))
        return {
            "embedded": len(chunks), "dim": stored_dim, "resumed": True,
            "added": 0, "removed": 0, "kept": len(chunks),
        }

    # Count already-stored chunks toward progress so the bar starts partway
    # full on incremental updates.
    done = kept
    if progress_callback is not None and done:
        progress_callback(done, len(chunks))

    to_embed_chunks = [chunks[i] for i in to_embed_indexes]
    new_vecs: dict[int, list[float]] = {}
    dim = 0

    if to_embed_chunks:
        probe_vec = _embed_probe(llm_client, to_embed_chunks[0])
        dim = len(probe_vec)
        new_vecs[0] = probe_vec
        done += 1
        if progress_callback is not None:
            progress_callback(done, len(chunks))

        remaining = list(enumerate(to_embed_chunks[1:], start=1))
        if remaining:
            # Prefer batch embedding when the client exposes it — one HTTP call
            # per batch instead of one per chunk. Falls back silently to the
            # per-chunk path if get_embeddings is missing or the backend raises
            # (e.g. unexpected response shape from a non-standard Ollama fork).
            batch_fn = getattr(llm_client, "get_embeddings", None)
            used_batch = False
            if callable(batch_fn):
                try:
                    for start in range(0, len(remaining), _EMBED_BATCH_SIZE):
                        group = remaining[start:start + _EMBED_BATCH_SIZE]
                        texts = [c.text for _, c in group]
                        vecs = batch_fn(texts)
                        if len(vecs) != len(group):
                            raise RuntimeError(
                                f"Batch embedding returned {len(vecs)} vectors for {len(group)} inputs"
                            )
                        for (slot, _chunk), vec in zip(group, vecs):
                            new_vecs[slot] = vec
                            done += 1
                            if progress_callback is not None:
                                progress_callback(done, len(chunks))
                    used_batch = True
                except Exception as exc:
                    logger.warning(
                        "Batch embedding failed (%s); falling back to per-chunk", exc,
                    )
                    # Reset progress for chunks that didn't actually get embedded
                    # so the per-chunk path below fills in missing slots.
                    for slot, _c in remaining:
                        if slot not in new_vecs:
                            # this slot wasn't filled by the partial batch run;
                            # it will be filled below
                            pass

            if not used_batch:
                missing = [(i, c) for i, c in remaining if i not in new_vecs]
                workers = max(1, max_workers)
                if workers == 1:
                    for i, chunk in missing:
                        new_vecs[i] = llm_client.get_embedding(chunk.text)
                        done += 1
                        if progress_callback is not None:
                            progress_callback(done, len(chunks))
                else:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    with ThreadPoolExecutor(max_workers=workers) as ex:
                        futures = {
                            ex.submit(llm_client.get_embedding, chunk.text): i
                            for i, chunk in missing
                        }
                        for fut in as_completed(futures):
                            i = futures[fut]
                            new_vecs[i] = fut.result()  # propagates first exception
                            done += 1
                            if progress_callback is not None:
                                progress_callback(done, len(chunks))
    else:
        dim = _read_stored_dim(db_path)

    # Write incrementally: drop stale rows, insert new rows, commit in
    # bounded batches so a mid-phase crash doesn't lose everything.
    conn = _get_vec_conn(db_path)
    try:
        if force:
            init_vec_tables(conn, dim, force_recreate=True)
        else:
            init_vec_tables(conn, dim, force_recreate=False)

        if stale_hashes and not force:
            _delete_by_hashes(conn, stale_hashes)
            conn.commit()

        if new_vecs:
            now = datetime.now(timezone.utc).isoformat()
            for batch_start in range(0, len(new_vecs), _WRITE_BATCH_SIZE):
                batch_end = min(batch_start + _WRITE_BATCH_SIZE, len(new_vecs))
                for local_i in range(batch_start, batch_end):
                    chunk = to_embed_chunks[local_i]
                    vec = new_vecs.get(local_i)
                    if vec is None:
                        # Should not happen — every submitted future populated
                        # its slot or raised. Explicit raise so python -O
                        # doesn't silently skip the check.
                        raise RuntimeError(
                            f"Embedding slot {local_i} for {chunk.qualified_name} "
                            "is empty; worker did not complete."
                        )
                    _insert_chunk(conn, chunk, vec, now, desired_hashes[to_embed_indexes[local_i]])
                conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM discovery_chunk_meta").fetchone()[0]
        return {
            "embedded": total, "dim": dim, "resumed": False,
            "added": len(new_vecs), "removed": len(stale_hashes), "kept": kept,
        }
    finally:
        conn.close()


def _load_existing_hashes(db_path: Path) -> dict[str, int]:
    """Return {chunk_hash: rowid} for chunks already stored. Empty dict if
    the tables don't exist yet (fresh scan)."""
    try:
        conn = _get_vec_conn(db_path)
    except Exception as exc:
        logger.warning("Could not open DB for hash lookup, treating as fresh: %s", exc)
        return {}
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='discovery_chunk_meta'"
        ).fetchone()
        if not table:
            return {}
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(discovery_chunk_meta)").fetchall()}
        if "chunk_hash" not in cols:
            # Legacy DB without hash column — can't do identity match, fall
            # back to full rebuild rather than guess.
            logger.info("Legacy RAG schema (no chunk_hash); full re-embed required.")
            return {}
        rows = conn.execute(
            "SELECT rowid, chunk_hash FROM discovery_chunk_meta WHERE chunk_hash IS NOT NULL"
        ).fetchall()
        return {r["chunk_hash"]: r["rowid"] for r in rows}
    finally:
        conn.close()


def _read_stored_dim(db_path: Path) -> int:
    """Read the vector dimension from the first stored vector blob. Returns 0
    if no vectors stored."""
    try:
        conn = _get_vec_conn(db_path)
    except Exception:
        return 0
    try:
        _load_sqlite_vec(conn)
        row = conn.execute("SELECT embedding FROM discovery_vectors LIMIT 1").fetchone()
        return len(row[0]) // 4 if row else 0
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _delete_by_hashes(conn, hashes: set[str]) -> None:
    """Delete vector+meta rows for the given hashes. Uses parameterized IN
    clause, chunked to stay under SQLite variable limit (999)."""
    hash_list = list(hashes)
    CHUNK = 500
    for start in range(0, len(hash_list), CHUNK):
        subset = hash_list[start:start + CHUNK]
        placeholders = ",".join("?" * len(subset))
        rowids = [
            r["rowid"]
            for r in conn.execute(
                f"SELECT rowid FROM discovery_chunk_meta WHERE chunk_hash IN ({placeholders})",
                subset,
            ).fetchall()
        ]
        if not rowids:
            continue
        rid_placeholders = ",".join("?" * len(rowids))
        conn.execute(
            f"DELETE FROM discovery_vectors WHERE rowid IN ({rid_placeholders})", rowids,
        )
        conn.execute(
            f"DELETE FROM discovery_chunk_meta WHERE rowid IN ({rid_placeholders})", rowids,
        )


def _insert_chunk(conn, chunk: CodeChunk, vec: list[float], now: str, chunk_hash: str) -> None:
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
            chunk_text, language, node_type, chunk_index, embedded_at, chunk_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            chunk_hash,
        ),
    )
