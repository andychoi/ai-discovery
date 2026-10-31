"""Hybrid search: page FTS5 + symbol names + vectors, fused with RRF (M5.1).

Each retriever produces a ranked list of ids; Reciprocal Rank Fusion
(``Σ 1 / (k + rank)``, k = 60) merges them, so no retriever's raw score scale
dominates. Vectors need a query-side embedder: without one (keyless installs)
the vector list is skipped and, if a vector store exists, ``vector_search_skipped``
is reported in ``_meta.degraded`` instead of silently returning fewer hits.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Optional

from .answer import Answer, not_indexed
from .session import Project, Reader

RRF_K = 60
MAX_LIMIT = 50
_PER_LIST = 50

_MD_LINK_TARGET = re.compile(r"\]\([^)\s…]*(?:\)|(?=…)|$)")
_MD_NOISE = re.compile(r"(\*\*|__|`|^#+\s|\s#+\s|-{3,}|[|\[\]])", re.MULTILINE)


def plain_snippet(raw: Optional[str]) -> str:
    """FTS snippet → plain text, matches wrapped in ``[...]`` (markdown syntax stripped first)."""
    if not raw:
        return ""
    text = _MD_NOISE.sub(" ", _MD_LINK_TARGET.sub("", raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("\x02", "[").replace("\x03", "]")


def _tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"\w+", q, flags=re.UNICODE) if t]


def _fts(r: Reader, q: str, types: list[str]) -> list[dict]:
    from ..store.fts import fts5_available

    tokens = _tokens(q)
    if not tokens:
        return []
    clause = f" AND p.type IN ({','.join('?' * len(types))})" if types else ""
    if fts5_available(r.conn) and r.has_table("pages_fts"):
        match = " ".join(f'"{t}"*' for t in tokens)  # quoted: user input can't inject FTS syntax
        rows = r.conn.execute(
            "SELECT f.page_id, p.title, p.type, snippet(pages_fts, 3, char(2), char(3), '…', 12) AS snip, "
            "bm25(pages_fts, 0, 0, 10.0, 1.0, 2.0) AS score FROM pages_fts f JOIN pages p "
            f"ON p.page_id = f.page_id AND p.scan_id = f.scan_id WHERE pages_fts MATCH ? AND f.scan_id = ?{clause} "
            "ORDER BY lower(p.title) = lower(?) DESC, score LIMIT ?",
            [match, r.scan_id, *types, q.strip(), _PER_LIST]).fetchall()
        return [{"id": x["page_id"], "type": x["type"], "title": x["title"], "snippet": plain_snippet(x["snip"])}
                for x in rows]
    like = "%" + "%".join(tokens) + "%"
    rows = r.conn.execute(f"SELECT page_id, title, type FROM pages p WHERE scan_id = ?{clause} "
                          "AND (title LIKE ? OR body_md LIKE ?) ORDER BY title LIMIT ?",
                          [r.scan_id, *types, like, like, _PER_LIST]).fetchall()
    return [{"id": x["page_id"], "type": x["type"], "title": x["title"], "snippet": ""} for x in rows]


def _symbols(r: Reader, q: str) -> list[dict]:
    """Code symbols by name: exact name, then qualified-name suffix, then substring."""
    needle = q.strip()
    if not needle or len(needle) > 200:
        return []
    low = needle.lower()
    rows = r.conn.execute(
        "SELECT qualified_name, node_type, file_path, line_start, "
        "CASE WHEN lower(name) = ? OR lower(qualified_name) = ? THEN 0 "
        "     WHEN lower(qualified_name) LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END AS tier "
        "FROM code_nodes WHERE scan_id = ? AND instr(lower(qualified_name), ?) > 0 "
        "GROUP BY qualified_name ORDER BY tier, length(qualified_name), qualified_name LIMIT ?",
        (low, low, "%." + low.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"),
         r.scan_id, low, _PER_LIST)).fetchall()
    return [{"id": f"symbol:{x['qualified_name']}", "type": "symbol", "title": x["qualified_name"],
             "snippet": "", "path": r.rel(x["file_path"]), "line": x["line_start"], "kind": x["node_type"]}
            for x in rows]


def _vectors(r: Reader, q: str) -> tuple[list[dict], Optional[str]]:
    if not r.has_table("discovery_chunk_meta"):
        return [], None
    if r.project.embed is None:
        return [], "vector_search_skipped"
    try:
        from ..rag.retriever import search as vec_search

        class _Client:  # the retriever expects an object with get_embedding()
            get_embedding = staticmethod(r.project.embed)

        rows = vec_search(q, r.project.layout.db_path, _Client(), top_k=_PER_LIST)
    except (ImportError, sqlite3.Error, RuntimeError, ValueError) as exc:  # sqlite-vec missing, dim mismatch…
        return [], f"vector_search_failed:{type(exc).__name__}"
    return [{"id": f"symbol:{x['qualified_name']}", "type": "symbol", "title": x["qualified_name"],
             "snippet": (x.get("chunk_text") or "")[:200], "path": r.rel(x.get("file_path"))} for x in rows], None


def search(project: Project, query: str, *, kinds: Optional[list[str]] = None, limit: int = 20) -> dict:
    """Hybrid search over pages and symbols. ``kinds`` = page types and/or ``"symbol"``."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    kinds = [k for k in (kinds or []) if k]
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        page_types = [k for k in kinds if k != "symbol"]
        want_pages = not kinds or bool(page_types)
        want_symbols = not kinds or "symbol" in kinds
        lists: dict[str, list[dict]] = {}
        degraded: list[str] = []
        if want_pages:
            lists["fts"] = _fts(r, query, page_types)
        if want_symbols:
            lists["name"] = _symbols(r, query)
            vec, note = _vectors(r, query)
            lists["vector"] = vec
            if note:
                degraded.append(note)
        fused: dict[str, dict] = {}
        for source, hits in lists.items():
            for rank, hit in enumerate(hits, 1):
                cur = fused.setdefault(hit["id"], {**hit, "score": 0.0, "sources": []})
                cur["score"] += 1.0 / (RRF_K + rank)
                cur["sources"].append(source)
                for k in ("snippet", "path", "line", "kind"):  # first non-empty value wins
                    if not cur.get(k) and hit.get(k):
                        cur[k] = hit[k]
        exact = query.strip().lower()
        ans = Answer()
        hits = ans.cap("hits", fused.values(), limit,
                       key=lambda h: (h["title"].lower() != exact and h["title"].lower().split(".")[-1] != exact,
                                      -h["score"], h["id"]))
        for h in hits:
            h["score"] = round(h["score"], 5)
        ans.data = {"query": query, "hits": hits}
        ans.meta = r.meta(files=[h.get("path") for h in hits], untrusted=["hits[].snippet"], degraded=degraded)
        return ans.to_dict()
