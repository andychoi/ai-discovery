"""Impact and change impact (roadmap item M5.2, design/mcp-and-agents.md §4).

**Question answered:** "if I change X, what *may* break?" Reverse traversal of the
call graph (who depends on X), plus forward data edges (what X writes), ending at
business entry points: endpoints, screens, batch jobs, entity state machines.

Rules (ADR-0004, design §4):

- Follow an edge only when its evidence kind is in ``include`` (default static,
  framework, dynamic_hint; ``scip`` counts as static) and its origin confidence is
  ≥ 0.75. Lower-confidence edges (``global_unique`` 0.50, ``sql_regex`` 0.60) are
  followed **only from a seed** (depth 1), marked ``certainty: "possible"``, and
  never expanded further — that cut is reported in ``unknowns``.
- Visited set (cycle-safe), depth cap (default 3, max 6).
- Hub dampening: a non-seed node whose in-degree is above the 99th percentile (and
  ≥ ``HUB_MIN``) is reported but not expanded, and listed in ``unknowns``.
- Score: ``pw(n) = Σ over shortest-path parents p of pw(p) · w(edge_kind) · conf(origin)``
  with ``pw(seed) = 1``; ``score = min(1, pw(n) · 0.7^(depth-1))``. Data writes
  outrank calls, which outrank dispatch and type use (``EDGE_WEIGHTS``).
- Wording: ``may_break`` (never "will"). Unresolved calls whose name matches a
  reached symbol may be missing callers: they are ``unknowns``, never dropped.
- Co-change / coverage / runtime evidence (M8) is listed separately, never merged
  into ``items``; until those overlays exist they are reported as missing.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..graph.origins import FLOW_MIN_CONFIDENCE, ORIGINS
from .answer import Answer, ambiguous, not_found, not_indexed
from .session import Project, Reader, loads
from .views import _TARGET_PREFIXES, _resolve_symbol

DEFAULT_DEPTH = 3
MAX_DEPTH = 6
DEFAULT_ITEMS = 50
MAX_ITEMS = 100
MAX_UNKNOWNS = 50
DECAY = 0.7
HUB_PERCENTILE = 0.99
HUB_MIN = 25
DEFAULT_INCLUDE = frozenset({"static", "framework", "dynamic_hint"})
OVERLAY_EVIDENCE = ("co_change", "coverage", "runtime")

EDGE_WEIGHTS = {
    "writes_table": 1.0, "calls": 0.8, "framework_binds": 0.8, "markup_event": 0.8, "calls_proc": 0.8,
    "calls_external": 0.8, "dispatches_to": 0.6, "dynamic_hint": 0.5, "reads_table": 0.5, "type_use": 0.4,
}
SCREEN_WEIGHT = 0.8          # backend node → screen that uses it
ENTRY_KINDS = {"endpoint", "batch_job", "cli_command", "event_consumer"}
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(Tests?|_test|\.test|\.spec|Spec)\.[A-Za-z]+$")
_RANGE = re.compile(r"^[\w./~^@{}-]+(\.\.\.?[\w./~^@{}-]+)?$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PATH_PARAM = re.compile(r"\{[^}/]*\}|:[A-Za-z_]\w*|\$\{[^}]*\}|<[^>/]*>")


# ── graph snapshot ────────────────────────────────────────────────────────

@dataclass
class Edge:
    src: int
    dst: int
    origin: str
    confidence: float
    edge_kind: str
    evidence_kind: str


@dataclass
class Graph:
    nodes: dict[int, Any]
    rev: dict[int, list[Edge]]          # callee → edges from its callers
    fwd_data: dict[int, list[Edge]]     # caller → writes_table edges
    in_degree: Counter
    hub_threshold: float
    overlays: dict[str, int]
    by_qn: dict[str, list[int]] = field(default_factory=dict)


def _load_graph(r: Reader) -> Graph:
    nodes = {row["id"]: row for row in r.conn.execute(
        "SELECT id, name, qualified_name, node_type, file_path, line_start, line_end, language "
        "FROM code_nodes WHERE scan_id = ?", (r.scan_id,))}
    cols = {row[1] for row in r.conn.execute("PRAGMA table_info(call_edges)")}
    kind = "edge_kind" if "edge_kind" in cols else "'calls'"
    evid = "evidence_kind" if "evidence_kind" in cols else "'static'"
    rev: dict[int, list[Edge]] = defaultdict(list)
    fwd: dict[int, list[Edge]] = defaultdict(list)
    callers_of: dict[int, set[int]] = defaultdict(set)
    overlays: Counter = Counter()
    for row in r.conn.execute(f"SELECT caller_id, callee_id, resolved_by, confidence, {kind}, {evid} FROM call_edges "
                              "WHERE scan_id = ? AND callee_id IS NOT NULL AND caller_id IS NOT NULL", (r.scan_id,)):
        e = Edge(row[0], row[1], row[2] or "", float(row[3] or 0), row[4] or "calls", row[5] or "static")
        if e.evidence_kind in OVERLAY_EVIDENCE:
            overlays[e.evidence_kind] += 1
            continue
        rev[e.dst].append(e)
        callers_of[e.dst].add(e.src)
        if e.edge_kind == "writes_table":
            fwd[e.src].append(e)
    in_degree = Counter({k: len(v) for k, v in callers_of.items()})
    degrees = sorted(in_degree.values())
    p99 = degrees[min(len(degrees) - 1, int(len(degrees) * HUB_PERCENTILE))] if degrees else 0
    by_qn: dict[str, list[int]] = defaultdict(list)
    for nid, row in nodes.items():
        by_qn[row["qualified_name"]].append(nid)
    return Graph(nodes, rev, fwd, in_degree, max(p99, HUB_MIN), dict(overlays), dict(by_qn))


def _conf(e: Edge) -> float:
    o = ORIGINS.get(e.origin)
    return o.confidence if o is not None else e.confidence


def _allowed(e: Edge, include: frozenset[str]) -> bool:
    kinds = set(include) | ({"scip"} if "static" in include else set())
    return e.evidence_kind in kinds


# ── business layer lookups ────────────────────────────────────────────────

def _norm_route(path: str) -> str:
    return _PATH_PARAM.sub("*", (path or "").split("?", 1)[0]).rstrip("/").lower() or "/"


@dataclass
class Business:
    endpoint_page: dict[str, str]                 # handler QN → endpoint page id
    endpoint_route: dict[str, tuple[str, str]]    # handler QN → (METHOD, normalized path)
    screens: list[tuple[str, dict]]               # (screen_id, spec)
    writers: dict[str, list[str]]                 # trigger function QN → entity ids


def _load_business(r: Reader) -> Business:
    pages, routes = {}, {}
    for pid, facts_json in r.conn.execute("SELECT page_id, facts_json FROM pages WHERE scan_id = ? AND type = 'endpoint'",
                                          (r.scan_id,)):
        facts = loads(facts_json, {}).get("facts", {})
        if facts.get("handler"):
            pages[facts["handler"]] = pid
            routes[facts["handler"]] = ((facts.get("method") or "").upper(), _norm_route(facts.get("path") or ""))
    screens = [(sid, loads(spec, {})) for sid, spec in r.conn.execute(
        "SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ? ORDER BY screen_id",
        (r.scan_id,))] if r.has_table("screen_specs") else []
    writers: dict[str, list[str]] = defaultdict(list)
    if r.has_table("state_transitions"):
        for fn, eid in r.conn.execute("SELECT DISTINCT trigger_function, entity_id FROM state_transitions "
                                      "WHERE scan_id = ? AND trigger_function IS NOT NULL ORDER BY entity_id",
                                      (r.scan_id,)):
            writers[fn].append(eid)
    return Business(pages, routes, screens, dict(writers))


def _components(spec: dict) -> list[dict]:
    return [c for key in ("be_controllers", "be_services") for c in (spec.get(key) or []) if isinstance(c, dict)]


def _screen_uses(spec: dict, node, route: Optional[tuple[str, str]]) -> Optional[float]:
    """Mapping confidence when *spec*'s screen uses code node *node* (or its endpoint route), else None."""
    qn, name = node["qualified_name"], node["name"]
    owner = qn.rsplit(".", 1)[0] if "." in qn else qn
    owner_short = owner.rsplit(".", 1)[-1]
    best: Optional[float] = None
    for comp in _components(spec):
        cls = comp.get("class_name") or ""
        if cls in (qn, name) or (cls in (owner, owner_short) and (not comp.get("methods") or name in comp["methods"])):
            best = max(best or 0.0, float(comp.get("confidence") or 0.5))
    if route:
        method, path = route
        for call in spec.get("fe_api_calls") or []:
            if isinstance(call, dict) and _norm_route(call.get("path") or "") == path and \
                    (not call.get("method") or not method or call["method"].upper() == method):
                best = max(best or 0.0, float(call.get("confidence") or 0.5))
    return best


