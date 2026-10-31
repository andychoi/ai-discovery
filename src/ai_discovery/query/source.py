"""Exact symbol source and call paths (roadmap item M5.3 opt-in tools).

``symbol_source`` reads the live file and says whether the indexed line bounds can
still be trusted (file unchanged since the scan hashed it). ``call_path`` lists
shortest call chains A → B over edges the impact walk would follow.
"""

from __future__ import annotations

from collections import defaultdict, deque

from ..graph.origins import FLOW_MIN_CONFIDENCE
from .answer import Answer, ambiguous, not_found, not_indexed
from .impact import _allowed, _conf, _load_graph, DEFAULT_INCLUDE
from .session import Project
from .views import _resolve_symbol

MAX_SOURCE_LINES = 400
MAX_PATHS = 20


def symbol_source(project: Project, target: str, *, context_lines: int = 0) -> dict:
    target = (target or "").strip()
    key = target.split(":", 1)[1] if target.startswith("symbol:") else target
    context_lines = max(0, min(int(context_lines), 50))
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        state, found = _resolve_symbol(r, key)
        if state == "missing":
            return not_found(target, r.did_you_mean(key), r.meta()).to_dict()
        if state == "ambiguous":
            return ambiguous(target, found, r.meta()).to_dict()
        row = found[0]
        path = r.rel(row["file_path"])
        if r.repo is None or path is None:
            return not_found(target, [], r.meta(), reason="no repository checkout").to_dict()
        file = (r.repo / path).resolve()
        if r.repo.resolve() not in file.parents or not file.is_file():
            return Answer(status="unknown", data={"target": target, "reason": "file_missing", "path": path},
                          meta=r.meta(), remedy="the file was moved or deleted since the scan; run `discover scan`"
                          ).to_dict()
        from ..core.encoding import read_source

        lines = read_source(file).text.splitlines()
        start, end = row["line_start"] or 1, row["line_end"] or row["line_start"] or 1
        changed = r.changed_files([path])
        in_bounds = end <= len(lines)
        first, last = max(1, start - context_lines), min(len(lines), end + context_lines)
        ans = Answer()
        shown = lines[first - 1:last]
        if len(shown) > MAX_SOURCE_LINES:
            ans.omitted.append({"field": "source", "dropped": len(shown) - MAX_SOURCE_LINES, "total": len(shown)})
            shown = shown[:MAX_SOURCE_LINES]
        ans.data = {"id": row["qualified_name"], "kind": row["node_type"], "path": path,
                    "line_start": start, "line_end": end, "first_line": first,
                    "source": "\n".join(shown), "total_lines": len(lines),
                    "verified": bool(in_bounds and not changed),
                    "verification": "unchanged since scan" if in_bounds and not changed else
                    ("file changed since scan — bounds may be off" if changed else "bounds beyond end of file")}
        if len(found) > 1:
            ans.data["overloads"] = len(found)
        ans.meta = r.meta(files=[path], untrusted=["source"])
        return ans.to_dict()


def call_path(project: Project, source: str, target: str, *, max_depth: int = 4,
              include: list[str] | None = None) -> dict:
    """Shortest call chains from *source* to *target* (both symbols), following impact-grade edges."""
    max_depth = max(1, min(int(max_depth), 6))
    include_set = frozenset(include) if include else DEFAULT_INCLUDE
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        ends = []
        for name in (source, target):
            key = name.split(":", 1)[1] if name.startswith("symbol:") else name
            state, found = _resolve_symbol(r, key)
            if state == "missing":
                return not_found(name, r.did_you_mean(key), r.meta()).to_dict()
            if state == "ambiguous":
                return ambiguous(name, found, r.meta()).to_dict()
            ends.append({row["id"] for row in found})
        g = _load_graph(r)
        fwd: dict[int, list] = defaultdict(list)
        for edges in g.rev.values():
            for e in edges:
                if _allowed(e, include_set) and _conf(e) >= FLOW_MIN_CONFIDENCE:
                    fwd[e.src].append(e)
        start, goal = ends
        dist = {s: 0 for s in start}
        parents: dict[int, list] = defaultdict(list)
        queue = deque(sorted(start))
        while queue:
            cur = queue.popleft()
            if dist[cur] >= max_depth:
                continue
            for e in sorted(fwd.get(cur, ()), key=lambda x: x.dst):
                if e.dst not in dist:
                    dist[e.dst] = dist[cur] + 1
                    queue.append(e.dst)
                if dist[e.dst] == dist[cur] + 1:
                    parents[e.dst].append(e)
        reached = sorted(n for n in goal if n in dist)
        paths: list[list[dict]] = []

        def walk(n: int, acc: list[dict]) -> None:
            if len(paths) >= MAX_PATHS:
                return
            if n in start:
                paths.append(list(reversed(acc)))
                return
            for e in parents[n]:
                walk(e.src, acc + [{"src": g.nodes[e.src]["qualified_name"], "dst": g.nodes[e.dst]["qualified_name"],
                                    "edge_kind": e.edge_kind, "origin": e.origin,
                                    "evidence_kind": e.evidence_kind, "confidence": round(_conf(e), 3)}])
        for n in reached:
            walk(n, [])
        ans = Answer()
        if len(paths) >= MAX_PATHS:
            ans.omitted.append({"field": "paths", "dropped": 0, "total": MAX_PATHS})
        ans.data = {"source": source, "target": target, "max_depth": max_depth, "found": "yes" if paths else
                    "no", "paths": paths,
                    "note": None if paths else "no chain of followable edges within max_depth; unresolved or "
                                               "low-confidence calls may still connect them (see get_impact unknowns)"}
        ans.totals["paths"] = len(paths)
        ans.meta = r.meta(evidence={v["evidence_kind"] for p in paths for v in p})
        return ans.to_dict()
