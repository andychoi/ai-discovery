"""Structural business pages: screen, endpoint, entity, table, interface (roadmap item M3.3).

Built from the scan's DB rows only (no LLM): ``screen_specs`` (structural or
LLM specs — prose sections are shown only when present), endpoint and
external-system nodes, ``entity_state_machines``/``state_transitions`` (FSM
diagram + DMN decision tables + EARS requirements via the existing
generators), ``db_relationship`` and ``db_model`` nodes for tables.
All cross-links point at pages built here or by the code builders.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Optional

from ..model import Citation, Link, Page, Section, make_page_id
from ..registry import BuildContext, page_builder
from .code import FOOTER, Snapshot, _footer, _md_table, _symbol_page_ids

VERSION = "2"
WRITE_CRUD = {"create", "update", "delete", "crud", "write", "read_write", "create_update"}


# ── shared loading ────────────────────────────────────────────────────────

def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _screens(ctx: BuildContext) -> list[dict]:
    rows = ctx.conn.execute("SELECT screen_id, spec_json FROM screen_specs WHERE scan_id = ? ORDER BY screen_id",
                            (ctx.scan_id,)).fetchall()
    return [{**_json(spec, {}), "screen_id": sid} for sid, spec in rows]


def _table_key(name: str) -> str:
    return name.strip().strip('"`[]').lower()


def _db_models(snap: Snapshot, ctx: BuildContext) -> dict[str, dict]:
    """table key -> {class node, columns{name:type}, relationships[]} from db_model nodes."""
    out: dict[str, dict] = {}
    rows = ctx.conn.execute(
        "SELECT id, framework_hints FROM code_nodes WHERE scan_id = ? AND node_type = 'db_model' ORDER BY id",
        (ctx.scan_id,)).fetchall()
    for nid, hints in rows:
        h = _json(hints, {})
        table = h.get("table") or snap.by_id[nid].name
        out.setdefault(_table_key(table), {"node": snap.by_id[nid], "columns": h.get("field_types", {}),
                                            "relationships": h.get("relationships", [])})
    return out


def _relationships(ctx: BuildContext, snap: Snapshot) -> list[dict]:
    rows = ctx.conn.execute(
        "SELECT from_entity, to_entity, from_field, to_field, cardinality, source, source_file, source_line, "
        "inferred FROM db_relationship WHERE scan_id = ? ORDER BY from_entity, to_entity, from_field",
        (ctx.scan_id,)).fetchall()
    from .code import _rel
    return [{"from": _table_key(r[0]), "to": _table_key(r[1]), "from_field": r[2] or "", "to_field": r[3] or "",
             "cardinality": r[4] or "", "source": r[5] or "", "file": _rel(r[6], snap.repo) if r[6] else None,
             "line": r[7] or 0, "inferred": bool(r[8])} for r in rows]


def _cite_line(snap: Snapshot, rel: Optional[str], line: int) -> list[Citation]:
    if not rel or line < 1:
        return []
    total = snap.line_count(rel)
    return [Citation(rel, line, line)] if total and line <= total else []


# ── screens ───────────────────────────────────────────────────────────────

def _screen_access(spec: dict) -> str:
    crud = (spec.get("crud_profile") or "").lower()
    return "writes" if crud in WRITE_CRUD or "write" in crud or "update" in crud else "reads"


@page_builder("business.screens", emits={"screen"},
              inputs=("screen_specs", "code_nodes", "call_edges", "state_transitions", "entity_state_machines"),
              version=VERSION)
def build_screens(ctx: BuildContext):
    specs = list(_screens(ctx))
    g = None
    if specs:
        from .flows import FlowGraph, transition_rows

        g = FlowGraph.of(ctx)
    for spec in specs:
        sid = spec["screen_id"]
        tables = sorted({_table_key(t) for t in spec.get("db_tables") or []})
        access = _screen_access(spec)
        menu = " › ".join(spec.get("menu_path") or []) or "—"
        sections = [Section("Where", "\n".join([
            f"- **Menu:** {menu}",
            f"- **Component:** `{spec['fe_component']}`" if spec.get("fe_component") else "- **Component:** —",
            f"- **Interaction:** {spec.get('interaction_mode') or '—'}; data access: {spec.get('crud_profile') or '—'}",
        ]))]
        if spec.get("purpose"):
            sections.append(Section("Purpose", spec["purpose"]))
        flow_rows = [["API call", f"`{c}`" if isinstance(c, str) else f"`{json.dumps(c, sort_keys=True)}`"]
                     for c in spec.get("fe_api_calls") or []]
        flow_rows += [["Controller", f"`{getattr_name(c)}`"] for c in spec.get("be_controllers") or []]
        flow_rows += [["Service", f"`{getattr_name(c)}`"] for c in spec.get("be_services") or []]
        sections.append(Section("Actions → backend", _md_table(["Step", "Target"], flow_rows)))
        started = sorted(g.screen_flows(spec), key=lambda x: (-x[2], x[0])) if g is not None else []
        if started:
            sections.append(Section("Flows it starts", _md_table(
                ["Flow", "Matched by", "Mapping confidence"],
                ([g.flow_ref(e), how, f"{conf:.2f}"] for e, how, conf in started))))
            changes = g.state_changes({n for e, _h, _c in started for n in [e, *(x.id for x in g.scenario(e).nodes)]})
            if changes:
                sections.append(Section("Business state it can change", _md_table(
                    ["Entity", "Field", "Change", "Done by", "Guard"], transition_rows(g, changes))))
        sections.append(Section("Data", "\n".join(
            f"- [{t}]({make_page_id('table', t)}) ({access})" for t in tables) or "_no tables mapped_"))
        if spec.get("batch_jobs") or spec.get("external_interfaces"):
            sections.append(Section("Downstream", "\n".join(
                [f"- batch job `{b}`" for b in spec.get("batch_jobs") or []]
                + [f"- interface `{getattr_name(i)}`" for i in spec.get("external_interfaces") or []])))
        if spec.get("permissions"):
            sections.append(Section("Permissions", "\n".join(f"- {p}" for p in spec["permissions"])))
        if spec.get("rules"):
            sections.append(Section("Rules", "\n".join(f"- {getattr_name(r)}" for r in spec["rules"])))
        sections.append(Section("Provenance", (FOOTER if spec.get("model") in (None, "", "structural")
                                               else f"Screen spec written by {spec.get('model')} "
                                                    f"(confidence {spec.get('confidence')}).")
                                + (f" Scan of commit `{ctx.commit[:12]}`." if ctx.commit else "")))
        links = [Link(make_page_id("table", t), access) for t in tables]
        links += [Link(make_page_id("flow", g.flow_key[e]), "routes_to") for e in sorted({e for e, _h, _c in started})]
        hashes = {k: v for k, v in (spec.get("source_hashes") or {}).items() if isinstance(v, str)}
        yield Page(make_page_id("screen", sid), spec.get("screen_label") or sid,
                   facts={"menu_path": spec.get("menu_path") or [], "tables": tables, "access": access,
                          "flows": sorted({g.flow_key[e] for e, _h, _c in started}),
                          "crud_profile": spec.get("crud_profile"), "batch_jobs": spec.get("batch_jobs") or []},
                   sections=sections, links=links, source_hashes=hashes,
                   confidence=float(spec.get("confidence") or 1.0) if spec.get("model") not in (None, "", "structural") else 1.0)


def getattr_name(v) -> str:
    """Display name for mapping entries that may be strings or dicts."""
    if isinstance(v, dict):
        for k in ("class_name", "name", "qualified_name", "path", "id"):
            if v.get(k):
                return str(v[k])
        return json.dumps(v, sort_keys=True)
    return str(v)


# ── endpoints ─────────────────────────────────────────────────────────────

@page_builder("business.endpoints", emits={"endpoint"}, inputs=("code_nodes", "call_edges"), version=VERSION)
def build_endpoints(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    picked = _symbol_page_ids(ctx, snap)
    rows = ctx.conn.execute(
        "SELECT id, framework_hints FROM code_nodes WHERE scan_id = ? AND node_type = 'endpoint' ORDER BY id",
        (ctx.scan_id,)).fetchall()
    keyed: dict[str, list] = defaultdict(list)
    for nid, hints in rows:
        h = _json(hints, {})
        n = snap.by_id[nid]
        route = h.get("route") or n.name
        method = (h.get("method") or "ANY").upper()
        keyed[f"{method} {route}"].append((n, h))
    for key, members in sorted(keyed.items()):
        for n, h in members:
            page_key = key if len(members) == 1 else f"{key}@{n.rel}:{n.line_start}"
            callees = sorted((snap.by_id[c] for c in snap.callees[n.id]), key=lambda o: o.qualified_name)
            sections = [
                Section("Route", "\n".join([f"- **Method:** {key.split(' ', 1)[0]}",
                                            f"- **Path:** `{key.split(' ', 1)[1]}`",
                                            f"- **Handler:** `{n.qualified_name}` (`{n.rel}:{n.line_start}`)"])),
                Section("Calls", "\n".join(f"- `{o.qualified_name}`" for o in callees[:100]) or "_no resolved calls_"),
                _footer(ctx),
            ]
            links = [Link(make_page_id("symbol", snap.symbol_key[n.id]), "implements")] if n.id in picked else []
            links += [Link(make_page_id("symbol", snap.symbol_key[o.id]), "calls") for o in callees if o.id in picked]
            yield Page(make_page_id("endpoint", page_key), key,
                       facts={"method": key.split(" ", 1)[0], "path": key.split(" ", 1)[1],
                              "handler": n.qualified_name, "calls": len(callees)},
                       sections=sections, links=links, citations=snap.cite(n))


# ── entities (FSM + DMN + EARS) ───────────────────────────────────────────

def _load_fsms(ctx: BuildContext):
    from ...graph.models import EntityStateMachine, StateTransition

    fsms = []
    for ent, eid, states, fields, files, conf, meta in ctx.conn.execute(
            "SELECT entity, entity_id, states_json, fields_json, source_files_json, confidence, metadata_json "
            "FROM entity_state_machines WHERE scan_id = ? ORDER BY entity_id, entity", (ctx.scan_id,)):
        trans = [StateTransition(entity=t[0], entity_id=t[1], field=t[2] or "", from_state=t[3], to_state=t[4],
                                 trigger_function=t[5], guard_expr=t[6], confidence=t[7] or 1.0)
                 for t in ctx.conn.execute(
                     "SELECT entity, entity_id, field, from_state, to_state, trigger_function, guard_expr, confidence "
                     "FROM state_transitions WHERE scan_id = ? AND entity_id = ? "
                     "ORDER BY field, from_state, to_state, trigger_function", (ctx.scan_id, eid))]
        fsms.append(EntityStateMachine(entity=ent, entity_id=eid or ent, transitions=trans,
                                       states=set(_json(states, [])), fields=set(_json(fields, [])),
                                       source_files=set(_json(files, [])), confidence=conf or 1.0,
                                       metadata=_json(meta, {})))
    return fsms


_STATE_ID = re.compile(r"[^A-Za-z0-9_]")


def _state_diagram(fsm) -> str:
    if not fsm.transitions:
        return "_No state transitions detected for this entity._"
    sid = lambda s: "s_" + _STATE_ID.sub("_", s) if s else "[*]"  # noqa: E731
    lines = ["```mermaid", "stateDiagram-v2"]
    for t in sorted(fsm.transitions, key=lambda t: (t.field or "", t.from_state or "", t.to_state or "",
                                                    t.trigger_function or "")):
        label = (t.trigger_function or "").split(".")[-1]
        lines.append(f"    {sid(t.from_state)} --> {sid(t.to_state)}" + (f" : {label}" if label else ""))
    for s in sorted(fsm.states):
        lines.append(f'    {sid(s)} : {s.replace(chr(34), "")}')
    lines.append("```")
    return "\n".join(lines)


def _strip_h1(md: str) -> str:
    return "\n".join(line for line in md.splitlines() if not line.startswith("# ")).strip()


def entity_key(fsm) -> str:
    return fsm.entity_id or fsm.entity


DRIVER_CAP = 5
LIMITS = ("Each arrow is a literal state assignment in the code. **Not visible from code:** who performs a step, "
          "approvals or work done outside this system, how long a state waits, and how often each path is taken "
          "(ADR-0007). Add what you know as a note on this page.")


def _drivers(g, fsm) -> str:
    """What drives each lifecycle change: trigger, guard, the flows that reach the trigger, their screens."""
    rows = []
    for t in sorted(fsm.transitions, key=lambda t: (t.field or "", t.from_state or "", t.to_state or "",
                                                    t.trigger_function or "")):
        flows = g.flows_through(t.trigger_function) if t.trigger_function else []
        screens = sorted({s for e in flows for s in g.entry_screens().get(e, [])})
        shown = ", ".join(g.flow_ref(e) for e in flows[:DRIVER_CAP]) + (
            f", …and {len(flows) - DRIVER_CAP} more" if len(flows) > DRIVER_CAP else "")
        rows.append([f"{t.from_state or '(new)'} → {t.to_state or '?'}", f"`{t.field}`" if t.field else "—",
                     g.ref(t.trigger_function) if t.trigger_function else "—",
                     f"`{t.guard_expr}`" if t.guard_expr else "—", shown or "_no traced flow reaches it_",
                     ", ".join(f"[{label}]({make_page_id('screen', sid)})" for sid, label in screens[:DRIVER_CAP])
                     or "—"])
    return _md_table(["Change", "Field", "Done by", "Guard", "Reached from flows", "Screens"], rows)


@page_builder("business.entities", emits={"entity"},
              inputs=("entity_state_machines", "state_transitions", "code_nodes", "call_edges", "screen_specs"),
              version=VERSION)
def build_entities(ctx: BuildContext):
    from ...generators.dmn_generator import generate_entity_decisions_markdown
    from ...generators.ears_generator import generate_entity_ears_markdown
    from .code import _rel
    from .flows import FlowGraph

    snap = Snapshot.of(ctx)
    tables = set(_all_tables(ctx, snap))
    fsms = _load_fsms(ctx)
    g = FlowGraph.of(ctx) if any(f.transitions for f in fsms) else None
    for fsm in fsms:
        files = sorted(r for r in (_rel(f, snap.repo) for f in fsm.source_files) if r)
        sections = [
            Section("Lifecycle", _state_diagram(fsm)),
            *([Section("What drives each change", _drivers(g, fsm) + "\n\n" + LIMITS)]
              if g is not None and fsm.transitions else []),
            Section("Fields", ", ".join(f"`{f}`" for f in sorted(fsm.fields)) or "_none recorded_"),
            Section("Decision table (DMN)", _strip_h1(generate_entity_decisions_markdown([fsm], []))),
            Section("Requirements (EARS)", _strip_h1(generate_entity_ears_markdown([fsm], []))),
            Section("Defined in", "\n".join(f"- `{f}`" for f in files) or "_unknown_"),
            _footer(ctx),
        ]
        links = []
        if _table_key(fsm.entity) in tables:
            links.append(Link(make_page_id("table", _table_key(fsm.entity)), "see_also"))
        yield Page(make_page_id("entity", entity_key(fsm)), fsm.entity,
                   facts={"states": sorted(fsm.states), "fields": sorted(fsm.fields),
                          "transitions": len(fsm.transitions),
                          "kind": fsm.metadata.get("entity_kind"), "files": files},
                   sections=sections, links=links, confidence=min(1.0, max(0.0, float(fsm.confidence))))


# ── tables ────────────────────────────────────────────────────────────────

def _all_tables(ctx: BuildContext, snap: Snapshot) -> list[str]:
    names = set(_db_models(snap, ctx))
    for r in _relationships(ctx, snap):
        names.update((r["from"], r["to"]))
    for spec in _screens(ctx):
        names.update(_table_key(t) for t in spec.get("db_tables") or [])
    for fsm in _load_fsms(ctx):
        if fsm.metadata.get("node_type") == "sql_table":
            names.add(_table_key(fsm.entity))
    return sorted(n for n in names if n)


@page_builder("business.tables", emits={"table"},
              inputs=("code_nodes", "db_relationship", "screen_specs", "entity_state_machines"), version=VERSION)
def build_tables(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    picked = _symbol_page_ids(ctx, snap)
    models = _db_models(snap, ctx)
    rels = _relationships(ctx, snap)
    fsm_by_table = {_table_key(f.entity): f for f in _load_fsms(ctx)}
    screens_by_table: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for spec in _screens(ctx):
        for t in {_table_key(x) for x in spec.get("db_tables") or []}:
            screens_by_table[t].append((spec["screen_id"], _screen_access(spec)))

    for table in _all_tables(ctx, snap):
        model = models.get(table)
        fsm = fsm_by_table.get(table)
        columns = dict(model["columns"]) if model else {c: "" for c in sorted(fsm.fields)} if fsm else {}
        out_rels = [r for r in rels if r["from"] == table]
        in_rels = [r for r in rels if r["to"] == table and r["from"] != table]
        citations: list[Citation] = []
        for r in out_rels:
            citations += _cite_line(snap, r["file"], r["line"])
        if model:
            citations += snap.cite(model["node"])

        def rel_row(r, other_key: str) -> list[str]:
            cols = f"{r['from_field'] or '?'} → {r['to_field'] or 'id'}"
            how = r["source"] + (" (inferred)" if r["inferred"] else "")
            return [f"[{r[other_key]}]({make_page_id('table', r[other_key])})", r["cardinality"] or "—", cols, how]

        users = sorted(screens_by_table.get(table, []))
        sections = [
            Section("Columns", _md_table(["Column", "Type"], ([f"`{c}`", t or "—"] for c, t in sorted(columns.items())))),
            Section("References", _md_table(["Table", "Cardinality", "Columns", "Source"], (rel_row(r, "to") for r in out_rels))),
            Section("Referenced by", _md_table(["Table", "Cardinality", "Columns", "Source"], (rel_row(r, "from") for r in in_rels))),
            Section("Used by screens", "\n".join(f"- [{s}]({make_page_id('screen', s)}) ({a})" for s, a in users)
                    or "_no screen mapping references this table_"),
        ]
        if model:
            n = model["node"]
            label = f"`{n.qualified_name}`"
            sections.append(Section("Mapped by", f"- {label} (`{n.rel}:{n.line_start}`)"))
        sections.append(_footer(ctx))
        links = [Link(make_page_id("table", r["to"]), "see_also") for r in out_rels]
        links += [Link(make_page_id("table", r["from"]), "see_also") for r in in_rels]
        links += [Link(make_page_id("screen", s), "see_also") for s, _ in users]
        if model and model["node"].id in picked:
            links.append(Link(make_page_id("symbol", snap.symbol_key[model["node"].id]), "implements"))
        if fsm:
            links.append(Link(make_page_id("entity", entity_key(fsm)), "see_also"))
        yield Page(make_page_id("table", table), table,
                   facts={"columns": sorted(columns), "references": sorted({r["to"] for r in out_rels}),
                          "referenced_by": sorted({r["from"] for r in in_rels}),
                          "screens": [s for s, _ in users]},
                   sections=sections, links=links, citations=citations)


# ── interfaces ────────────────────────────────────────────────────────────

@page_builder("business.interfaces", emits={"interface"}, inputs=("code_nodes", "call_edges", "screen_specs"),
              version=VERSION)
def build_interfaces(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    systems: dict[str, list] = defaultdict(list)
    for n in snap.nodes:
        if n.node_type == "external_system":
            systems[n.name].append(n)
    screen_refs: dict[str, list[str]] = defaultdict(list)
    for spec in _screens(ctx):
        for i in spec.get("external_interfaces") or []:
            screen_refs[getattr_name(i)].append(spec["screen_id"])
            systems.setdefault(getattr_name(i), [])
    hints = {nid: _json(h, {}) for nid, h in ctx.conn.execute(
        "SELECT id, framework_hints FROM code_nodes WHERE scan_id = ? AND node_type = 'external_system'",
        (ctx.scan_id,))}
    for name in sorted(systems):
        nodes = systems[name]
        callers = sorted({snap.by_id[c].qualified_name for n in nodes for c in snap.callers[n.id]})
        kinds = sorted({str(hints.get(n.id, {}).get("kind") or hints.get(n.id, {}).get("protocol") or "external")
                        for n in nodes}) or ["external"]
        sections = [
            Section("System", f"- **Kind:** {', '.join(kinds)}\n- **Detected at:** "
                    + (", ".join(f"`{n.rel}:{n.line_start}`" for n in nodes if n.rel) or "screen mapping only")),
            Section("Called from", "\n".join(f"- `{c}`" for c in callers) or "_no resolved callers_"),
            Section("Screens", "\n".join(f"- [{s}]({make_page_id('screen', s)})" for s in sorted(screen_refs[name]))
                    or "_none_"),
            _footer(ctx),
        ]
        links = [Link(make_page_id("screen", s), "see_also") for s in screen_refs[name]]
        yield Page(make_page_id("interface", name), name,
                   facts={"kinds": kinds, "callers": callers, "screens": sorted(screen_refs[name])},
                   sections=sections, links=links,
                   citations=[c for n in nodes for c in snap.cite(n)])
