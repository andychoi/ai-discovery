"""Structural code pages: overview, module, file, symbol (roadmap item M3.2).

Pure functions of a scan snapshot — no LLM, no timestamps — so the same scan
always renders the same pages. Every citation is checked against the file on
disk (it must exist and contain the cited lines); unverifiable spans are left
out rather than cited wrong.

Size limits (``BuildContext.extra``): ``max_file_pages`` (default 500; above it
only the files with the most incoming calls get pages) and ``max_hub_symbols``
(default 25 most-called symbols).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from ..model import Citation, Link, Page, Section, make_page_id
from ..registry import BuildContext, page_builder

VERSION = "1"
FOOTER = "Derived from structure (parsed code and call graph) — no LLM was used for this page."
DEFAULT_MAX_FILE_PAGES = 500
DEFAULT_MAX_HUBS = 25
ENTRY_NODE_TYPES = ("endpoint", "batch_job")


@dataclass(frozen=True)
class Node:
    id: int
    rel: Optional[str]          # repo-relative path ('/' separators) or None if outside the repo
    language: str
    node_type: str
    name: str
    qualified_name: str
    line_start: int
    line_end: int
    domain: Optional[str]


class Snapshot:
    """The rows code builders need, loaded once per build (cached on the context)."""

    def __init__(self, ctx: BuildContext):
        conn, sid = ctx.conn, ctx.scan_id
        repo = ctx.repo.resolve()
        self.repo = repo
        self.nodes: list[Node] = [
            Node(r[0], _rel(r[1], repo), r[2] or "unknown", r[3], r[4], r[5], int(r[6] or 1),
                 int(r[7] or r[6] or 1), r[8])
            for r in conn.execute(
                "SELECT id, file_path, language, node_type, name, qualified_name, line_start, line_end, domain "
                "FROM code_nodes WHERE scan_id = ? ORDER BY id", (sid,))
        ]
        self.by_id = {n.id: n for n in self.nodes}
        self.edges = conn.execute(
            "SELECT caller_id, callee_id, resolved_by FROM call_edges WHERE scan_id = ?", (sid,)).fetchall()
        self.in_degree: Counter[int] = Counter(e[1] for e in self.edges if e[1] is not None)
        self.callees: dict[int, set[int]] = defaultdict(set)
        self.callers: dict[int, set[int]] = defaultdict(set)
        for caller, callee, _ in self.edges:
            if callee is not None:
                self.callees[caller].add(callee)
                self.callers[callee].add(caller)
        self.domains = conn.execute(
            "SELECT name, node_count, entry_points FROM domains WHERE scan_id = ? ORDER BY name", (sid,)).fetchall()
        self.screen_count = conn.execute(
            "SELECT COUNT(*) FROM screen_specs WHERE scan_id = ?", (sid,)).fetchone()[0]
        self._line_counts: dict[str, Optional[int]] = {}
        # symbol page ids: qualified names that collide get "@path:line" keys
        groups: dict[str, list[Node]] = defaultdict(list)
        for n in self.nodes:
            if n.rel is not None and n.node_type not in ("external_system",):
                groups[n.qualified_name].append(n)
        self.symbol_key: dict[int, str] = {}
        for qn, members in groups.items():
            for n in members:
                self.symbol_key[n.id] = qn if len(members) == 1 else f"{qn}@{n.rel}:{n.line_start}"

    @classmethod
    def of(cls, ctx: BuildContext) -> "Snapshot":
        snap = ctx.extra.get("_code_snapshot")
        if snap is None:
            snap = cls(ctx)
            ctx.extra["_code_snapshot"] = snap
        return snap

    def line_count(self, rel: str) -> Optional[int]:
        if rel not in self._line_counts:
            try:
                with open(self.repo / rel, "rb") as fh:
                    self._line_counts[rel] = sum(1 for _ in fh) or 1
            except OSError:
                self._line_counts[rel] = None
        return self._line_counts[rel]

    def cite(self, n: Node) -> list[Citation]:
        """A citation for *n* only if the span exists in the file on disk."""
        if n.rel is None:
            return []
        total = self.line_count(n.rel)
        if total is None or n.line_start < 1 or n.line_end < n.line_start or n.line_end > total:
            return []
        return [Citation(n.rel, n.line_start, n.line_end)]

    def files(self) -> dict[str, list[Node]]:
        out: dict[str, list[Node]] = defaultdict(list)
        for n in self.nodes:
            if n.rel is not None and n.node_type != "external_system":
                out[n.rel].append(n)
        return out

    def entry_points(self) -> list[Node]:
        named: set[str] = set()
        for _, _, raw in self.domains:
            try:
                named.update(json.loads(raw or "[]"))
            except ValueError:
                pass
        picks = [n for n in self.nodes if n.rel is not None and
                 (n.node_type in ENTRY_NODE_TYPES or n.qualified_name in named
                  or (n.name == "main" and n.node_type in ("method", "function")))]
        return sorted(picks, key=lambda n: (n.rel or "", n.line_start, n.qualified_name))

    def hubs(self, limit: int) -> list[Node]:
        ranked = sorted((n for n in self.nodes if n.id in self.symbol_key and self.in_degree[n.id] > 0),
                        key=lambda n: (-self.in_degree[n.id], n.qualified_name))
        return ranked[:limit]


def _rel(path: str, repo: Path) -> Optional[str]:
    p = Path(path)
    try:
        rel = p.resolve().relative_to(repo) if p.is_absolute() else PurePosixPath(path)
    except ValueError:
        return None
    return PurePosixPath(*Path(rel).parts).as_posix()


def _module_of(rel: str) -> str:
    parent = PurePosixPath(rel).parent.as_posix()
    return "." if parent in ("", ".") else parent


def _md_table(header: list[str], rows: Iterable[list[str]]) -> str:
    rows = list(rows)
    if not rows:
        return "_none_"
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(lines)


MAX_OVERVIEW_DOMAINS = 30


def _domains_table(domains) -> str:
    ranked = sorted(domains, key=lambda d: (-(d[1] or 0), d[0]))
    table = _md_table(["Domain", "Symbols"], ([d[0], d[1] or 0] for d in ranked[:MAX_OVERVIEW_DOMAINS]))
    extra = len(ranked) - MAX_OVERVIEW_DOMAINS
    return table + (f"\n\n…and {extra} more (largest shown)." if extra > 0 else "")


def _sym_link_text(snap: Snapshot, n: Node) -> str:
    return f"[`{n.qualified_name}`]({make_page_id('symbol', snap.symbol_key[n.id])})"


def _footer(ctx: BuildContext) -> Section:
    commit = f" Scan of commit `{ctx.commit[:12]}`." if ctx.commit else ""
    return Section("Provenance", FOOTER + commit)


# ── builders ──────────────────────────────────────────────────────────────

@page_builder("code.overview", emits={"overview"},
              inputs=("code_nodes", "call_edges", "domains", "screen_specs"), version=VERSION)
def build_overview(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    files = snap.files()
    langs = Counter()
    for rel, members in files.items():
        langs[members[0].language] += 1
    resolved = sum(1 for e in snap.edges if e[1] is not None)
    externals = sorted({n.name for n in snap.nodes if n.node_type == "external_system"})
    entries = snap.entry_points()
    top_modules = sorted({_module_of(r).split("/")[0] for r in files})

    facts = {
        "languages": dict(sorted(langs.items())), "files": len(files), "symbols": len(snap.nodes),
        "edges": len(snap.edges), "resolved_edges": resolved, "domains": [d[0] for d in snap.domains],
        "screens": snap.screen_count, "entry_points": len(entries), "external_systems": externals,
    }
    sections = [
        Section("Summary", "\n".join([
            f"- **Files:** {len(files)} ({', '.join(f'{k} {v}' for k, v in sorted(langs.items())) or 'none'})",
            f"- **Symbols:** {len(snap.nodes)}",
            f"- **Calls:** {len(snap.edges)} ({resolved} resolved to a definition, "
            f"{len(snap.edges) - resolved} unresolved)",
            f"- **Screens:** {snap.screen_count}",
        ])),
        Section("Domains", _domains_table(snap.domains)),
        Section("Entry points", _md_table(
            ["Entry point", "Kind", "Where"],
            ([_sym_link_text(snap, n), n.node_type, f"`{n.rel}:{n.line_start}`"] for n in entries[:50]))
            + (f"\n\n…and {len(entries) - 50} more." if len(entries) > 50 else "")),
        Section("External systems", "\n".join(f"- {e}" for e in externals) or "_none detected_"),
        Section("Modules", "\n".join(f"- [{m}]({make_page_id('module', m)})" for m in top_modules) or "_none_"),
        _footer(ctx),
    ]
    links = [Link(make_page_id("module", m), "child") for m in top_modules]
    links += [Link(make_page_id("symbol", snap.symbol_key[n.id]), "link") for n in entries]
    yield Page(make_page_id("overview", "system"), "System overview", facts=facts,
               sections=sections, links=links)


@page_builder("code.modules", emits={"module"}, inputs=("code_nodes", "call_edges"), version=VERSION)
def build_modules(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    files = snap.files()
    by_module: dict[str, list[str]] = defaultdict(list)
    for rel in files:
        by_module[_module_of(rel)].append(rel)
    # include ancestor directories so the tree is navigable
    all_modules = set(by_module)
    for m in list(all_modules):
        parts = PurePosixPath(m).parts
        for i in range(1, len(parts)):
            all_modules.add(PurePosixPath(*parts[:i]).as_posix())
    file_pages = set(_file_page_set(ctx, snap))

    for m in sorted(all_modules):
        children = sorted(c for c in all_modules if c != m and _module_of(c) == m)
        own = sorted(by_module.get(m, []))
        symbols = [n for rel in own for n in files[rel]]
        langs = sorted({n.language for n in symbols})
        sections = [
            Section("Contents", "\n".join([
                f"- **Files:** {len(own)}", f"- **Symbols:** {len(symbols)}",
                f"- **Languages:** {', '.join(langs) or '—'}"])),
        ]
        if children:
            sections.append(Section("Sub-modules", "\n".join(f"- [{c}]({make_page_id('module', c)})" for c in children)))
        if own:
            sections.append(Section("Files", _md_table(["File", "Symbols"], (
                [f"[{r}]({make_page_id('file', r)})" if r in file_pages else r, len(files[r])] for r in own))))
        sections.append(_footer(ctx))
        links = [Link(make_page_id("module", c), "child") for c in children]
        links += [Link(make_page_id("file", r), "child") for r in own if r in file_pages]
        parent_mod = _module_of(m)
        if m != "." and parent_mod in all_modules:
            links.append(Link(make_page_id("module", parent_mod), "parent"))
        yield Page(make_page_id("module", m), m if m != "." else "(repository root)",
                   facts={"files": len(own), "symbols": len(symbols), "languages": langs},
                   sections=sections, links=links)


def _file_page_set(ctx: BuildContext, snap: Snapshot) -> list[str]:
    files = snap.files()
    cap = int(ctx.extra.get("max_file_pages", DEFAULT_MAX_FILE_PAGES))
    if len(files) <= cap:
        return sorted(files)
    score = {rel: sum(snap.in_degree[n.id] for n in members) for rel, members in files.items()}
    return sorted(sorted(files, key=lambda r: (-score[r], r))[:cap])


def _symbol_page_ids(ctx: BuildContext, snap: Snapshot) -> set[int]:
    limit = int(ctx.extra.get("max_hub_symbols", DEFAULT_MAX_HUBS))
    return {n.id for n in snap.entry_points()} | {n.id for n in snap.hubs(limit)}


@page_builder("code.files", emits={"file"}, inputs=("code_nodes", "call_edges"), version=VERSION)
def build_files(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    files = snap.files()
    symbol_pages = _symbol_page_ids(ctx, snap)
    for rel in _file_page_set(ctx, snap):
        members = sorted(files[rel], key=lambda n: (n.line_start, n.qualified_name))
        citations = [c for n in members for c in snap.cite(n)]
        rows = []
        for n in members:
            label = _sym_link_text(snap, n) if n.id in symbol_pages else f"`{n.qualified_name}`"
            rows.append([label, n.node_type, n.line_start, snap.in_degree[n.id], len(snap.callees[n.id])])
        incoming_files = sorted({snap.by_id[c].rel for n in members for c in snap.callers[n.id]
                                 if snap.by_id[c].rel and snap.by_id[c].rel != rel})
        sections = [
            Section("Symbols", _md_table(["Symbol", "Kind", "Line", "Called by", "Calls"], rows)),
            Section("Used by files", "\n".join(f"- `{f}`" for f in incoming_files) or "_no other file calls into this one_"),
            _footer(ctx),
        ]
        links = [Link(make_page_id("module", _module_of(rel)), "belongs_to")]
        links += [Link(make_page_id("symbol", snap.symbol_key[n.id]), "child") for n in members if n.id in symbol_pages]
        yield Page(make_page_id("file", rel), rel,
                   facts={"language": members[0].language, "symbols": len(members),
                          "incoming_calls": sum(snap.in_degree[n.id] for n in members)},
                   sections=sections, links=links, citations=citations)


@page_builder("code.symbols", emits={"symbol"}, inputs=("code_nodes", "call_edges", "domains"), version=VERSION)
def build_symbols(ctx: BuildContext):
    snap = Snapshot.of(ctx)
    picked = _symbol_page_ids(ctx, snap)
    entry_ids = {n.id for n in snap.entry_points()}
    file_pages = set(_file_page_set(ctx, snap))
    for nid in sorted(picked, key=lambda i: snap.symbol_key[i]):
        n = snap.by_id[nid]

        def ref(o: Node) -> str:
            return _sym_link_text(snap, o) if o.id in picked else f"`{o.qualified_name}`"

        callers = sorted((snap.by_id[c] for c in snap.callers[nid]), key=lambda o: o.qualified_name)
        callees = sorted((snap.by_id[c] for c in snap.callees[nid]), key=lambda o: o.qualified_name)
        role = "entry point" if nid in entry_ids else "hub"
        sections = [
            Section("Definition", "\n".join([
                f"- **Kind:** {n.node_type} ({n.language})",
                f"- **Defined in:** `{n.rel}:{n.line_start}-{n.line_end}`",
                f"- **Domain:** {n.domain or '—'}",
                f"- **Why it has a page:** {role} (called by {snap.in_degree[nid]})"])),
            Section("Called by", "\n".join(f"- {ref(o)}" for o in callers[:100]) or "_no resolved callers_"),
            Section("Calls", "\n".join(f"- {ref(o)}" for o in callees[:100]) or "_no resolved calls_"),
            _footer(ctx),
        ]
        links = [Link(make_page_id("file", n.rel), "belongs_to")] if n.rel in file_pages else []
        links += [Link(make_page_id("symbol", snap.symbol_key[o.id]), "calls") for o in callees if o.id in picked]
        links += [Link(make_page_id("symbol", snap.symbol_key[o.id]), "called_by") for o in callers if o.id in picked]
        yield Page(make_page_id("symbol", snap.symbol_key[nid]), n.qualified_name,
                   facts={"kind": n.node_type, "language": n.language, "domain": n.domain,
                          "called_by": snap.in_degree[nid], "calls": len(snap.callees[nid]), "role": role},
                   sections=sections, links=links, citations=snap.cite(n))
