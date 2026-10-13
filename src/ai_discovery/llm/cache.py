"""LLM response cache (roadmap item M1.11.b, ADR-0002).

A repeated call — same provider, model, role, output cap, system prefix and
prompt (and schema, for structured calls) — is answered from the cache at $0.
The key is a sha256 over those fields; the prompt text carries the page facts
and the prompt template, so a changed fact or template is a new key without a
separate version number. ``prompt_id`` records what kind of call it was.

Only complete answers are stored: a failed or truncated call never writes the
cache (ADR-0002).

Storage: the ``llm_cache`` schema from migration 0002, in its own SQLite file
``<state>/cache/llm_cache.db`` rather than the scan DB. Self-review calls the
LLM while RAG search holds the scan DB open through ``pysqlite3``; a second
library opening the same WAL file at the same time clobbers its locks (audit
#87). A separate file avoids that and lets answers outlive scan snapshot GC.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """CREATE TABLE IF NOT EXISTS llm_cache (
    key             TEXT PRIMARY KEY,
    role            TEXT,
    model_id        TEXT,
    prompt_id       TEXT,
    prompt_version  TEXT,
    output          TEXT NOT NULL,
    usage_json      TEXT,
    created_at      TEXT NOT NULL
)"""


def cache_key(*, provider: str, model_id: str, role: str, prompt: str, max_tokens: int,
              system: str = "", kind: str = "text", schema: Optional[dict] = None) -> str:
    payload = json.dumps({"provider": provider, "model": model_id, "role": role, "kind": kind,
                          "max_tokens": max_tokens, "system": system, "prompt": prompt,
                          "schema": schema}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CachedAnswer:
    output: Any
    usage: dict


class ResponseCache:
    """Thread-safe read-through cache; one short-lived connection per operation."""

    def __init__(self, path: Path, *, read: bool = True):
        self.path = Path(path)
        self.read = read
        self.hits = 0
        self.writes = 0
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, key: str) -> Optional[CachedAnswer]:
        if not self.read:
            return None
        conn = self._connect()
        try:
            row = conn.execute("SELECT output, usage_json FROM llm_cache WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        with self._lock:
            self.hits += 1
        return CachedAnswer(json.loads(row[0]), json.loads(row[1] or "{}"))

    def put(self, key: str, output: Any, *, role: str, model_id: str, kind: str, usage: dict) -> None:
        from ..db import now_iso

        conn = self._connect()
        try:
            conn.execute("INSERT OR REPLACE INTO llm_cache (key, role, model_id, prompt_id, prompt_version, "
                         "output, usage_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (key, role, model_id, kind, "content", json.dumps(output, ensure_ascii=False),
                          json.dumps(usage), now_iso()))
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self.writes += 1
