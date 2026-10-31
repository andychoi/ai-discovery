"""Read views over ``current_scan``: overview, context, screen, entity, flow, table (M5.1).

Every function takes a ``Project`` and returns ``Answer.to_dict()``. Lists are
ranked then capped; caps and true totals are recorded (``Answer.cap``).
Repo-derived text (titles excepted) is listed in ``_meta.content_trust.untrusted``.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Optional

from ..graph.origins import FLOW_EDGE_KINDS, FLOW_EVIDENCE, FLOW_MIN_CONFIDENCE, ORIGINS
from .answer import Answer, ambiguous, not_found, not_indexed
from .session import Project, Reader, loads

MAX_NEIGHBOURS = 20
MAX_ENTRY_POINTS = 20
MAX_LIST = 50
SUMMARY_CHARS = 600

_TARGET_PREFIXES = ("overview", "domain", "module", "file", "symbol", "endpoint", "screen", "menu", "entity",
                    "table", "stored_proc", "batch_job", "interface", "flow", "onboarding", "tcode", "dynpro")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _plain(md: Optional[str], limit: int = SUMMARY_CHARS) -> str:
    text = _MD_LINK.sub(r"\1", md or "")
    text = re.sub(r"-{3,}|[`*_#|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sections(facts_json: Optional[str]) -> list[dict]:
    return loads(facts_json, {}).get("sections", []) or []


def _facts(facts_json: Optional[str]) -> dict:
    return loads(facts_json, {}).get("facts", {}) or {}


def _citations(row) -> list[dict]:
    return [{"path": c.get("path"), "line_start": c.get("line_start"), "line_end": c.get("line_end")}
            for c in loads(row["citations_json"], [])] if row is not None else []


# ── edges ─────────────────────────────────────────────────────────────────

def _edge_columns(r: Reader) -> str:
    cols = {row[1] for row in r.conn.execute("PRAGMA table_info(call_edges)")}
    kind = "edge_kind" if "edge_kind" in cols else "'calls' AS edge_kind"
    evidence = "evidence_kind" if "evidence_kind" in cols else "'static' AS evidence_kind"
    return f"e.caller_id, e.callee_id, e.resolved_by, e.confidence, {kind}, {evidence}"


def _followable(origin: Optional[str], confidence: float, edge_kind: str, evidence_kind: str) -> bool:
    o = ORIGINS.get(origin or "")
    if o is None:
        return (confidence or 0) >= FLOW_MIN_CONFIDENCE
    return o.evidence_kind in FLOW_EVIDENCE and o.edge_kind in FLOW_EDGE_KINDS and o.confidence >= FLOW_MIN_CONFIDENCE


def _neighbour(r: Reader, edge, node) -> dict:
    return {"id": node["qualified_name"], "kind": node["node_type"], "path": r.rel(node["file_path"]),
            "line": node["line_start"], "edge_kind": edge["edge_kind"], "origin": edge["resolved_by"] or "",
            "evidence_kind": edge["evidence_kind"], "confidence": edge["confidence"],
            "followable": _followable(edge["resolved_by"], edge["confidence"], edge["edge_kind"],
                                      edge["evidence_kind"])}


def _rank_neighbour(n: dict) -> tuple:
    return (not n["followable"], -(n["confidence"] or 0), n["id"])


def _graph_neighbours(r: Reader, ids: list[int], *, inside: Optional[set[int]] = None) -> tuple[list, list]:
    """(callers, callees) of the node set *ids*; edges internal to *inside* are skipped."""
    if not ids:
        return [], []
    inside = inside if inside is not None else set(ids)
    marks = ",".join("?" * len(ids))
    cols = _edge_columns(r)
    node_cols = "n.qualified_name, n.node_type, n.file_path, n.line_start"
    callers = [_neighbour(r, row, row) for row in r.conn.execute(
        f"SELECT {cols}, {node_cols} FROM call_edges e JOIN code_nodes n ON n.id = e.caller_id "
        f"WHERE e.scan_id = ? AND e.callee_id IN ({marks})", [r.scan_id, *ids]) if row["caller_id"] not in inside]
    callees = [_neighbour(r, row, row) for row in r.conn.execute(
        f"SELECT {cols}, {node_cols} FROM call_edges e JOIN code_nodes n ON n.id = e.callee_id "
        f"WHERE e.scan_id = ? AND e.caller_id IN ({marks}) AND e.callee_id IS NOT NULL", [r.scan_id, *ids])
        if row["callee_id"] not in inside]
    return _dedupe(callers), _dedupe(callees)


def _dedupe(items: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for n in sorted(items, key=_rank_neighbour):
        best.setdefault(n["id"], n)
    return list(best.values())


def _unresolved(r: Reader, ids: list[int]) -> list[dict]:
    if not ids or not r.has_table("unresolved_calls"):
        return []
    marks = ",".join("?" * len(ids))
    return [{"callee_name": row["callee_name"], "reason": row["reason"], "path": r.rel(row["file"]),
             "line": row["line"]}
            for row in r.conn.execute(
                f"SELECT callee_name, reason, file, line FROM unresolved_calls WHERE scan_id = ? "
                f"AND caller_id IN ({marks}) ORDER BY file, line, callee_name", [r.scan_id, *ids])]


def _callers_complete(r: Reader, names: Iterable[str]) -> str:
    """"unknown" when some unresolved call elsewhere has this symbol's short name (it might be a caller)."""
    names = sorted({n for n in names if n})
    if not names or not r.has_table("unresolved_calls"):
        return "yes"
    marks = ",".join("?" * len(names))
    hit = r.conn.execute(f"SELECT 1 FROM unresolved_calls WHERE scan_id = ? AND callee_name IN ({marks}) "
                         "AND reason IN ('ambiguous', 'not_found', 'dynamic', 'dispatch_cap', 'di_ambiguous') LIMIT 1",
                         [r.scan_id, *names]).fetchone()
    return "unknown" if hit else "yes"