# ── traversal ─────────────────────────────────────────────────────────────

def _expand_seeds(g: Graph, seeds: Iterable[int]) -> list[int]:
    """A class seed also seeds its members (a change to the class may change any of them)."""
    out = set(seeds)
    for nid in list(out):
        row = g.nodes.get(nid)
        if row is not None and row["node_type"] in ("class", "db_model", "interface"):
            prefix = row["qualified_name"] + "."
            out.update(m for m, mr in g.nodes.items()
                       if mr["file_path"] == row["file_path"] and mr["qualified_name"].startswith(prefix))
    return sorted(out)


def _kind(row) -> str:
    path = (row["file_path"] or "").replace("\\", "/")
    if _TEST_PATH.search(path):
        return "test"
    return row["node_type"] if row["node_type"] in ENTRY_KINDS else "symbol"


def _traverse(r: Reader, g: Graph, biz: Business, seeds: list[int], depth: int, include: frozenset[str],
              max_items: int, ans: Answer) -> dict:
    seeds = _expand_seeds(g, seeds)
    seed_set = set(seeds)
    dist = {s: 0 for s in seeds}
    pw = {s: 1.0 for s in seeds}
    best_parent: dict[int, tuple[Edge, float]] = {}
    possible: set[int] = set()
    unknowns: list[dict] = []
    frontier = list(seeds)
    for d in range(1, depth + 1):
        nxt: dict[int, float] = defaultdict(float)
        for cur in frontier:
            if cur not in seed_set and g.in_degree[cur] >= g.hub_threshold:
                unknowns.append({"kind": "hub_not_expanded", "where": g.nodes[cur]["qualified_name"],
                                 "detail": f"in-degree {g.in_degree[cur]} ≥ {g.hub_threshold:g}; callers not walked"})
                continue
            if cur in possible:  # a "possible" node is reported, never expanded
                if g.in_degree[cur]:
                    unknowns.append({"kind": "possible_not_expanded", "where": g.nodes[cur]["qualified_name"],
                                     "detail": f"reached only through a low-confidence or dynamic edge; "
                                               f"its {g.in_degree[cur]} caller(s) were not walked"})
                continue
            for e in g.rev.get(cur, ()):
                if not _allowed(e, include) or e.src not in g.nodes:
                    continue
                c = _conf(e)
                low = c < FLOW_MIN_CONFIDENCE
                if low and cur not in seed_set:
                    unknowns.append({"kind": "low_confidence_edge_not_followed",
                                     "where": g.nodes[e.src]["qualified_name"],
                                     "detail": f"{e.origin or 'unknown'} ({c:.2f}) → {g.nodes[cur]['qualified_name']}"})
                    continue
                if e.src in dist and dist[e.src] < d:
                    continue  # already reached by a shorter path (cycle-safe)
                gain = pw[cur] * EDGE_WEIGHTS.get(e.edge_kind, 0.5) * c
                nxt[e.src] += gain
                if e.src not in best_parent or gain > best_parent[e.src][1]:
                    best_parent[e.src] = (e, gain)
                if low or e.evidence_kind == "dynamic_hint" or e.edge_kind == "dynamic_hint":
                    possible.add(e.src)
        new = [n for n in sorted(nxt) if n not in dist]
        for n in new:
            dist[n] = d
            pw[n] = nxt[n]
        frontier = new
        if not frontier:
            break
    # forward data edges (what reached code writes)
    data_items: list[dict] = []
    for nid in sorted(dist):
        for e in g.fwd_data.get(nid, ()):
            if e.dst in g.nodes and e.dst not in dist and _allowed(e, include):
                data_items.append({"node": g.nodes[e.dst]["qualified_name"], "kind": "table",
                                   "score": round(min(1.0, pw[nid] * EDGE_WEIGHTS["writes_table"] * _conf(e)
                                                      * DECAY ** dist[nid]), 4),
                                   "depth": dist[nid] + 1, "via": [_via(g, e)], "may_break": True,
                                   "certainty": "likely" if _conf(e) >= FLOW_MIN_CONFIDENCE else "possible",
                                   "citation": None})
    items: list[dict] = []
    entry_points: set[str] = set()
    for nid, d in dist.items():
        row = g.nodes[nid]
        qn = row["qualified_name"]
        page = biz.endpoint_page.get(qn)
        if row["node_type"] in ENTRY_KINDS:
            entry_points.add(page or f"{row['node_type']}:{qn}")
        for eid in biz.writers.get(qn, []):
            data_items.append({"node": f"entity:{eid}", "kind": "entity", "score": round(min(
                1.0, pw[nid] * EDGE_WEIGHTS["writes_table"] * DECAY ** d), 4), "depth": d + 1,
                "via": [{"src": qn, "dst": f"entity:{eid}", "edge_kind": "state_transition", "origin": "fsm",
                         "evidence_kind": "static", "confidence": None}],
                "may_break": True, "certainty": "likely", "citation": None})
        if d == 0:
            continue
        items.append({"node": qn, "kind": _kind(row), "page_id": page, "score": round(min(1.0, pw[nid] * DECAY ** (d - 1)), 4),
                      "depth": d, "via": _path(g, best_parent, nid, seed_set), "may_break": True,
                      "certainty": "possible" if nid in possible else "likely",
                      "citation": {"path": r.rel(row["file_path"]), "line_start": row["line_start"],
                                   "line_end": row["line_end"]}})
    # screens that use any reached (or seed) node
    screens: dict[str, dict] = {}
    for nid, d in dist.items():
        row = g.nodes[nid]
        route = biz.endpoint_route.get(row["qualified_name"])
        for sid, spec in biz.screens:
            conf = _screen_uses(spec, row, route)
            if conf is None:
                continue
            score = min(1.0, pw[nid] * SCREEN_WEIGHT * conf * DECAY ** d)
            cur = screens.get(sid)
            if cur is None or score > cur["score"]:
                screens[sid] = {"node": f"screen:{sid}", "kind": "screen", "page_id": f"screen:{sid}",
                                "score": round(score, 4), "depth": d + 1,
                                "via": [{"src": f"screen:{sid}", "dst": row["qualified_name"], "edge_kind": "uses",
                                         "origin": "screen_mapping", "evidence_kind": "framework",
                                         "confidence": conf}],
                                "may_break": True, "certainty": "likely" if conf >= FLOW_MIN_CONFIDENCE else "possible",
                                "citation": None}
    entry_points |= {f"screen:{sid}" for sid in screens}
    all_items = items + list(screens.values()) + data_items
    ranked = ans.cap("items", all_items, max_items, key=lambda i: (-i["score"], i["depth"], i["node"]))
    unknowns.extend(_unresolved_unknowns(r, g, dist))
    seen_u: set[tuple] = set()
    unknowns = [u for u in unknowns if (k := (u["kind"], u["where"], u["detail"])) not in seen_u and not seen_u.add(k)]
    unknowns = ans.cap("unknowns", unknowns, MAX_UNKNOWNS, key=lambda u: (u["kind"], u["where"], u["detail"]))
    ans.totals["by_kind"] = dict(Counter(i["kind"] for i in all_items))
    evidence = {v["evidence_kind"] for i in ranked for v in i["via"] if v.get("evidence_kind")}
    return {"seeds": [g.nodes[s]["qualified_name"] for s in seeds[:50]], "items": ranked,
            "entry_points_reached": sorted(entry_points), "tests_reached": sorted(
                i["node"] for i in all_items if i["kind"] == "test"),
            "unknowns": unknowns, "co_change": [], "runtime": [],
            "overlays_missing": [k for k in OVERLAY_EVIDENCE if not g.overlays.get(k)],
            "_evidence": evidence, "_files": [i["citation"]["path"] for i in ranked if i.get("citation")]}


