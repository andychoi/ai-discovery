"""API v1 (roadmap item M4.1, design/wiki-web.md §3).

Every response is the ``Answer`` envelope::

    {"status": "ok"|"not_found"|"not_indexed"|"unknown", "data": {...}, "totals": {...},
     "truncated": bool, "omitted": [...], "meta": {...}}

Domain outcomes (not found, not indexed) are HTTP 200 with a non-"ok" status;
HTTP 4xx/5xx are reserved for protocol errors (bad parameters, forbidden).
Reads go through a read-only connection and only ever see ``current_scan``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

CONTRACT_VERSION = "1"
MAX_LIST = 1000
MAX_GRAPH_NODES = 500
_META_TTL = 5.0


class NoteIn(BaseModel):
    author: Optional[str] = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


# ── plumbing ──────────────────────────────────────────────────────────────

def _answer(data: Any = None, *, status: str = "ok", totals: Optional[dict] = None, truncated: bool = False,
            omitted: Optional[list] = None, meta: Optional[dict] = None) -> dict:
    return {"status": status, "data": data if data is not None else {}, "totals": totals or {},
            "truncated": truncated, "omitted": omitted or [], "meta": meta or {}}


@contextmanager
def _ro(layout) -> Iterator[Optional[sqlite3.Connection]]:
    if not layout.db_path.exists():
        yield None
        return
    conn = sqlite3.connect(f"file:{layout.db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _current_scan(conn: sqlite3.Connection) -> Optional[int]:
    from ..store.scans import get_current_scan

    return get_current_scan(conn)


def _git(repo: Optional[Path], *args: str) -> Optional[str]:
    if repo is None:
        return None
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


_meta_cache: dict[tuple, tuple[float, dict]] = {}


def _meta(request: Request, conn: Optional[sqlite3.Connection], scan_id: Optional[int]) -> dict:
    layout = request.app.state.layout
    key = (str(layout.db_path), scan_id)
    hit = _meta_cache.get(key)
    if hit and time.monotonic() - hit[0] < _META_TTL:
        return hit[1]
    meta: dict[str, Any] = {"scan_id": scan_id, "contract_version": CONTRACT_VERSION, "degraded": []}
    if conn is not None and scan_id is not None:
        row = conn.execute("SELECT commit_sha FROM scan_runs WHERE id = ?", (scan_id,)).fetchone()
        indexed = row[0] if row else None
        head = _git(layout.repo, "rev-parse", "HEAD")
        behind = None
        if indexed and head and indexed != head:
            n = _git(layout.repo, "rev-list", "--count", f"{indexed}..HEAD")
            behind = int(n) if n and n.isdigit() else None
        elif indexed and head:
            behind = 0
        from ..pages.build import stale_pages

        meta.update(indexed_commit=indexed, head_commit=head, behind_by=behind,
                    dirty=bool(_git(layout.repo, "status", "--porcelain")),
                    stale_pages=len(stale_pages(conn, scan_id, layout.repo)) if layout.repo else 0)
        from ..store.fts import fts5_available
        if not fts5_available(conn):
            meta["degraded"].append("fts_unavailable")
    _meta_cache[key] = (time.monotonic(), meta)
    return meta


def _ctx(request: Request):
    """(layout, conn context) for handlers."""
    return request.app.state.layout


def _not_indexed(request: Request) -> dict:
    return _answer({"hint": "run `discover scan`"}, status="not_indexed",
                   meta={"scan_id": None, "contract_version": CONTRACT_VERSION, "degraded": []})


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


# ── router ────────────────────────────────────────────────────────────────

_MD_LINK_TARGET = re.compile(r"\]\([^)\s…]*(?:\)|(?=…)|$)")  # also a target cut off by the snippet
_MD_NOISE = re.compile(r"(\*\*|__|`|^#+\s|\s#+\s|-{3,}|[|\[\]])", re.MULTILINE)


def _plain_snippet(raw: Optional[str]) -> str:
    """FTS snippet → plain text with matches in ``[...]``.

    Bodies are markdown, so the raw snippet is full of ``[text](page:id)``,
    ``**``, backticks and table pipes; those are stripped first, so the only
    brackets left in the result are the match markers (FTS emits \\x02/\\x03).
    """
    if not raw:
        return ""
    text = _MD_NOISE.sub(" ", _MD_LINK_TARGET.sub("", raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("\x02", "[").replace("\x03", "]")


def build_router() -> APIRouter:
    r = APIRouter()

    @r.get("/status")
    def status(request: Request):
        layout = _ctx(request)
        with _ro(layout) as conn:
            if conn is None:
                return _not_indexed(request)
            sid = _current_scan(conn)
            scans = [
                {"scan_id": row["id"], "commit": row["commit_sha"], "status": row["status"],
                 "started_at": row["started_at"], "ended_at": row["finished_at"],
                 "spend_usd": round(conn.execute("SELECT COALESCE(SUM(est_usd),0) FROM llm_costs WHERE scan_id=?",
                                                 (row["id"],)).fetchone()[0] or 0, 4)}
                for row in conn.execute("SELECT id, commit_sha, status, started_at, finished_at FROM scan_runs "
                                        "ORDER BY id DESC LIMIT 20")]
            stages = [{"stage": row["phase_name"], "status": row["status"], "fingerprint": ""}
                      for row in conn.execute("SELECT phase_name, status FROM phase_checkpoints WHERE scan_id = ? "
                                              "ORDER BY phase_num", (sid,))] if sid else []
            degradations = [{"stage": row["stage"], "kind": row["kind"], "count": row["count"]}
                            for row in conn.execute("SELECT stage, kind, count FROM degradations WHERE scan_id = ? "
                                                    "ORDER BY stage, kind", (sid,))] if sid else []
            enc = conn.execute("SELECT COUNT(DISTINCT file_path) FROM code_nodes WHERE scan_id = ? AND "
                               "framework_hints LIKE '%source_encoding%'", (sid,)).fetchone()[0] if sid else 0
            if enc:
                degradations.append({"stage": "parse", "kind": "encoding_fallback", "count": enc})
            pages = conn.execute("SELECT COUNT(*) FROM pages WHERE scan_id = ?", (sid,)).fetchone()[0] if sid else 0
            data = {"project": {"slug": layout.slug, "repo": str(layout.repo) if layout.repo else None,
                                "state_dir": str(layout.state_dir)},
                    "scans": scans, "stages": stages, "degradations": degradations, "pages": pages}
            return _answer(data, status="ok" if sid else "not_indexed", meta=_meta(request, conn, sid))

    @r.get("/pages")
    def pages(request: Request, type: Optional[str] = None, prefix: Optional[str] = None,
              limit: int = Query(100, ge=1, le=MAX_LIST), offset: int = Query(0, ge=0)):
        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            where, args = ["scan_id = ?"], [sid]
            if type:
                where.append("type = ?")
                args.append(type)
            if prefix:
                where.append("page_id LIKE ? ESCAPE '\\'")
                args.append(prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            clause = " AND ".join(where)
            total = conn.execute(f"SELECT COUNT(*) FROM pages WHERE {clause}", args).fetchone()[0]
            items = [dict(row) for row in conn.execute(
                f"SELECT page_id, type, title, confidence, freshness FROM pages WHERE {clause} "
                "ORDER BY type, title, page_id LIMIT ? OFFSET ?", [*args, limit, offset])]
            return _answer({"items": items}, totals={"items": total},
                           truncated=offset + len(items) < total, meta=_meta(request, conn, sid))

    @r.get("/pages/{page_id:path}")
    def page(request: Request, page_id: str):
        from ..pages.build import stale_pages
        from ..pages.model import backlinks, load_page
        from ..pages.notes import notes_for, resolve

        layout = _ctx(request)
        with _ro(layout) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            p = load_page(conn, sid, page_id)
            meta = _meta(request, conn, sid)
            if p is None:
                try:
                    successor = resolve(conn, page_id)
                except ValueError:
                    successor = page_id
                data = {"page_id": page_id}
                if successor != page_id:
                    data["successor"] = successor
                return _answer(data, status="not_found", meta=meta)
            stale = stale_pages(conn, sid, layout.repo) if layout.repo else {}
            data = {
                "page_id": p.page_id, "type": p.type, "title": p.title, "body_md": p.body_md(), "facts": p.facts,
                "links": [{"dst": link.dst, "kind": link.kind} for link in p.links],
                "backlinks": [{"src": b.dst, "kind": b.kind} for b in backlinks(conn, sid, p.page_id)],
                "citations": [{"path": c.path, "line_start": c.line_start, "line_end": c.line_end} for c in p.citations],
                "confidence": p.confidence,
                "freshness": "stale" if p.page_id in stale else p.freshness,
                "stale_reason": stale.get(p.page_id),
                "stamp": p.stamp.__dict__ if p.stamp else None,
                "notes": [n.__dict__ for n in notes_for(conn, p.page_id)],
            }
            return _answer(data, meta=meta)

    @r.post("/pages/{page_id:path}/notes")
    def add_page_note(request: Request, page_id: str, note: NoteIn):
        from ..db import get_conn
        from ..pages.model import PageError, split_page_id
        from ..pages.notes import add_note

        app = request.app
        if app.state.allow_remote:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {app.state.token}":
                raise HTTPException(status_code=401, detail="a bearer token is required for writes")
        try:
            split_page_id(page_id)
        except PageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        layout = app.state.layout
        if not layout.db_path.exists():
            return _not_indexed(request)
        conn = get_conn(layout.db_path)
        try:
            note_id = add_note(conn, page_id, note.body, author=note.author)
            conn.commit()
        finally:
            conn.close()
        return _answer({"note_id": note_id, "page_id": page_id})

    @r.get("/tree")
    def tree(request: Request):
        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            rows = conn.execute("SELECT page_id, type, title FROM pages WHERE scan_id = ? ORDER BY type, title, page_id",
                                (sid,)).fetchall()
            parent_of: dict[str, str] = {}
            for src, dst, kind in conn.execute("SELECT src, dst, kind FROM page_links WHERE scan_id = ? AND "
                                               "kind IN ('child', 'parent')", (sid,)):
                child, parent = (dst, src) if kind == "child" else (src, dst)
                if child.split(":", 1)[0] in ("module", "file") and parent.startswith("module:"):
                    parent_of.setdefault(child, parent)
            nodes = {row["page_id"]: {"page_id": row["page_id"], "title": row["title"], "children": []}
                     for row in rows}
            out: dict[str, list] = defaultdict(list)
            for row in rows:
                pid, ptype = row["page_id"], row["type"]
                if ptype in ("module", "file"):
                    parent = parent_of.get(pid)
                    if parent and parent in nodes and parent != pid:
                        nodes[parent]["children"].append(nodes[pid])
                        continue
                    out["module"].append(nodes[pid])
                elif ptype != "symbol":  # symbols are reached through files
                    out[ptype].append(nodes[pid])
            return _answer(dict(sorted(out.items())), totals={"pages": len(rows)}, meta=_meta(request, conn, sid))

    @r.get("/search")
    def search(request: Request, q: str = Query(..., min_length=1, max_length=200),
               kinds: Optional[str] = None, limit: int = Query(20, ge=1, le=100)):
        from ..store.fts import fts5_available

        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            types = [k.strip() for k in kinds.split(",")] if kinds else []
            type_clause = f" AND p.type IN ({','.join('?' * len(types))})" if types else ""
            meta = _meta(request, conn, sid)
            tokens = [t for t in re.findall(r"\w+", q, flags=re.UNICODE) if t]
            if not tokens:
                return _answer({"hits": []}, totals={"hits": 0}, meta=meta)
            if fts5_available(conn) and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'pages_fts'").fetchone():
                match = " ".join(f'"{t}"*' for t in tokens)  # quoted: user input can't inject FTS syntax
                rows = conn.execute(
                    "SELECT f.page_id, p.title, p.type, snippet(pages_fts, 3, char(2), char(3), '…', 12) AS snip, "
                    # title hits outweigh body/facts hits; an exact title match always ranks first
                    "bm25(pages_fts, 0, 0, 10.0, 1.0, 2.0) AS score FROM pages_fts f JOIN pages p "
                    "ON p.page_id = f.page_id "
                    f"AND p.scan_id = f.scan_id WHERE pages_fts MATCH ? AND f.scan_id = ?{type_clause} "
                    "ORDER BY lower(p.title) = lower(?) DESC, score LIMIT ?",
                    [match, sid, *types, q.strip(), limit]).fetchall()
                hits = [{"page_id": row["page_id"], "title": row["title"], "type": row["type"],
                         "snippet": _plain_snippet(row["snip"]), "score": round(-row["score"], 4), "source": "fts"} for row in rows]
            else:
                like = "%" + "%".join(tokens) + "%"
                rows = conn.execute(f"SELECT page_id, title, type FROM pages p WHERE scan_id = ?{type_clause} "
                                    "AND (title LIKE ? OR body_md LIKE ?) ORDER BY title LIMIT ?",
                                    [sid, *types, like, like, limit]).fetchall()
                hits = [{"page_id": row["page_id"], "title": row["title"], "type": row["type"], "snippet": "",
                         "score": 0, "source": "like"} for row in rows]
            return _answer({"hits": hits}, totals={"hits": len(hits)}, truncated=len(hits) == limit, meta=meta)

    @r.get("/source")
    def source(request: Request, path: str = Query(..., min_length=1, max_length=1024),
               start: int = Query(1, ge=1), end: Optional[int] = Query(None, ge=1),
               context: int = Query(3, ge=0, le=50)):
        """Lines of a file the current scan's pages cite (M4.3 source view).

        Only paths that appear in some page citation are readable, and they are
        resolved inside the repo — arbitrary files can never be fetched.
        """
        layout = _ctx(request)
        with _ro(layout) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            meta = _meta(request, conn, sid)
            needle = json.dumps(path)[1:-1]
            cited = conn.execute(
                "SELECT 1 FROM pages WHERE scan_id = ? AND instr(citations_json, ?) > 0 LIMIT 1",
                (sid, f'"path":"{needle}"')).fetchone()
            repo = layout.repo.resolve() if layout.repo else None
            target = (repo / path).resolve() if repo else None
            if not cited or repo is None or target is None or repo not in target.parents or not target.is_file():
                return _answer({"path": path}, status="not_found", meta=meta)
            from ..core.encoding import read_source

            lines = read_source(target).text.splitlines()
            last = min(len(lines), end or start)
            first = max(1, start - context)
            stop = min(len(lines), last + context)
            data = {"path": path, "start": start, "end": last, "first_line": first,
                    "lines": lines[first - 1:stop], "total_lines": len(lines)}
            return _answer(data, meta=meta)

    # ── call graph ────────────────────────────────────────────────────────

    def _graph(conn, sid):
        nodes = {row["id"]: row for row in conn.execute(
            "SELECT id, name, qualified_name, node_type, file_path, line_start FROM code_nodes WHERE scan_id = ?",
            (sid,))}
        edges = conn.execute("SELECT caller_id, callee_id, edge_type, resolved_by, confidence FROM call_edges "
                             "WHERE scan_id = ? AND callee_id IS NOT NULL", (sid,)).fetchall()
        return nodes, edges

    def _find_node(conn, sid, ident: str) -> Optional[int]:
        key = ident.split(":", 1)[1] if ident.startswith("symbol:") else ident
        key = key.split("@", 1)[0]
        row = conn.execute("SELECT id FROM code_nodes WHERE scan_id = ? AND (qualified_name = ? OR CAST(id AS TEXT) = ?) "
                           "ORDER BY id LIMIT 1", (sid, key, key)).fetchone()
        return row[0] if row else None

    def _node_json(row, repo: Optional[Path]) -> dict:
        from ..pages.builders.code import _rel

        return {"id": row["id"], "label": row["qualified_name"], "type": row["node_type"],
                "file": _rel(row["file_path"], repo) if repo else row["file_path"], "line": row["line_start"]}

    @r.get("/graph/neighborhood")
    def neighborhood(request: Request, node: str, depth: int = Query(1, ge=1, le=2),
                     kinds: Optional[str] = None, limit: int = Query(200, ge=1, le=MAX_GRAPH_NODES)):
        layout = _ctx(request)
        with _ro(layout) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            meta = _meta(request, conn, sid)
            start = _find_node(conn, sid, node)
            if start is None:
                return _answer({"node": node}, status="not_found", meta=meta)
            nodes, edges = _graph(conn, sid)
            allowed = {k.strip() for k in kinds.split(",")} if kinds else None
            adj: dict[int, list] = defaultdict(list)
            for e in edges:
                adj[e["caller_id"]].append(e)
                adj[e["callee_id"]].append(e)
            seen = {start: 0}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                if seen[cur] >= depth:
                    continue
                for e in adj[cur]:
                    other = e["callee_id"] if e["caller_id"] == cur else e["caller_id"]
                    if other in nodes and other not in seen and (allowed is None or nodes[other]["node_type"] in allowed):
                        seen[other] = seen[cur] + 1
                        queue.append(other)
            degree = defaultdict(int)
            sub_edges = [e for e in edges if e["caller_id"] in seen and e["callee_id"] in seen]
            for e in sub_edges:
                degree[e["caller_id"]] += 1
                degree[e["callee_id"]] += 1
            ranked = sorted(seen, key=lambda n: (n != start, seen[n], -degree[n], nodes[n]["qualified_name"]))
            keep = set(ranked[:limit])
            out_edges = [{"src": e["caller_id"], "dst": e["callee_id"], "edge_kind": "calls",
                          "origin": e["resolved_by"] or "", "confidence": e["confidence"], "evidence_kind": "static"}
                         for e in sub_edges if e["caller_id"] in keep and e["callee_id"] in keep]
            data = {"focus": start, "nodes": [_node_json(nodes[n], layout.repo) for n in ranked[:limit]],
                    "edges": sorted(out_edges, key=lambda e: (e["src"], e["dst"]))}
            return _answer(data, totals={"nodes": len(seen), "edges": len(sub_edges)},
                           truncated=len(seen) > limit, omitted=["nodes beyond limit"] if len(seen) > limit else [],
                           meta=meta)

    @r.get("/graph/path")
    def path(request: Request, from_: str = Query(..., alias="from"), to: str = Query(...),
             max_depth: int = Query(4, ge=1, le=6)):
        layout = _ctx(request)
        with _ro(layout) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            meta = _meta(request, conn, sid)
            a, b = _find_node(conn, sid, from_), _find_node(conn, sid, to)
            if a is None or b is None:
                return _answer({"missing": [x for x, n in ((from_, a), (to, b)) if n is None]}, status="not_found",
                               meta=meta)
            nodes, edges = _graph(conn, sid)
            out: dict[int, list[int]] = defaultdict(list)
            for e in edges:
                out[e["caller_id"]].append(e["callee_id"])
            # BFS layering, then enumerate all shortest paths (bounded).
            dist = {a: 0}
            parents: dict[int, list[int]] = defaultdict(list)
            queue = deque([a])
            while queue:
                cur = queue.popleft()
                if dist[cur] >= max_depth:
                    continue
                for nxt in sorted(set(out[cur])):
                    if nxt not in dist:
                        dist[nxt] = dist[cur] + 1
                        queue.append(nxt)
                    if dist[nxt] == dist[cur] + 1:
                        parents[nxt].append(cur)
            if b not in dist:
                return _answer({"paths": [], "nodes": {}}, totals={"paths": 0}, meta=meta)
            paths: list[list[int]] = []

            def walk(n: int, acc: list[int]) -> None:
                if len(paths) >= 20:
                    return
                if n == a:
                    paths.append([a, *reversed(acc)])
                    return
                for p in sorted(parents[n]):
                    walk(p, [*acc, n])
            walk(b, [])
            used = sorted({n for p in paths for n in p})
            return _answer({"paths": paths, "nodes": {n: _node_json(nodes[n], layout.repo) for n in used}},
                           totals={"paths": len(paths)}, truncated=len(paths) >= 20, meta=meta)

    # ── business views ────────────────────────────────────────────────────

    @r.get("/screens")
    def screens(request: Request):
        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            root: dict = {"label": "(menu)", "children": {}, "screens": []}
            count = 0
            for screen_id, spec_json in conn.execute(
                    "SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ? ORDER BY screen_id", (sid,)):
                spec = _json(spec_json, {})
                node = root
                for part in (spec.get("menu_path") or [])[:-1]:
                    node = node["children"].setdefault(part, {"label": part, "children": {}, "screens": []})
                node["screens"].append({"screen_id": screen_id, "page_id": f"screen:{screen_id}",
                                        "label": spec.get("screen_label") or screen_id})
                count += 1

            def freeze(n: dict) -> dict:
                return {"label": n["label"], "screens": n["screens"],
                        "children": [freeze(c) for _, c in sorted(n["children"].items())]}
            return _answer(freeze(root), totals={"screens": count}, meta=_meta(request, conn, sid))

    @r.get("/screens/{screen_id}")
    def screen(request: Request, screen_id: str):
        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            row = conn.execute("SELECT spec_json FROM screen_specs WHERE scan_id = ? AND screen_id = ?",
                               (sid, screen_id)).fetchone()
            meta = _meta(request, conn, sid)
            if not row:
                return _answer({"screen_id": screen_id}, status="not_found", meta=meta)
            spec = _json(row[0], {})
            chain = {"menu_path": spec.get("menu_path") or [], "component": spec.get("fe_component"),
                     "api_calls": spec.get("fe_api_calls") or [], "controllers": spec.get("be_controllers") or [],
                     "services": spec.get("be_services") or [], "tables": spec.get("db_tables") or [],
                     "batch_jobs": spec.get("batch_jobs") or [], "interfaces": spec.get("external_interfaces") or []}
            return _answer({"screen_id": screen_id, "page_id": f"screen:{screen_id}",
                            "label": spec.get("screen_label") or screen_id, "chain": chain,
                            "crud_profile": spec.get("crud_profile")}, meta=meta)

    @r.get("/entities/{name:path}")
    def entity(request: Request, name: str):
        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            meta = _meta(request, conn, sid)
            row = conn.execute("SELECT entity, entity_id, states_json, fields_json, confidence FROM entity_state_machines "
                               "WHERE scan_id = ? AND (entity_id = ? OR entity = ?) ORDER BY entity_id LIMIT 1",
                               (sid, name, name)).fetchone()
            if not row:
                return _answer({"name": name}, status="not_found", meta=meta)
            transitions = [dict(t) for t in conn.execute(
                "SELECT field, from_state, to_state, trigger_function, guard_expr, confidence FROM state_transitions "
                "WHERE scan_id = ? AND entity_id = ? ORDER BY field, from_state, to_state, trigger_function",
                (sid, row["entity_id"]))]
            page = conn.execute("SELECT body_md FROM pages WHERE scan_id = ? AND page_id = ?",
                                (sid, f"entity:{row['entity_id']}")).fetchone()
            return _answer({"entity": row["entity"], "entity_id": row["entity_id"], "page_id": f"entity:{row['entity_id']}",
                            "states": _json(row["states_json"], []), "fields": _json(row["fields_json"], []),
                            "transitions": transitions, "confidence": row["confidence"],
                            "body_md": page[0] if page else None}, meta=meta)

    @r.get("/tables/{name:path}")
    def table(request: Request, name: str):
        with _ro(_ctx(request)) as conn:
            if conn is None or (sid := _current_scan(conn)) is None:
                return _not_indexed(request)
            meta = _meta(request, conn, sid)
            key = name.strip().strip('"`[]').lower()
            row = conn.execute("SELECT facts_json FROM pages WHERE scan_id = ? AND page_id = ?",
                               (sid, f"table:{key}")).fetchone()
            if not row:
                return _answer({"name": name}, status="not_found", meta=meta)
            facts = _json(row[0], {}).get("facts", {})
            rels = [dict(r) for r in conn.execute(
                "SELECT from_entity, to_entity, from_field, to_field, cardinality, source, inferred FROM db_relationship "
                "WHERE scan_id = ? AND (lower(from_entity) = ? OR lower(to_entity) = ?) ORDER BY from_entity, to_entity",
                (sid, key, key))]
            return _answer({"table": key, "page_id": f"table:{key}", "columns": facts.get("columns", []),
                            "relationships": rels, "screens": facts.get("screens", []),
                            "procs": []}, meta=meta)

    return r