# ── overview ──────────────────────────────────────────────────────────────

def overview(project: Project) -> dict:
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        ans = Answer()
        page = r.page_row("overview:system")
        facts = _facts(page["facts_json"]) if page else {}
        counts = {t: 0 for t in ("screen", "entity", "table", "endpoint", "batch_job", "stored_proc", "flow")}
        for t, n in r.conn.execute("SELECT type, COUNT(*) FROM pages WHERE scan_id = ? GROUP BY type", (r.scan_id,)):
            if t in counts:
                counts[t] = n
        files, symbols = r.conn.execute("SELECT COUNT(DISTINCT file_path), COUNT(*) FROM code_nodes WHERE scan_id = ?",
                                        (r.scan_id,)).fetchone()
        langs = Counter()
        for lang, n in r.conn.execute("SELECT language, COUNT(DISTINCT file_path) FROM code_nodes WHERE scan_id = ? "
                                      "GROUP BY language", (r.scan_id,)):
            langs[lang or "unknown"] = n
        # entry points: domain entry points, ranked by how much they call (a proxy for how much they reach)
        eps: set[str] = set()
        domains = []
        for row in r.conn.execute("SELECT name, node_count, entry_points FROM domains WHERE scan_id = ? ORDER BY name",
                                  (r.scan_id,)):
            eps.update(loads(row["entry_points"], []))
            pid = f"domain:{row['name']}"
            domains.append({"name": row["name"], "node_count": row["node_count"],
                            "page_id": pid if r.page_row(pid) else None})
        out_degree = Counter()
        node_of: dict[str, Any] = {}
        if eps:
            qns = sorted(eps)
            for i in range(0, len(qns), 500):
                chunk = qns[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for row in r.conn.execute(
                        f"SELECT n.qualified_name, n.node_type, n.file_path, n.line_start, "
                        f"(SELECT COUNT(*) FROM call_edges e WHERE e.scan_id = n.scan_id AND e.caller_id = n.id) AS deg "
                        f"FROM code_nodes n WHERE n.scan_id = ? AND n.qualified_name IN ({marks})", [r.scan_id, *chunk]):
                    out_degree[row["qualified_name"]] += row["deg"]
                    node_of.setdefault(row["qualified_name"], row)
        entry_points = ans.cap("entry_points", [
            {"id": qn, "kind": node_of[qn]["node_type"], "path": r.rel(node_of[qn]["file_path"]),
             "line": node_of[qn]["line_start"], "calls": out_degree[qn]} for qn in node_of],
            MAX_ENTRY_POINTS, key=lambda e: (-e["calls"], e["id"]))
        domains = ans.cap("domains", domains, MAX_LIST, key=lambda d: (-(d["node_count"] or 0), d["name"]))
        # unresolved ratio per caller language: unresolved / (unresolved + resolved)
        resolved = Counter({lang or "unknown": n for lang, n in r.conn.execute(
            "SELECT n.language, COUNT(*) FROM call_edges e JOIN code_nodes n ON n.id = e.caller_id "
            "WHERE e.scan_id = ? AND e.callee_id IS NOT NULL GROUP BY n.language", (r.scan_id,))})
        unresolved = Counter()
        if r.has_table("unresolved_calls"):
            unresolved = Counter({lang or "unknown": n for lang, n in r.conn.execute(
                "SELECT n.language, COUNT(*) FROM unresolved_calls u JOIN code_nodes n ON n.id = u.caller_id "
                "WHERE u.scan_id = ? GROUP BY n.language", (r.scan_id,))})
        ratio = {lang: round(unresolved[lang] / (unresolved[lang] + resolved[lang]), 3)
                 for lang in sorted(set(resolved) | set(unresolved)) if unresolved[lang] + resolved[lang]}
        stacks = [{"language": lang, "files": n} for lang, n in sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))]
        langs_txt = ", ".join(f"{s['language']} {s['files']}" for s in stacks) or "none"
        summary = (f"{files} files ({langs_txt}), "
                   f"{symbols} symbols, {counts['screen']} screens, {counts['endpoint']} endpoints, "
                   f"{counts['table']} tables, {counts['entity']} entities.")
        ans.data = {"summary": summary, "stacks": stacks, "external_systems": facts.get("external_systems", []),
                    "counts": {"files": files, "symbols": symbols, "screens": counts["screen"],
                               "entities": counts["entity"], "tables": counts["table"],
                               "endpoints": counts["endpoint"], "batch_jobs": counts["batch_job"],
                               "stored_procs": counts["stored_proc"], "flows": counts["flow"]},
                    "entry_points": entry_points, "domains": domains, "unresolved_ratio_by_lang": ratio,
                    "page_id": "overview:system" if page else None}
        ans.meta = r.meta()
        return ans.to_dict()