def _via(g: Graph, e: Edge) -> dict:
    return {"src": g.nodes[e.src]["qualified_name"], "dst": g.nodes[e.dst]["qualified_name"],
            "edge_kind": e.edge_kind, "origin": e.origin, "evidence_kind": e.evidence_kind,
            "confidence": round(_conf(e), 3)}


def _path(g: Graph, best_parent: dict, nid: int, seeds: set[int]) -> list[dict]:
    """Best (highest-weight) path from a seed to *nid*, seed side first."""
    out = []
    cur = nid
    while cur not in seeds and cur in best_parent and len(out) <= MAX_DEPTH:
        e, _ = best_parent[cur]
        out.append(_via(g, e))
        cur = e.dst
    return list(reversed(out))


def _unresolved_unknowns(r: Reader, g: Graph, dist: dict[int, int]) -> list[dict]:
    """Unresolved calls whose name matches a reached symbol: possibly a caller we could not bind."""
    if not r.has_table("unresolved_calls"):
        return []
    names = sorted({g.nodes[n]["name"] for n in dist})
    out = []
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for row in r.conn.execute(
                f"SELECT u.callee_name, u.reason, u.file, u.line, n.qualified_name FROM unresolved_calls u "
                f"LEFT JOIN code_nodes n ON n.id = u.caller_id WHERE u.scan_id = ? AND u.callee_name IN ({marks}) "
                "AND u.reason IN ('ambiguous', 'not_found', 'dynamic', 'dispatch_cap', 'di_ambiguous') "
                "ORDER BY u.file, u.line", [r.scan_id, *chunk]):
            kind = "dynamic_hint" if row["reason"] == "dynamic" else "unresolved_call"
            where = f"{r.rel(row['file']) or '?'}:{row['line'] or '?'}"
            out.append({"kind": kind, "where": where,
                        "detail": f"call to `{row['callee_name']}` ({row['reason']}) from "
                                  f"{row['qualified_name'] or 'unknown caller'} may target a reached symbol"})
    return out


