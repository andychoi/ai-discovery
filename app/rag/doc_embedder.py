"""Embed generated markdown docs into discovery_doc_vectors for dual RAG retrieval."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .embedder import _get_vec_conn, _load_sqlite_vec, _serialize_f32


def init_doc_vec_tables(conn, dim: int, force_recreate: bool = False) -> None:
    _load_sqlite_vec(conn)
    if force_recreate:
        conn.execute("DROP TABLE IF EXISTS discovery_doc_vectors")
        conn.execute("DROP TABLE IF EXISTS discovery_doc_chunk_meta")
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS discovery_doc_vectors USING vec0(
            embedding float[{dim}]
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovery_doc_chunk_meta (
            rowid          INTEGER PRIMARY KEY,
            doc_id         TEXT NOT NULL,
            doc_type       TEXT NOT NULL,
            domain         TEXT,
            section_heading TEXT,
            chunk_text     TEXT,
            embedded_at    TEXT NOT NULL
        )
    """)
    conn.commit()


def _chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, content) pairs on ## headings.

    Returns list of (section_heading, chunk_text) where chunk_text includes
    the heading line. Frontmatter (--- block) is treated as one chunk.
    """
    chunks: list[tuple[str, str]] = []

    # Frontmatter block
    fm_match = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
    if fm_match:
        chunks.append(("frontmatter", fm_match.group(0).strip()))
        text = text[fm_match.end():]

    # Split on ## headings (keep delimiter)
    parts = re.split(r"(?=^## )", text, flags=re.MULTILINE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        heading_match = re.match(r"^## (.+)", part)
        heading = heading_match.group(1).strip() if heading_match else "intro"
        chunks.append((heading, part))

    return chunks


def embed_docs(docs_dir: Path, db_path: Path, llm_client) -> dict:
    """Embed all markdown docs under docs_dir into discovery_doc_vectors.

    Scans {docs_dir}/**/*.md, chunks each file by ## section, embeds each chunk.
    Resume guard: if the number of already-indexed doc_ids matches the number of
    markdown files found, skips re-embedding (same pattern as embed_chunks).
    Returns {"embedded": N, "files": F, "dim": dim, "resumed": bool}.
    """
    md_files = list(docs_dir.rglob("*.md"))
    if not md_files:
        return {"embedded": 0, "files": 0, "dim": 0, "resumed": False}

    # Resume guard: skip if BOTH tables exist and are fully populated
    try:
        conn = _get_vec_conn(db_path)
        try:
            _load_sqlite_vec(conn)
            # Both the meta table AND the vector table must exist and have data
            meta_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='discovery_doc_chunk_meta'"
            ).fetchone()
            vec_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='discovery_doc_vectors'"
            ).fetchone()
            if meta_exists and vec_exists:
                existing_docs = conn.execute(
                    "SELECT COUNT(DISTINCT doc_id) FROM discovery_doc_chunk_meta"
                ).fetchone()[0]
                vec_count = conn.execute(
                    "SELECT COUNT(*) FROM discovery_doc_chunk_meta"
                ).fetchone()[0]
                if existing_docs >= len(md_files) and vec_count > 0:
                    import logging
                    logging.getLogger(__name__).info(
                        "RAG doc embed: %d docs already indexed, skipping (resume)", existing_docs
                    )
                    return {"embedded": vec_count, "files": existing_docs, "dim": 0, "resumed": True}
        except Exception:
            pass
        finally:
            conn.close()
    except Exception:
        pass

    # Parse doc metadata from file path: {docs_dir}/{doc_type}/{doc_id}.md
    doc_records: list[dict] = []
    for f in md_files:
        doc_type = f.parent.name
        doc_id = f.stem
        # Infer domain from doc_id: strip project prefix + doc_type suffix
        # e.g. "myproj-auth-as-is" → domain hint is middle segment(s)
        text = f.read_text(encoding="utf-8")
        # Extract domain from frontmatter if present
        domain_match = re.search(r"^domain:\s*(.+)$", text, re.MULTILINE)
        domain = domain_match.group(1).strip() if domain_match else doc_type
        doc_records.append({"doc_id": doc_id, "doc_type": doc_type, "domain": domain, "text": text, "path": f})

    # Probe embedding dim
    probe_chunks = _chunk_markdown(doc_records[0]["text"])
    probe_text = probe_chunks[0][1] if probe_chunks else doc_records[0]["text"][:500]
    probe_vec = llm_client.get_embedding(probe_text)
    dim = len(probe_vec)

    # Build all (doc, heading, text) records first — no DB connection held during API calls
    all_chunks: list[tuple[dict, str, str]] = []
    first_doc = doc_records[0]
    first_heading, _ = probe_chunks[0] if probe_chunks else ("intro", "")
    all_chunks.append((first_doc, first_heading, probe_text))
    for chunk_heading, chunk_text in probe_chunks[1:]:
        all_chunks.append((first_doc, chunk_heading, chunk_text))
    for doc in doc_records[1:]:
        for heading, chunk_text in _chunk_markdown(doc["text"]):
            all_chunks.append((doc, heading, chunk_text))

    # Embed all chunks (API calls outside any DB transaction)
    vecs: list[list[float]] = [probe_vec]
    for _, _, chunk_text in all_chunks[1:]:
        vecs.append(llm_client.get_embedding(chunk_text))

    # Write to DB in one short transaction — lock held only during fast inserts
    conn = _get_vec_conn(db_path)
    try:
        init_doc_vec_tables(conn, dim, force_recreate=True)
        now = datetime.now(timezone.utc).isoformat()
        for (doc, heading, chunk_text), vec in zip(all_chunks, vecs):
            _insert_doc_chunk(conn, doc, heading, chunk_text, vec, now)
        conn.commit()
        return {"embedded": len(all_chunks), "files": len(doc_records), "dim": dim, "resumed": False}
    finally:
        conn.close()


def _insert_doc_chunk(conn, doc: dict, heading: str, text: str, vec: list[float], now: str) -> None:
    vec_bytes = _serialize_f32(vec)
    cursor = conn.execute(
        "INSERT INTO discovery_doc_vectors (embedding) VALUES (?)", (vec_bytes,)
    )
    rid = cursor.lastrowid
    conn.execute(
        """INSERT INTO discovery_doc_chunk_meta
           (rowid, doc_id, doc_type, domain, section_heading, chunk_text, embedded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rid, doc["doc_id"], doc["doc_type"], doc["domain"], heading, text, now),
    )


def search_docs(
    query_vec: list[float],
    db_path: Path,
    top_k: int = 5,
) -> list[dict]:
    """Search discovery_doc_vectors. Returns top_k chunk dicts."""
    conn = _get_vec_conn(db_path)
    try:
        _load_sqlite_vec(conn)
        vec_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='discovery_doc_vectors'"
        ).fetchone()
        if not vec_exists:
            return []
        rows = conn.execute(
            """SELECT v.distance, m.doc_id, m.doc_type, m.domain,
                      m.section_heading, m.chunk_text
               FROM discovery_doc_vectors v
               JOIN discovery_doc_chunk_meta m ON m.rowid = v.rowid
               WHERE v.embedding MATCH ?
                 AND k = ?
               ORDER BY v.distance
               LIMIT ?""",
            (_serialize_f32(query_vec), top_k, top_k),
        ).fetchall()
        return [
            {
                "source": "doc",
                "doc_id": r["doc_id"],
                "doc_type": r["doc_type"],
                "domain": r["domain"],
                "section_heading": r["section_heading"],
                "chunk_text": r["chunk_text"],
                "distance": r["distance"],
            }
            for r in rows
        ]
    finally:
        conn.close()