# ── context ───────────────────────────────────────────────────────────────

def _resolve_symbol(r: Reader, key: str) -> tuple[str, Any]:
    """('ok', rows) | ('ambiguous', candidates) | ('missing', None) for a symbol name or qualified name."""
    rows = r.conn.execute("SELECT id, name, qualified_name, node_type, file_path, line_start, line_end, language "
                          "FROM code_nodes WHERE scan_id = ? AND qualified_name = ? ORDER BY id",
                          (r.scan_id, key)).fetchall()
    if not rows and key.isdigit():
        rows = r.conn.execute("SELECT id, name, qualified_name, node_type, file_path, line_start, line_end, language "
                              "FROM code_nodes WHERE scan_id = ? AND id = ?", (r.scan_id, int(key))).fetchall()
    if rows:
        return "ok", rows
    rows = r.conn.execute("SELECT id, name, qualified_name, node_type, file_path, line_start, line_end, language "
                          "FROM code_nodes WHERE scan_id = ? AND (name = ? OR qualified_name LIKE ? ESCAPE '\\') "
                          "ORDER BY qualified_name, id",
                          (r.scan_id, key, "%." + key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")))
    rows = rows.fetchall()
    qns = sorted({row["qualified_name"] for row in rows})
    if len(qns) == 1:
        return "ok", rows
    if len(qns) > 1:
        return "ambiguous", [{"id": f"symbol:{q}", "kind": row["node_type"], "path": r.rel(row["file_path"]),
                              "line": row["line_start"]}
                             for q in qns[:MAX_NEIGHBOURS]
                             for row in [next(x for x in rows if x["qualified_name"] == q)]]
    return "missing", None


def _page_relations(r: Reader, page_id: str) -> tuple[list[dict], list[dict]]:
    """(outgoing links, backlinks) of a page as ``{page_id, kind}``."""
    out = [{"page_id": row[0], "kind": row[1]} for row in r.conn.execute(
        "SELECT dst, kind FROM page_links WHERE scan_id = ? AND src = ? ORDER BY kind, dst", (r.scan_id, page_id))]
    back = [{"page_id": row[0], "kind": row[1]} for row in r.conn.execute(
        "SELECT src, kind FROM page_links WHERE scan_id = ? AND dst = ? ORDER BY kind, src", (r.scan_id, page_id))]
    return out, back


def _summary(r: Reader, page, node_ids: list[int]) -> Optional[str]:
    parts = []
    if page is not None:
        secs = _sections(page["facts_json"])
        if secs:
            parts.append(_plain(secs[0].get("body_md")))
    if node_ids and r.has_table("node_summaries"):
        marks = ",".join("?" * len(node_ids))
        row = r.conn.execute(f"SELECT purpose FROM node_summaries WHERE node_id IN ({marks}) AND purpose IS NOT NULL "
                             "AND purpose != '' LIMIT 1", node_ids).fetchone()
        if row:
            parts.append(_plain(row[0]))
    return " ".join(parts) or None


def context(project: Project, target: str) -> dict:
    """Everything an agent needs around one target before reading code.

    *target*: a symbol (qualified or short name, optionally ``symbol:``-prefixed)
    or a page id (``file:``, ``screen:``, ``table:``, ``entity:``, ``endpoint:``, …).
    """
    target = (target or "").strip()
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        prefix, sep, rest = target.partition(":")
        page_id: Optional[str] = None
        node_rows: list = []
        if sep and prefix in _TARGET_PREFIXES and prefix != "symbol":
            page_id = target
            if prefix == "file":
                path = rest.strip("/")
                node_rows = [row for row in r.conn.execute(
                    "SELECT id, name, qualified_name, node_type, file_path, line_start, line_end, language "
                    "FROM code_nodes WHERE scan_id = ? AND (file_path = ? OR file_path LIKE ? ESCAPE '\\') ORDER BY id",
                    (r.scan_id, path, "%/" + path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")))
                    if r.rel(row["file_path"]) == path]
            if r.page_row(page_id) is None and not node_rows:
                return not_found(target, r.did_you_mean(target, types=[prefix]), r.meta()).to_dict()
        else:
            key = rest if prefix == "symbol" and sep else target
            state, found = _resolve_symbol(r, key)
            if state == "missing":
                return not_found(target, r.did_you_mean(key), r.meta()).to_dict()
            if state == "ambiguous":
                return ambiguous(target, found, r.meta()).to_dict()
            node_rows = found
            page_id = f"symbol:{node_rows[0]['qualified_name']}"
        page = r.page_row(page_id) if page_id else None
        ids = [row["id"] for row in node_rows]
        ans = Answer()
        callers, callees = _graph_neighbours(r, ids)
        callers = ans.cap("callers", callers, MAX_NEIGHBOURS, key=_rank_neighbour)
        callees = ans.cap("callees", callees, MAX_NEIGHBOURS, key=_rank_neighbour)
        out_links, backlinks = _page_relations(r, page_id) if page is not None else ([], [])
        screens = {b["page_id"] for b in backlinks if b["page_id"].startswith("screen:")}
        screens |= {o["page_id"] for o in out_links if o["page_id"].startswith("screen:")}
        if node_rows and r.has_table("screen_specs"):
            qn = node_rows[0]["qualified_name"]
            owner = qn.rsplit(".", 1)[0] if node_rows[0]["node_type"] in ("method", "endpoint", "function") else qn
            for (sid_,) in r.conn.execute("SELECT screen_id FROM screen_specs WHERE scan_id = ? AND "
                                          "(instr(spec_json, ?) > 0 OR instr(spec_json, ?) > 0)",
                                          (r.scan_id, f'"{qn}"', f'"{owner}"')):
                screens.add(f"screen:{sid_}")
        if page_id and page_id.startswith("screen:"):
            screens.discard(page_id)
        screens_l = ans.cap("screens", sorted(screens), MAX_LIST)
        tables = {"reads": sorted({o["page_id"] for o in out_links if o["kind"] == "reads"}),
                  "writes": sorted({o["page_id"] for o in out_links if o["kind"] == "writes"})}
        unresolved = _unresolved(r, ids)
        citations = _citations(page)
        if not citations and node_rows:
            citations = [{"path": r.rel(row["file_path"]), "line_start": row["line_start"],
                          "line_end": row["line_end"]} for row in node_rows]
        related_out = ans.cap("links", [o for o in out_links if o["kind"] not in ("reads", "writes")], MAX_LIST)
        related_in = ans.cap("backlinks", backlinks, MAX_LIST)
        short = {row["name"] for row in node_rows} if node_rows and not (page_id or "").startswith("file:") else set()
        ans.data = {
            "target": target, "page_id": page_id if page is not None else None,
            "type": page["type"] if page is not None else "symbol",
            "title": page["title"] if page is not None else (node_rows[0]["qualified_name"] if node_rows else target),
            "summary": _summary(r, page, ids),
            "symbols": [{"id": row["qualified_name"], "kind": row["node_type"], "path": r.rel(row["file_path"]),
                         "line_start": row["line_start"], "line_end": row["line_end"]}
                        for row in node_rows[:MAX_NEIGHBOURS]],
            "callers": callers, "callees": callees,
            "callers_complete": _callers_complete(r, short) if short else ("yes" if node_rows else "unknown"),
            "screens": screens_l, "tables": tables, "links": related_out, "backlinks": related_in,
            "citations": citations[:MAX_LIST], "unresolved_calls_here": len(unresolved),
            "unresolved_calls": unresolved[:MAX_NEIGHBOURS],
            "confidence": page["confidence"] if page is not None else None,
        }
        if len(unresolved) > MAX_NEIGHBOURS:
            ans.omitted.append({"field": "unresolved_calls", "dropped": len(unresolved) - MAX_NEIGHBOURS,
                                "total": len(unresolved)})
        if len(node_rows) > MAX_NEIGHBOURS:
            ans.omitted.append({"field": "symbols", "dropped": len(node_rows) - MAX_NEIGHBOURS,
                                "total": len(node_rows)})
        ans.totals["symbols"] = len(node_rows)
        evidence = {n["evidence_kind"] for n in callers + callees if n.get("evidence_kind")}
        ans.meta = r.meta(files=[c["path"] for c in citations], untrusted=["summary"], evidence=evidence)
        return ans.to_dict()


# ── business views ────────────────────────────────────────────────────────

def _node_citation(r: Reader, qn: str) -> Optional[dict]:
    row = r.conn.execute("SELECT file_path, line_start, line_end FROM code_nodes WHERE scan_id = ? AND "
                         "qualified_name = ? ORDER BY id LIMIT 1", (r.scan_id, qn)).fetchone()
    return {"path": r.rel(row[0]), "line_start": row[1], "line_end": row[2]} if row else None


def _as_names(items: Iterable[Any]) -> list[str]:
    out = []
    for it in items or []:
        if isinstance(it, dict):
            label = (f"{it.get('method', '')} {it.get('path', '')}".strip() if "path" in it and "method" in it
                     else it.get("name") or it.get("qualified_name") or it.get("id") or str(it))
            out.append(label)
        else:
            out.append(str(it))
    return out


def screen(project: Project, id_or_route: str) -> dict:
    key = (id_or_route or "").strip()
    key = key.split(":", 1)[1] if key.startswith("screen:") else key
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        if not r.has_table("screen_specs"):
            return not_found(id_or_route, [], r.meta()).to_dict()
        row = r.conn.execute("SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ? AND screen_id = ?",
                             (r.scan_id, key)).fetchone()
        route = None
        if row is None and r.has_table("screens"):
            hits = r.conn.execute("SELECT screen_id, path FROM screens WHERE scan_id = ? AND (path = ? OR "
                                  "lower(label) = lower(?)) ORDER BY screen_id", (r.scan_id, key, key)).fetchall()
            if len(hits) > 1:
                return ambiguous(id_or_route, [{"id": f"screen:{h[0]}", "route": h[1]} for h in hits],
                                 r.meta()).to_dict()
            if hits:
                row = r.conn.execute("SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ? AND "
                                     "screen_id = ?", (r.scan_id, hits[0][0])).fetchone()
        if row is None:
            return not_found(id_or_route, r.did_you_mean(key, types=["screen"]), r.meta()).to_dict()
        if r.has_table("screens"):
            hit = r.conn.execute("SELECT path FROM screens WHERE scan_id = ? AND screen_id = ?",
                                 (r.scan_id, row[0])).fetchone()
            route = hit[0] if hit else None
        spec = loads(row[1], {})
        ans = Answer()
        api_calls = spec.get("fe_api_calls") or []
        events = []
        for action in spec.get("user_actions") or []:
            if isinstance(action, dict):
                events.append({"ui_event": action.get("action") or action.get("name"),
                               "response": action.get("system_response") or action.get("response"),
                               "handler": action.get("handler"), "endpoint": action.get("endpoint")})
        for call in api_calls:
            if isinstance(call, dict):
                events.append({"ui_event": None, "handler": None,
                               "endpoint": f"{call.get('method', '')} {call.get('path', '')}".strip(),
                               "citation": {"path": call.get("source_file"), "line_start": call.get("line_number"),
                                            "line_end": call.get("line_number")} if call.get("source_file") else None})
        chain = []
        if spec.get("fe_component"):
            chain.append({"layer": "component", "node": spec["fe_component"], "citation": None})
        for layer, field in (("api", "fe_api_calls"), ("controller", "be_controllers"), ("service", "be_services")):
            for name in _as_names(spec.get(field)):
                chain.append({"layer": layer, "node": name,
                              "citation": _node_citation(r, name) if layer != "api" else None})
        for t in spec.get("db_tables") or []:
            chain.append({"layer": "table", "node": f"table:{str(t).lower()}", "citation": None})
        events = ans.cap("events", events, MAX_LIST)
        chain = ans.cap("chain", chain, MAX_LIST)
        ans.data = {"screen": {"id": row[0], "page_id": f"screen:{row[0]}",
                               "label": spec.get("screen_label") or row[0], "route": route},
                    "menu_path": spec.get("menu_path") or [], "interaction_mode": spec.get("interaction_mode"),
                    "crud_profile": spec.get("crud_profile"), "events": events, "chain": chain,
                    "tables": sorted({str(t).lower() for t in spec.get("db_tables") or []}),
                    "batch_jobs": _as_names(spec.get("batch_jobs") or (spec.get("related_docs") or {}).get("batch_jobs")),
                    "interfaces": _as_names(spec.get("external_interfaces")
                                            or (spec.get("related_docs") or {}).get("interfaces")),
                    "purpose": spec.get("purpose") or None}
        files = [c["citation"]["path"] for c in chain + events if c.get("citation")]
        ans.meta = r.meta(files=files, untrusted=["purpose", "events[].ui_event", "events[].response"])
        return ans.to_dict()


def entity(project: Project, name: str) -> dict:
    key = (name or "").strip()
    key = key.split(":", 1)[1] if key.startswith("entity:") else key
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        if not r.has_table("entity_state_machines"):
            return not_found(name, [], r.meta()).to_dict()
        rows = r.conn.execute("SELECT entity, entity_id, states_json, fields_json, confidence FROM entity_state_machines "
                              "WHERE scan_id = ? AND (entity_id = ? OR lower(entity) = lower(?)) ORDER BY entity_id",
                              (r.scan_id, key, key)).fetchall()
        if len(rows) > 1:
            return ambiguous(name, [{"id": f"entity:{x['entity_id']}", "entity": x["entity"]} for x in rows],
                             r.meta()).to_dict()
        if not rows:
            return not_found(name, r.did_you_mean(key, types=["entity"]), r.meta()).to_dict()
        row = rows[0]
        ans = Answer()
        transitions = [dict(t) for t in r.conn.execute(
            "SELECT field, from_state, to_state, trigger_function, guard_expr, confidence FROM state_transitions "
            "WHERE scan_id = ? AND entity_id = ? ORDER BY field, from_state, to_state, trigger_function",
            (r.scan_id, row["entity_id"]))]
        writers = sorted({t["trigger_function"] for t in transitions if t.get("trigger_function")})
        transitions = ans.cap("transitions", transitions, MAX_LIST * 2)
        writers = ans.cap("writers", writers, MAX_LIST)
        pid = f"entity:{row['entity_id']}"
        page = r.page_row(pid)
        _, backlinks = _page_relations(r, pid) if page is not None else ([], [])
        ans.data = {"entity": row["entity"], "entity_id": row["entity_id"], "page_id": pid if page else None,
                    "states": loads(row["states_json"], []), "fields": loads(row["fields_json"], []),
                    "transitions": transitions, "writers": writers,
                    "readers": sorted({b["page_id"] for b in backlinks if b["kind"] in ("reads", "see_also")}),
                    "confidence": row["confidence"], "citations": _citations(page)}
        ans.meta = r.meta(files=[c["path"] for c in ans.data["citations"]],
                          untrusted=["transitions[].guard_expr"])
        return ans.to_dict()


def flow(project: Project, flow_id: str) -> dict:
    key = (flow_id or "").strip()
    key = key.split(":", 1)[1] if key.startswith("flow:") else key
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        row = None
        if r.has_table("scenario_flows"):
            row = r.conn.execute("SELECT scenario_id, domain, steps_json, structured_steps_json, bpmn_xml, mermaid, "
                                 "confidence FROM scenario_flows WHERE scan_id = ? AND scenario_id = ?",
                                 (r.scan_id, key)).fetchone()
        if row is None:
            suggestions = []
            if r.has_table("scenario_flows"):
                low = key.lower().split(".")[-1]
                suggestions = [f"flow:{x[0]}" for x in r.conn.execute(
                    "SELECT scenario_id FROM scenario_flows WHERE scan_id = ? AND instr(lower(scenario_id), ?) > 0 "
                    "ORDER BY scenario_id LIMIT 5", (r.scan_id, low))]
                total = r.conn.execute("SELECT COUNT(*) FROM scenario_flows WHERE scan_id = ?",
                                       (r.scan_id,)).fetchone()[0]
            else:
                total = 0
            return not_found(flow_id, suggestions or r.did_you_mean(key, types=["flow"]), r.meta(),
                             flows_in_scan=total).to_dict()
        ans = Answer()
        structured = loads(row["structured_steps_json"], [])
        raw = structured or loads(row["steps_json"], [])
        steps = []
        for s in raw:
            if isinstance(s, dict):
                node = s.get("qualified_name") or s.get("node") or s.get("function") or s.get("name")
                steps.append({"node": node, "description": s.get("description") or s.get("action"),
                              "citation": _node_citation(r, node) if node else None})
            else:
                steps.append({"node": None, "description": str(s), "citation": None})
        steps = ans.cap("steps", steps, MAX_LIST * 2)
        ans.data = {"flow_id": row["scenario_id"], "domain": row["domain"], "steps": steps,
                    "bpmn_available": bool(row["bpmn_xml"]), "mermaid_available": bool(row["mermaid"]),
                    "confidence": row["confidence"], "stop_reason": None}
        ans.meta = r.meta(files=[s["citation"]["path"] for s in steps if s.get("citation")],
                          untrusted=["steps[].description"])
        return ans.to_dict()


def table(project: Project, name: str) -> dict:
    key = (name or "").strip()
    key = (key.split(":", 1)[1] if key.startswith("table:") else key).strip('"`[]').lower()
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        pid = f"table:{key}"
        page = r.page_row(pid)
        if page is None:
            return not_found(name, r.did_you_mean(key, types=["table"]), r.meta()).to_dict()
        ans = Answer()
        facts = _facts(page["facts_json"])
        rels = []
        if r.has_table("db_relationship"):
            rels = [dict(x) for x in r.conn.execute(
                "SELECT from_entity, to_entity, from_field, to_field, cardinality, source, inferred FROM db_relationship "
                "WHERE scan_id = ? AND (lower(from_entity) = ? OR lower(to_entity) = ?) ORDER BY from_entity, to_entity",
                (r.scan_id, key, key))]
        out_links, backlinks = _page_relations(r, pid)
        readers = sorted({b["page_id"] for b in backlinks if b["kind"] == "reads"})
        writers = sorted({b["page_id"] for b in backlinks if b["kind"] == "writes"})
        screens = sorted({f"screen:{s}" for s in facts.get("screens", [])}
                         | {b["page_id"] for b in backlinks if b["page_id"].startswith("screen:")})
        ans.data = {"table": key, "page_id": pid, "columns": facts.get("columns", []),
                    "relationships": ans.cap("relationships", rels, MAX_LIST * 2),
                    "readers": ans.cap("readers", readers, MAX_LIST), "writers": ans.cap("writers", writers, MAX_LIST),
                    "screens": ans.cap("screens", screens, MAX_LIST),
                    "procs": sorted({o["page_id"] for o in out_links + backlinks
                                     if o["page_id"].startswith("stored_proc:")}),
                    "citations": _citations(page)}
        ans.meta = r.meta(files=[c["path"] for c in ans.data["citations"]])
        return ans.to_dict()


__all__ = ["overview", "context", "screen", "entity", "flow", "table"]