# ── targets ───────────────────────────────────────────────────────────────

def _seeds_for(r: Reader, g: Graph, biz: Business, target: str) -> tuple[str, Any]:
    """('ok', (seed ids, label)) | ('not_found', did_you_mean) | ('ambiguous', candidates)."""
    prefix, sep, rest = target.partition(":")
    if sep and prefix in _TARGET_PREFIXES and prefix != "symbol":
        if prefix == "file":
            path = rest.strip("/")
            ids = [n for n, row in g.nodes.items() if r.rel(row["file_path"]) == path]
            return ("ok", (ids, target)) if ids else ("not_found", r.did_you_mean(target, types=["file"]))
        if prefix == "endpoint":
            handler = next((h for h, p in biz.endpoint_page.items() if p == target), None)
            ids = g.by_qn.get(handler or "", [])
            return ("ok", (ids, target)) if ids else ("not_found", r.did_you_mean(target, types=["endpoint"]))
        if prefix == "screen":
            spec = next((s for sid, s in biz.screens if sid == rest), None)
            if spec is None:
                return "not_found", r.did_you_mean(target, types=["screen"])
            ids: list[int] = []
            for comp in _components(spec):
                cls = comp.get("class_name") or ""
                for nid, row in g.nodes.items():
                    owner = row["qualified_name"].rsplit(".", 1)[0]
                    if row["qualified_name"] == cls or row["name"] == cls or (
                            owner.rsplit(".", 1)[-1] in (cls, cls.rsplit(".", 1)[-1])
                            and (not comp.get("methods") or row["name"] in comp["methods"])):
                        ids.append(nid)
            routes = {(_norm_route(c.get("path") or "")) for c in spec.get("fe_api_calls") or [] if isinstance(c, dict)}
            ids += [nid for h, (_, path) in biz.endpoint_route.items() if path in routes for nid in g.by_qn.get(h, [])]
            return "ok", (sorted(set(ids)), target)  # a screen with no backend mapping → empty impact, still ok
        if prefix == "entity":
            key = rest
            row = r.conn.execute("SELECT entity_id FROM entity_state_machines WHERE scan_id = ? AND "
                                 "(entity_id = ? OR lower(entity) = lower(?)) ORDER BY entity_id",
                                 (r.scan_id, key, key)).fetchall() if r.has_table("entity_state_machines") else []
            if len(row) > 1:
                return "ambiguous", [{"id": f"entity:{x[0]}"} for x in row]
            if not row:
                return "not_found", r.did_you_mean(target, types=["entity"])
            eid = row[0][0]
            ids = list(g.by_qn.get(eid, []))
            ids += [n for fn, eids in biz.writers.items() if eid in eids for n in g.by_qn.get(fn, [])]
            return "ok", (sorted(set(ids)), f"entity:{eid}")
        if prefix == "table":
            key = rest.strip('"`[]').lower()
            page = r.page_row(f"table:{key}")
            if page is None:
                return "not_found", r.did_you_mean(target, types=["table"])
            files = {c.get("path") for c in loads(page["citations_json"], [])}
            ids = [n for n, row in g.nodes.items()
                   if row["node_type"] in ("db_model", "class") and r.rel(row["file_path"]) in files]
            return "ok", (ids, f"table:{key}")
        if r.page_row(target) is None:
            return "not_found", r.did_you_mean(target)
        return "ok", ([], target)
    key = rest if prefix == "symbol" and sep else target
    state, found = _resolve_symbol(r, key)
    if state == "missing":
        return "not_found", r.did_you_mean(key)
    if state == "ambiguous":
        return "ambiguous", found
    return "ok", ([row["id"] for row in found], f"symbol:{found[0]['qualified_name']}")


