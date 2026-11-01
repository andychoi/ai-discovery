"""Structural flow, domain, onboarding, batch-job and stored-procedure pages (roadmap item M3.4).

Built from the scan's stored graph only (no LLM):

- **flow** — one page per entry point (``graph/entry_points.py``, M6.12), with the execution slice re-traced
  from the stored nodes and edges by ``ExecutionSliceBuilder``. Slices follow resolved edges only (ADR-0004
  following rules, M1.12.b), and every point where the trace stops is listed with its reason.
  ``max_flow_pages`` (``BuildContext.extra``, default 300) caps the count, highest-scored entry points first.
- **domain** — one page per classified domain: its entry points (→ flow pages), files, the tables its flows
  touch, and its onboarding tour.
- **onboarding** — a per-domain learning path, entry point first, then down the high-confidence call chain
  (``generators/onboarding_generator.build_tour_steps``); domains with fewer than 3 steps get none.
- **batch_job** — scheduled jobs and background services (entry kinds ``scheduled``/``background``).
- **stored_proc** — procedures found by M6.10, with callers, callees and the tables they read and write.

A page type whose data the scan does not have is **omitted**, never rendered empty. Links go only to pages
that exist (symbol pages for entry points and hubs, table pages built by ``business.tables``, …).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Optional

from ..model import Citation, Link, Page, Section, make_page_id
from ..registry import BuildContext, page_builder
from .code import Node, Snapshot, _footer, _md_table, _module_of, _symbol_page_ids

VERSION = "1"
DEFAULT_MAX_FLOW_PAGES = 300
TABLE_KINDS = ("reads_table", "writes_table")
JOB_KINDS = ("scheduled", "background")
# nodes the graph stage adds after call resolution: the pipeline slices without them (an `external::` callee
# is then a synthesized boundary, `external_boundary`), so the re-traced slice must too
POST_RESOLUTION_TYPES = frozenset({"external_system", "sql_table", "sql_view", "stored_procedure", "sql_statement"})
STOP_MEANING = {
    "leaf_no_callees": "calls nothing the scan can see",
    "depth_cap": "depth limit reached (more calls follow)",
    "cycle": "everything it calls is already on this flow",
    "unresolved_call": "makes calls that could not be resolved to a definition",
    "dynamic_dispatch": "dispatches dynamically (DI, reflection or too many candidates)",
    "external_boundary": "leaves the system (external API or queue)",
    "db_boundary": "reaches the database",
}
_SCHEDULE = re.compile(r"@?Scheduled\s*\(([^)]*)\)|\b(cron|fixedRate|fixedDelay|every)\s*[=:]\s*([^,)\n]+)")


def _json(raw, default):
    try:
        value = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


class FlowGraph:
    """Stored nodes/edges as graph objects, the scored entry points and their slices (cached per build)."""

    def __init__(self, ctx: BuildContext):
        from ...graph.call_graph import ExecutionSliceBuilder, UnresolvedCall
        from ...graph.entry_points import score_entry_points
        from ...graph.models import CallEdge, CodeNode

        self.snap = Snapshot.of(ctx)
        conn, sid = ctx.conn, ctx.scan_id
        self.code: dict[str, CodeNode] = {}
        self.node_of: dict[str, Node] = {}          # qualified name → snapshot node (first by id)
        qn_of: dict[int, str] = {}
        for r in conn.execute(
                "SELECT id, file_path, language, node_type, name, qualified_name, line_start, line_end, "
                "source_code, annotations, params, framework_hints, domain FROM code_nodes WHERE scan_id = ? "
                "ORDER BY id", (sid,)):
            qn_of[r[0]] = r[5]
            if r[5] in self.code:
                continue
            self.code[r[5]] = CodeNode(
                file_path=r[1] or "", language=r[2] or "", node_type=r[3], name=r[4], qualified_name=r[5],
                source_code=r[8] or "", line_start=r[6] or 0, line_end=r[7] or 0, annotations=_json(r[9], []),
                params=_json(r[10], []), framework_hints=_json(r[11], {}), domain=r[12])
            self.code[r[5]].bases = list(self.code[r[5]].framework_hints.get("bases") or [])
            self.node_of[r[5]] = self.snap.by_id[r[0]]
        self.edges: list[CallEdge] = []
        for a, b, kind, conf, origin, edge_kind in conn.execute(
                "SELECT caller_id, callee_id, edge_type, confidence, resolved_by, edge_kind FROM call_edges "
                "WHERE scan_id = ? AND callee_id IS NOT NULL ORDER BY id", (sid,)):
            if a in qn_of and b in qn_of:
                self.edges.append(CallEdge(qn_of[a], qn_of[b], kind or "direct_call",
                                           1.0 if conf is None else conf,
                                           {"resolved_by": origin or "", "edge_kind": edge_kind or "calls"}))
        try:
            unresolved = [UnresolvedCall(qn_of[c], name, reason) for c, name, reason in conn.execute(
                "SELECT caller_id, callee_name, reason FROM unresolved_calls WHERE scan_id = ? ORDER BY id", (sid,))
                if c in qn_of]
        except Exception:  # noqa: BLE001 — a store older than migration 0002
            unresolved = []
        self.out: dict[str, list[CallEdge]] = defaultdict(list)
        self.into: dict[str, list[CallEdge]] = defaultdict(list)
        for e in self.edges:
            self.out[e.caller].append(e)
            self.into[e.callee].append(e)
        resolved_nodes = [n for n in self.code.values() if n.node_type not in POST_RESOLUTION_TYPES]
        self.entries = score_entry_points(resolved_nodes, self.edges)
        self.slicer = ExecutionSliceBuilder(resolved_nodes, self.edges, unresolved)
        self.slicer.entry_kinds = {ep.node.qualified_name: ep.kind for ep in self.entries}
        self.picked = _symbol_page_ids(ctx, self.snap)
        cap = int(ctx.extra.get("max_flow_pages", DEFAULT_MAX_FLOW_PAGES))
        self.flow_entries = self.entries[:cap]
        self.flow_key = {ep.node.qualified_name: self.symbol_key(ep.node.qualified_name) for ep in self.flow_entries}
        self._scenarios: dict[str, object] = {}
        from .business import _all_tables, _table_key

        self.table_key = _table_key
        self.table_pages = set(_all_tables(ctx, self.snap))

    @classmethod
    def of(cls, ctx: BuildContext) -> "FlowGraph":
        g = ctx.extra.get("_flow_graph")
        if g is None:
            g = cls(ctx)
            ctx.extra["_flow_graph"] = g
        return g

    def symbol_key(self, qn: str) -> str:
        n = self.node_of.get(qn)
        return self.snap.symbol_key.get(n.id, qn) if n is not None else qn

    def scenario(self, qn: str):
        if qn not in self._scenarios:
            self._scenarios[qn] = self.slicer.build_scenario(self.code[qn])
        return self._scenarios[qn]

    # ── rendering helpers ─────────────────────────────────────────────────
    def ref(self, qn: str) -> str:
        n = self.node_of.get(qn)
        if n is not None and n.id in self.picked:
            return f"[`{qn}`]({make_page_id('symbol', self.snap.symbol_key[n.id])})"
        return f"`{qn}`"

    def where(self, qn: str) -> str:
        n = self.node_of.get(qn)
        return f"`{n.rel}:{n.line_start}`" if n is not None and n.rel else "—"

    def cite(self, qn: str) -> list[Citation]:
        n = self.node_of.get(qn)
        return self.snap.cite(n) if n is not None else []

    def symbol_link(self, qn: str, kind: str) -> list[Link]:
        n = self.node_of.get(qn)
        return [Link(make_page_id("symbol", self.snap.symbol_key[n.id]), kind)] if n is not None and n.id in self.picked else []

    def table_name(self, qn: str) -> str:
        n = self.code.get(qn)
        return n.name if n is not None else qn.split("::", 1)[-1]

    def table_ref(self, qn: str) -> str:
        key = self.table_key(self.table_name(qn))
        return f"[{key}]({make_page_id('table', key)})" if key in self.table_pages else f"`{self.table_name(qn)}`"

    def touches(self, qns) -> dict[str, set[str]]:
        """Tables read/written, procedures called and external systems reached by *qns*."""
        out: dict[str, set[str]] = {"reads": set(), "writes": set(), "procs": set(), "externals": set()}
        for qn in qns:
            for e in self.out.get(qn, []):
                kind = (e.metadata or {}).get("edge_kind")
                target = self.code.get(e.callee)
                if kind == "reads_table":
                    out["reads"].add(e.callee)
                elif kind == "writes_table":
                    out["writes"].add(e.callee)
                elif kind == "calls_proc":
                    out["procs"].add(e.callee)
                elif target is not None and target.node_type == "external_system":
                    out["externals"].add(e.callee)
        return out


def proc_key(node) -> str:
    return str((node.framework_hints or {}).get("procedure") or node.qualified_name.split("::", 1)[-1])


def _touch_sections(g: FlowGraph, t: dict[str, set[str]]) -> tuple[list[Section], list[Link]]:
    rows = [[g.table_ref(q), "reads"] for q in sorted(t["reads"] - t["writes"])]
    rows += [[g.table_ref(q), "writes" + (" + reads" if q in t["reads"] else "")] for q in sorted(t["writes"])]
    rows += [[f"[{proc_key(g.code[q])}]({make_page_id('stored_proc', proc_key(g.code[q]))})", "calls procedure"]
             for q in sorted(t["procs"])]
    rows += [[f"[{g.code[q].name}]({make_page_id('interface', g.code[q].name)})", "calls external system"]
             for q in sorted(t["externals"])]
    links: list[Link] = []
    for q in t["reads"] | t["writes"]:
        key = g.table_key(g.table_name(q))
        if key in g.table_pages:
            links.append(Link(make_page_id("table", key), "writes" if q in t["writes"] else "reads"))
    links += [Link(make_page_id("stored_proc", proc_key(g.code[q])), "calls") for q in t["procs"]]
    links += [Link(make_page_id("interface", g.code[q].name), "calls") for q in t["externals"]]
    return [Section("Data and systems", _md_table(["Target", "Access"], rows) if rows
                    else "_no table, procedure or external system reached_")], links


# ── flows ─────────────────────────────────────────────────────────────────

@page_builder("flows.flows", emits={"flow"}, inputs=("code_nodes", "call_edges", "unresolved_calls"),
              version=VERSION)
def build_flows(ctx: BuildContext):
    g = FlowGraph.of(ctx)
    for ep in g.flow_entries:
        qn = ep.node.qualified_name
        sc = g.scenario(qn)
        reached = [n.id for n in sc.nodes]
        hints = ep.node.framework_hints or {}
        trigger = [f"- **Kind:** {ep.kind} ({sc.trigger_type})",
                   f"- **Entry point:** {g.ref(qn)} ({g.where(qn)})",
                   f"- **Evidence:** {', '.join(f'`{e}`' for e in ep.evidence)} (score {ep.score:.2f})"]
        if hints.get("route"):
            trigger.insert(0, f"- **Route:** `{(hints.get('method') or 'ANY').upper()} {hints['route']}`")
        steps = [[str(i), g.ref(s), g.code[s].node_type if s in g.code else "unresolved", g.where(s)]
                 for i, s in enumerate(sc.primary_path, start=1)]
        others = sorted(set(reached) - set(sc.primary_path))
        stops = sorted(sc.stop_reasons.items())
        touched = g.touches(reached)
        touch_sections, touch_links = _touch_sections(g, touched)
        sections = [
            Section("Trigger", "\n".join(trigger)),
            Section("Main path", _md_table(["#", "Step", "Kind", "Where"], steps)),
            Section("Also reached", "\n".join(f"- {g.ref(s)}" for s in others[:100])
                    + (f"\n- …and {len(others) - 100} more" if len(others) > 100 else "")
                    if others else "_nothing beyond the main path_"),
            Section("Where the trace stops", _md_table(
                ["At", "Reason", "Meaning"],
                ([g.ref(at), f"`{why}`", STOP_MEANING.get(why, "")] for at, why in stops))),
            *touch_sections,
            Section("Provenance", "Traced from the stored call graph: only resolved edges are followed "
                    "(no name-only guesses), up to depth 5. " + _footer(ctx).body_md),
        ]
        links = g.symbol_link(qn, "implements")
        links += [lk for s in reached if s != qn for lk in g.symbol_link(s, "calls")]
        links += touch_links
        if ep.node.domain:
            links.append(Link(make_page_id("domain", ep.node.domain), "belongs_to"))
        yield Page(make_page_id("flow", g.flow_key[qn]), f"Flow: {ep.node.name}",
                   facts={"entry": qn, "kind": ep.kind, "trigger": sc.trigger_type, "score": ep.score,
                          "evidence": list(ep.evidence), "main_path": list(sc.primary_path),
                          "reached": len(reached), "stop_reasons": dict(stops),
                          "reads": sorted(g.table_name(q) for q in touched["reads"]),
                          "writes": sorted(g.table_name(q) for q in touched["writes"]),
                          "procedures": sorted(proc_key(g.code[q]) for q in touched["procs"]),
                          "externals": sorted(g.code[q].name for q in touched["externals"]),
                          "domain": ep.node.domain},
                   sections=sections, links=links,
                   citations=[c for s in [qn, *sc.primary_path] for c in g.cite(s)])


# ── domains + onboarding ──────────────────────────────────────────────────

def _domain_entries(g: FlowGraph) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for ep in g.flow_entries:
        if ep.node.domain:
            out[ep.node.domain].append(ep)
    return out


def _tour(g: FlowGraph, name: str, members: list[Node], entries: list) -> list:
    from ...generators.onboarding_generator import _MIN_STEPS, build_tour_steps
    from ...graph.models import Domain

    nodes = [g.code[n.qualified_name] for n in members if n.qualified_name in g.code]
    steps = build_tour_steps(Domain(name=name, nodes=nodes, entry_points=[ep.node for ep in entries]), g.edges, {})
    return steps if len(steps) >= _MIN_STEPS else []


def _domain_members(g: FlowGraph) -> dict[str, list[Node]]:
    out: dict[str, list[Node]] = defaultdict(list)
    for n in g.snap.nodes:
        if n.domain and n.rel is not None and n.node_type not in ("external_system", "sql_table"):
            out[n.domain].append(n)
    return out


@page_builder("flows.domains", emits={"domain"}, inputs=("domains", "code_nodes", "call_edges"), version=VERSION)
def build_domains(ctx: BuildContext):
    g = FlowGraph.of(ctx)
    members = _domain_members(g)
    entries = _domain_entries(g)
    for name, node_count, _raw in g.snap.domains:
        mine = members.get(name, [])
        eps = entries.get(name, [])
        files = sorted({n.rel for n in mine})
        modules = sorted({_module_of(f) for f in files})
        touched = g.touches([ep.node.qualified_name for ep in eps] + [s for ep in eps
                                                                      for s in g.scenario(ep.node.qualified_name).primary_path])
        touch_sections, touch_links = _touch_sections(g, touched)
        has_tour = bool(_tour(g, name, mine, eps))
        sections = [
            Section("Summary", "\n".join([f"- **Symbols:** {node_count or len(mine)}", f"- **Files:** {len(files)}",
                                          f"- **Entry points:** {len(eps)}",
                                          f"- **Onboarding tour:** [{name}]({make_page_id('onboarding', name)})"
                                          if has_tour else "- **Onboarding tour:** _too small for a tour_"])),
            Section("Entry points", _md_table(
                ["Flow", "Kind", "Where"],
                ([f"[{ep.node.name}]({make_page_id('flow', g.flow_key[ep.node.qualified_name])})", ep.kind,
                  g.where(ep.node.qualified_name)] for ep in eps))),
            Section("Modules", "\n".join(f"- [{m}]({make_page_id('module', m)})" for m in modules) or "_none_"),
            *touch_sections,
            _footer(ctx),
        ]
        links = [Link(make_page_id("overview", "system"), "parent")]
        links += [Link(make_page_id("flow", g.flow_key[ep.node.qualified_name]), "child") for ep in eps]
        links += [Link(make_page_id("module", m), "link") for m in modules]
        if has_tour:
            links.append(Link(make_page_id("onboarding", name), "see_also"))
        links += touch_links
        yield Page(make_page_id("domain", name), name,
                   facts={"symbols": node_count or len(mine), "files": len(files), "modules": modules,
                          "entry_points": [ep.node.qualified_name for ep in eps], "onboarding": has_tour},
                   sections=sections, links=links)


@page_builder("flows.onboarding", emits={"onboarding"}, inputs=("domains", "code_nodes", "call_edges"),
              version=VERSION)
def build_onboarding(ctx: BuildContext):
    g = FlowGraph.of(ctx)
    members = _domain_members(g)
    entries = _domain_entries(g)
    for name, _count, _raw in g.snap.domains:
        steps = _tour(g, name, members.get(name, []), entries.get(name, []))
        if not steps:
            continue
        rows = [[str(s.order), g.ref(s.qualified_name), s.node_type, g.where(s.qualified_name),
                 str(s.fan_in), str(s.fan_out)] for s in steps]
        first = steps[0].qualified_name
        start = (f"Start at {g.ref(first)} — " + (
            f"an entry point; its [flow]({make_page_id('flow', g.flow_key[first])}) shows what it reaches."
            if first in g.flow_key else "the most-called symbol (this domain has no entry point)."))
        sections = [
            Section("Start here", start),
            Section("Learning path", _md_table(["#", "Symbol", "Kind", "Where", "Called by", "Calls"], rows)),
            Section("How this path is chosen", "Entry points first, then breadth-first along calls of confidence "
                    "≥ 0.8 inside the domain, at most 15 steps."),
            _footer(ctx),
        ]
        links = [Link(make_page_id("domain", name), "belongs_to")]
        if first in g.flow_key:
            links.append(Link(make_page_id("flow", g.flow_key[first]), "see_also"))
        links += [lk for s in steps for lk in g.symbol_link(s.qualified_name, "link")]
        yield Page(make_page_id("onboarding", name), f"Onboarding: {name}",
                   facts={"domain": name, "steps": [s.qualified_name for s in steps]},
                   sections=sections, links=links,
                   citations=[c for s in steps for c in g.cite(s.qualified_name)])


# ── batch jobs ────────────────────────────────────────────────────────────

def _schedule(node) -> Optional[str]:
    header = (node.source_code or "")[:600]
    m = _SCHEDULE.search(header)
    if m:
        return (m.group(1) if m.group(1) is not None else f"{m.group(2)} = {m.group(3)}").strip() or None
    for a in node.annotations or []:
        if "schedul" in a.lower() or "cron" in a.lower():
            return a
    return None


@page_builder("flows.batch_jobs", emits={"batch_job"}, inputs=("code_nodes", "call_edges"), version=VERSION)
def build_batch_jobs(ctx: BuildContext):
    g = FlowGraph.of(ctx)
    for ep in g.entries:
        if ep.kind not in JOB_KINDS and ep.node.node_type != "batch_job":
            continue
        qn = ep.node.qualified_name
        sc = g.scenario(qn)
        touch_sections, touch_links = _touch_sections(g, g.touches([n.id for n in sc.nodes]))
        schedule = _schedule(ep.node)
        flow = (f"[{ep.node.name}]({make_page_id('flow', g.flow_key[qn])})" if qn in g.flow_key else "_not traced_")
        sections = [
            Section("Job", "\n".join([
                f"- **Runs:** {g.ref(qn)} ({g.where(qn)})",
                f"- **Kind:** {'background service' if ep.kind == 'background' else 'scheduled job'}",
                f"- **Schedule:** `{schedule}`" if schedule else "- **Schedule:** _not stated in code_",
                f"- **Evidence:** {', '.join(f'`{e}`' for e in ep.evidence)}",
                f"- **Flow:** {flow}"])),
            *touch_sections,
            _footer(ctx),
        ]
        links = g.symbol_link(qn, "implements") + touch_links
        if qn in g.flow_key:
            links.append(Link(make_page_id("flow", g.flow_key[qn]), "see_also"))
        yield Page(make_page_id("batch_job", g.symbol_key(qn)), ep.node.name,
                   facts={"symbol": qn, "kind": ep.kind, "schedule": schedule},
                   sections=sections, links=links, citations=g.cite(qn))


# ── stored procedures ─────────────────────────────────────────────────────

@page_builder("flows.stored_procs", emits={"stored_proc"}, inputs=("code_nodes", "call_edges"), version=VERSION)
def build_stored_procs(ctx: BuildContext):
    g = FlowGraph.of(ctx)
    procs = sorted((n for n in g.code.values() if n.node_type == "stored_procedure"), key=proc_key)
    reached_by: dict[str, list[str]] = defaultdict(list)
    for ep in g.flow_entries:
        for q in g.touches([n.id for n in g.scenario(ep.node.qualified_name).nodes])["procs"]:
            reached_by[q].append(ep.node.qualified_name)
    seen: set[str] = set()
    for p in procs:
        key = proc_key(p)
        if key in seen:
            continue
        seen.add(key)
        qn = p.qualified_name
        callers = sorted(e.caller for e in g.into.get(qn, []) if (e.metadata or {}).get("edge_kind") == "calls_proc")
        touched = g.touches([qn])
        defined = bool((p.framework_hints or {}).get("defined"))

        def proc_ref(q: str) -> str:
            n = g.code.get(q)
            if n is not None and n.node_type == "stored_procedure":
                return f"[{proc_key(n)}]({make_page_id('stored_proc', proc_key(n))})"
            return g.ref(q)

        flows = sorted(reached_by.get(qn, []))
        sections = [
            Section("Definition", f"- **Defined in:** {g.where(qn)}" if defined
                    else "- **Defined in:** _not in this repository (called by name only)_"),
            Section("Called by", "\n".join(f"- {proc_ref(c)}" for c in callers) or "_no resolved callers_"),
            *_touch_sections(g, touched)[0],
            Section("Reached by flows", "\n".join(
                f"- [{g.code[f].name}]({make_page_id('flow', g.flow_key[f])})" for f in flows) or "_none_"),
            _footer(ctx),
        ]
        links = _touch_sections(g, touched)[1]
        links += [Link(make_page_id("stored_proc", proc_key(g.code[c])), "called_by") for c in callers
                  if g.code.get(c) is not None and g.code[c].node_type == "stored_procedure"]
        links += [lk for c in callers for lk in g.symbol_link(c, "called_by")]
        links += [Link(make_page_id("flow", g.flow_key[f]), "see_also") for f in flows]
        yield Page(make_page_id("stored_proc", key), key,
                   facts={"defined": defined, "callers": callers,
                          "reads": sorted(g.table_name(q) for q in touched["reads"]),
                          "writes": sorted(g.table_name(q) for q in touched["writes"]),
                          "calls": sorted(proc_key(g.code[q]) for q in touched["procs"])},
                   sections=sections, links=links, citations=g.cite(qn) if defined else [])
