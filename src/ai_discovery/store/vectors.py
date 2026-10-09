"""Embedder pinning (roadmap item M1.5).

A vector store must never mix embeddings from different models: same-length
vectors from two models are silently incomparable. ``vec_meta`` records the
model and dimension each store was built with; a mismatch means "reindex".
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


class EmbedderMismatch(RuntimeError):
    def __init__(self, store: str, pinned: tuple[str, int], requested: tuple[str, int]):
        self.store, self.pinned, self.requested = store, pinned, requested
        super().__init__(
            f"vector store {store!r} was built with {pinned[0]} (dim {pinned[1]}), "
            f"not {requested[0]} (dim {requested[1]}); a reindex is required"
        )


def pinned_embedder(conn: sqlite3.Connection, store: str) -> tuple[str, int] | None:
    try:
        row = conn.execute("SELECT embed_model_id, dim FROM vec_meta WHERE store = ?", (store,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None  # pre-0002 DB
        raise
    return (row[0], int(row[1])) if row else None


def check_embedder(conn: sqlite3.Connection, store: str, model_id: str, dim: int | None = None) -> None:
    """Raise EmbedderMismatch if *store* is pinned to another model (or dim)."""
    pinned = pinned_embedder(conn, store)
    if pinned is None:
        return
    if pinned[0] != model_id or (dim is not None and pinned[1] != dim):
        raise EmbedderMismatch(store, pinned, (model_id, dim if dim is not None else pinned[1]))


def pin_embedder(conn: sqlite3.Connection, store: str, model_id: str, dim: int) -> None:
    """Record the model/dim *store* now holds (call after a full (re)build)."""
    conn.execute(
        "INSERT INTO vec_meta (store, embed_model_id, dim, pinned_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(store) DO UPDATE SET embed_model_id = excluded.embed_model_id, "
        "dim = excluded.dim, pinned_at = excluded.pinned_at",
        (store, model_id, dim, datetime.now(timezone.utc).isoformat()),
    )


def unpin_embedder(conn: sqlite3.Connection, store: str) -> None:
    conn.execute("DELETE FROM vec_meta WHERE store = ?", (store,))