def _table_extras(r: Reader, label: str, ans_items: list[dict]) -> list[dict]:
    """For a table target: screens reading it and tables referencing it (FK) — schema changes reach them."""
    key = label.split(":", 1)[1]
    extra = []
    if r.has_table("screen_specs"):
        for sid, spec in r.conn.execute("SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ?",
                                        (r.scan_id,)):
            if key in {str(t).lower() for t in loads(spec, {}).get("db_tables") or []}:
                extra.append({"node": f"screen:{sid}", "kind": "screen", "page_id": f"screen:{sid}",
                              "score": round(EDGE_WEIGHTS["reads_table"], 4), "depth": 1,
                              "via": [{"src": f"screen:{sid}", "dst": label, "edge_kind": "reads_table",
                                       "origin": "screen_mapping", "evidence_kind": "framework", "confidence": None}],
                              "may_break": True, "certainty": "possible", "citation": None})
    if r.has_table("db_relationship"):
        for (src,) in r.conn.execute("SELECT DISTINCT lower(from_entity) FROM db_relationship WHERE scan_id = ? AND "
                                     "lower(to_entity) = ? AND lower(from_entity) != ?", (r.scan_id, key, key)):
            extra.append({"node": f"table:{src}", "kind": "table", "page_id": f"table:{src}",
                          "score": round(EDGE_WEIGHTS["reads_table"], 4), "depth": 1,
                          "via": [{"src": f"table:{src}", "dst": label, "edge_kind": "references",
                                   "origin": "foreign_key", "evidence_kind": "static", "confidence": None}],
                          "may_break": True, "certainty": "likely", "citation": None})
    have = {i["node"] for i in ans_items}
    return [e for e in extra if e["node"] not in have]


