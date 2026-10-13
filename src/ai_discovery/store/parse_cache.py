"""Per-file parse cache (roadmap item M1.9, ADR-0002).

Key: ``(repo-relative path, sha256 of the file bytes, parser version, encoding
decision)``. The parser version is a hash of the parser package sources, the
node model and the decoder, so editing any of them invalidates every entry
without a manual version bump. Payload: the file's ``CodeNode`` list as JSON
(never pickle — ``.discovery/`` may be shared, and loading it must not run code).

Only parse output is cached; the call graph is fully re-resolved every scan.
A file is cached only when its nodes survive a JSON round trip unchanged;
otherwise it is simply re-parsed next time.
"""

from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
from dataclasses import asdict, fields
from pathlib import Path
from typing import Optional

_PKG = Path(__file__).resolve().parent.parent  # src/ai_discovery
_VERSION_SOURCES = ("parsers", "graph/models.py", "core/encoding.py")


@functools.lru_cache(maxsize=1)
def parser_version() -> str:
    """Hash of every source that shapes parse output (parsers, node model, decoder)."""
    h = hashlib.sha256()
    for rel in _VERSION_SOURCES:
        root = _PKG / rel
        paths = sorted(root.rglob("*")) if root.is_dir() else [root]
        for p in paths:
            if p.is_file() and p.suffix in (".py", ".scm"):
                h.update(p.relative_to(_PKG).as_posix().encode())
                h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _node_fields() -> list[str]:
    from ..graph.models import CodeNode

    return [f.name for f in fields(CodeNode)]


def encode_nodes(nodes: list, abs_path: str) -> Optional[str]:
    """Nodes → JSON (file_path made relative), or None if they don't round-trip exactly."""
    out = []
    for n in nodes:
        d = asdict(n)
        if d["file_path"] == abs_path:
            d["file_path"] = None          # rebased to the current location on load
        out.append(d)
    try:
        text = json.dumps(out, ensure_ascii=False)   # keep key order: cached == freshly parsed
    except (TypeError, ValueError):
        return None
    if json.loads(text) != out:            # tuples, sets, non-str keys … → not cacheable
        return None
    return text


def decode_nodes(text: str, abs_path: str) -> list:
    from ..graph.models import CodeNode

    allowed = set(_node_fields())
    nodes = []
    for d in json.loads(text):
        d = {k: v for k, v in d.items() if k in allowed}
        if d.get("file_path") is None:
            d["file_path"] = abs_path
        nodes.append(CodeNode(**d))
    return nodes


class ParseCache:
    """Read-through cache over ``file_parse_cache`` for one scan."""

    def __init__(self, db_path: Path, *, read: bool = True):
        self.db_path = Path(db_path)
        self.version = parser_version()
        self.read = read
        self.hits = 0
        self.misses = 0
        self._entries: dict[str, tuple[str, str, str]] = {}
        if read:
            self._entries = self._load()

    def _load(self) -> dict[str, tuple[str, str, str]]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT path, content_hash, encoding, payload FROM file_parse_cache "
                                "WHERE parser_version = ?", (self.version,)).fetchall()
        except sqlite3.OperationalError:   # pre-0002 DB: no cache
            return {}
        finally:
            conn.close()
        return {r[0]: (r[1], r[2], r[3] if isinstance(r[3], str) else bytes(r[3]).decode("utf-8")) for r in rows}

    def get(self, rel_path: str, content_hash: str, encoding: str, abs_path: str) -> Optional[list]:
        entry = self._entries.get(rel_path) if self.read else None
        if entry and entry[0] == content_hash and entry[1] == encoding:
            self.hits += 1
            return decode_nodes(entry[2], abs_path)
        self.misses += 1
        return None

    def put_many(self, items: list[tuple[str, str, str, str]]) -> int:
        """Store ``(rel_path, content_hash, encoding, payload_json)``; older entries for a path are dropped."""
        if not items:
            return 0
        from ..db import now_iso

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany("DELETE FROM file_parse_cache WHERE path = ?", [(i[0],) for i in items])
            conn.executemany(
                "INSERT OR REPLACE INTO file_parse_cache (path, content_hash, parser_version, encoding, "
                "payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(p, h, self.version, enc, payload, now_iso()) for p, h, enc, payload in items])
            conn.commit()
        except sqlite3.OperationalError:   # pre-0002 DB: run uncached
            return 0
        finally:
            conn.close()
        return len(items)