def _norm_include(include: Optional[Iterable[str]]) -> frozenset[str]:
    return frozenset(include) if include else DEFAULT_INCLUDE


def _clamp(depth: int, max_items: int) -> tuple[int, int]:
    return max(1, min(int(depth), MAX_DEPTH)), max(1, min(int(max_items), MAX_ITEMS))


def impact(project: Project, target: str, *, depth: int = DEFAULT_DEPTH, include: Optional[Iterable[str]] = None,
           max_items: int = DEFAULT_ITEMS) -> dict:
    """What may break if *target* changes. Target: symbol, ``file:``, ``table:``, ``screen:``, ``entity:``, ``endpoint:``."""
    target = (target or "").strip()
    depth, max_items = _clamp(depth, max_items)
    include = _norm_include(include)
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        g, biz = _load_graph(r), _load_business(r)
        state, found = _seeds_for(r, g, biz, target)
        if state == "not_found":
            return not_found(target, found, r.meta()).to_dict()
        if state == "ambiguous":
            return ambiguous(target, found, r.meta()).to_dict()
        seeds, label = found
        ans = Answer()
        data = _traverse(r, g, biz, seeds, depth, include, max_items, ans)
        if label.startswith("table:"):
            extra = _table_extras(r, label, data["items"])
            if extra:
                kept = data["items"] + extra
                ans.omitted = [o for o in ans.omitted if o["field"] != "items"]
                data["items"] = ans.cap("items", kept, max_items, key=lambda i: (-i["score"], i["depth"], i["node"]))
                data["entry_points_reached"] = sorted(set(data["entry_points_reached"])
                                                      | {e["node"] for e in extra if e["kind"] == "screen"})
        evidence, files = data.pop("_evidence"), data.pop("_files")
        ans.data = {"target": label, "depth": depth, "include": sorted(include), **data}
        if not seeds:
            ans.data["note"] = "no code symbols map to this target; impact is limited to page-level relations"
        ans.meta = r.meta(files=files, evidence=evidence)
        return ans.to_dict()


# ── change impact ─────────────────────────────────────────────────────────

@dataclass
class FileDiff:
    old_path: Optional[str]
    new_path: Optional[str]
    status: str = "modified"                       # added | deleted | modified | renamed
    old_ranges: list[tuple[int, int]] = field(default_factory=list)
    new_ranges: list[tuple[int, int]] = field(default_factory=list)


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Files and changed line ranges (both sides) from ``git diff`` output (any -U context)."""
    files: list[FileDiff] = []
    cur: Optional[FileDiff] = None
    for line in (text or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" ")
            a = parts[2][2:] if len(parts) > 3 and parts[2].startswith("a/") else None
            b = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else None
            cur = FileDiff(a, b)
            files.append(cur)
        elif line.startswith("--- "):
            if cur is None:
                cur = FileDiff(None, None)
                files.append(cur)
            src = line[4:].split("\t", 1)[0]
            cur.old_path = None if src == "/dev/null" else src[2:] if src.startswith("a/") else src
            if src == "/dev/null":
                cur.status = "added"
        elif line.startswith("+++ ") and cur is not None:
            dst = line[4:].split("\t", 1)[0]
            cur.new_path = None if dst == "/dev/null" else dst[2:] if dst.startswith("b/") else dst
            if dst == "/dev/null":
                cur.status = "deleted"
        elif line.startswith("rename from ") and cur is not None:
            cur.old_path, cur.status = line[len("rename from "):], "renamed"
        elif line.startswith("rename to ") and cur is not None:
            cur.new_path = line[len("rename to "):]
        elif line.startswith("new file mode") and cur is not None:
            cur.status, cur.old_path = "added", None
        elif line.startswith("deleted file mode") and cur is not None:
            cur.status, cur.new_path = "deleted", None
        elif (m := _HUNK.match(line)) and cur is not None:
            a, b = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            c, d = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            cur.old_ranges.append((a, a + b - 1) if b else (a, a))      # b == 0: insertion after line a
            cur.new_ranges.append((c, c + d - 1) if d else (c, c))
    return files


def _git_out(repo: Optional[Path], *args: str) -> tuple[Optional[str], Optional[str]]:
    if repo is None:
        return None, "no repository"
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return (out.stdout, None) if out.returncode == 0 else (None, out.stderr.strip() or f"git exit {out.returncode}")


def _changed_symbols(r: Reader, g: Graph, files: list[FileDiff], exact_base: bool) -> tuple[list[int], list[dict]]:
    """Innermost symbols whose line span intersects a hunk; files with no symbols are returned separately."""
    by_file: dict[str, list[int]] = defaultdict(list)
    for nid, row in g.nodes.items():
        by_file[r.rel(row["file_path"]) or ""].append(nid)
    changed: set[int] = set()
    unmapped: list[dict] = []
    for f in files:
        ids = by_file.get(f.old_path or "", []) or by_file.get(f.new_path or "", [])
        if not ids:
            unmapped.append({"path": f.new_path or f.old_path, "status": f.status,
                             "reason": "new file (not indexed)" if f.status == "added" else "no symbols indexed"})
            continue
        if f.status == "deleted":
            changed.update(ids)
            continue
        ranges = list(f.old_ranges) + ([] if exact_base else list(f.new_ranges))
        for lo, hi in ranges:
            hits = [n for n in ids if (g.nodes[n]["line_start"] or 0) <= hi and (g.nodes[n]["line_end"] or
                                                                                   g.nodes[n]["line_start"] or 0) >= lo]
            inner = [n for n in hits if not any(
                m != n and (g.nodes[n]["line_start"] or 0) <= (g.nodes[m]["line_start"] or 0)
                and (g.nodes[m]["line_end"] or 0) <= (g.nodes[n]["line_end"] or 0) and
                (g.nodes[n]["line_start"], g.nodes[n]["line_end"]) != (g.nodes[m]["line_start"], g.nodes[m]["line_end"])
                for m in hits)]
            changed.update(inner)
    return sorted(changed), unmapped


def change_impact(project: Project, *, diff: Optional[str] = None, range: Optional[str] = None,
                  working_tree: bool = False, depth: int = DEFAULT_DEPTH, include: Optional[Iterable[str]] = None,
                  max_items: int = DEFAULT_ITEMS) -> dict:
    """Impact of a change: a unified diff, a git range ``A..B``, or the working tree vs the indexed commit."""
    depth, max_items = _clamp(depth, max_items)
    include = _norm_include(include)
    chosen = [x for x in (diff is not None, range is not None, working_tree) if x]
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        meta0 = r.meta()
        if len(chosen) != 1:
            return Answer(status="unknown", data={"reason": "bad_arguments"}, meta=meta0,
                          remedy="pass exactly one of diff, range or working_tree").to_dict()
        indexed = meta0.get("indexed_commit")
        degraded: list[str] = []
        exact_base = False
        if diff is not None:
            text, source = diff, {"kind": "diff"}
            degraded.append("diff_base_unknown")
        else:
            if range is not None:
                if not _RANGE.match(range) or range.startswith("-"):
                    return Answer(status="unknown", data={"reason": "bad_range", "range": range}, meta=meta0,
                                  remedy="use a git revision range such as `main..HEAD` or `abc123..def456`").to_dict()
                text, err = _git_out(r.repo, "diff", "-U0", "--no-color", "--no-ext-diff", "-M", range)
                base = range.split("..", 1)[0] if ".." in range else range
                base_sha, _ = _git_out(r.repo, "rev-parse", base) if base else (None, None)
                exact_base = bool(base_sha and indexed and base_sha.strip() == indexed)
                source = {"kind": "range", "range": range}
            else:
                if not indexed:
                    return Answer(status="unknown", data={"reason": "no_indexed_commit"}, meta=meta0,
                                  remedy="re-run `discover scan` inside a git repository").to_dict()
                text, err = _git_out(r.repo, "diff", "-U0", "--no-color", "--no-ext-diff", "-M", indexed)
                exact_base = True
                source = {"kind": "working_tree", "base": indexed}
            if text is None:
                return Answer(status="unknown", data={"reason": "git_failed", "detail": err}, meta=meta0,
                              remedy="check that the repository and revisions exist").to_dict()
            if not exact_base and source["kind"] == "range":
                degraded.append("diff_base_differs_from_index")
        files = parse_unified_diff(text)
        if source["kind"] == "working_tree":
            untracked, _ = _git_out(r.repo, "ls-files", "--others", "--exclude-standard")
            files += [FileDiff(None, p, "added") for p in (untracked or "").splitlines() if p.strip()]
        g, biz = _load_graph(r), _load_business(r)
        seeds, unmapped = _changed_symbols(r, g, files, exact_base)
        ans = Answer()
        data = _traverse(r, g, biz, seeds, depth, include, max_items, ans)
        for u in unmapped:
            data["unknowns"].append({"kind": "unmapped_change", "where": u["path"], "detail": u["reason"]})
        changed_rows = [g.nodes[n] for n in seeds]
        changed_syms = ans.cap("changed_symbols", [
            {"id": row["qualified_name"], "kind": row["node_type"], "path": r.rel(row["file_path"]),
             "line_start": row["line_start"], "line_end": row["line_end"]} for row in changed_rows],
            MAX_ITEMS, key=lambda s: (s["path"] or "", s["line_start"] or 0, s["id"]))
        changed_files = ans.cap("changed_files", [
            {"path": f.new_path or f.old_path, "old_path": f.old_path if f.status == "renamed" else None,
             "status": f.status, "hunks": len(f.old_ranges)} for f in files], MAX_ITEMS)
        evidence, files_cited = data.pop("_evidence"), data.pop("_files")
        screens = sorted({i["node"] for i in data["items"] if i["kind"] == "screen"})
        ans.data = {"source": source, "changed_files": changed_files, "changed_symbols": changed_syms,
                    "impact": {"depth": depth, "include": sorted(include), **data},
                    "screens_affected": screens,
                    "tests_to_run": {"status": "unknown", "tests": data["tests_reached"],
                                     "reason": "coverage overlay (M8.2) not imported; listed tests are only those "
                                               "reached through the call graph"}}
        since_index = [f.new_path or f.old_path for f in files] if source["kind"] == "working_tree" else []
        ans.meta = r.meta(files=files_cited + [s["path"] for s in changed_syms], evidence=evidence,
                          degraded=degraded, known_changed=since_index)
        return ans.to_dict()


__all__ = ["impact", "change_impact", "parse_unified_diff", "EDGE_WEIGHTS"]
