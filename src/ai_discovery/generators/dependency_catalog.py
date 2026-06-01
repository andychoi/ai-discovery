"""Dependency & interface catalog (addresses gap-assessment #7).

The original gap assessment flagged that the output had "no dedicated integration
inventory, dependency matrix, or interface catalog" — interdependencies were
never surfaced as a primary artifact. This renders one markdown doc per scan that
inventories what the session's structural extractors now capture:

  - External systems (code- and infra-declared) grouped by kind, with provenance.
  - Entity foreign-key relationships (the FK graph).
  - The inbound API surface (endpoint count by source: AST / OpenAPI / contracts).

It reads only persisted facts (no LLM), so it's deterministic and trustworthy.
"""

from __future__ import annotations

import json

from ..db import get_conn, get_relationships

_KIND_LABEL = {
    "http_api": "HTTP APIs", "database": "Databases", "cache": "Caches",
    "queue": "Queues / Messaging", "search": "Search", "storage": "Object Storage",
    "payment": "Payment", "mail": "Mail", "identity": "Identity", "cloud": "Cloud",
}


def render_dependency_catalog(db_path, scan_id: int, project_slug: str) -> str:
    """Return the catalog markdown, or "" when there's nothing to inventory."""
    conn = get_conn(db_path)
    try:
        ext_rows = conn.execute(
            "SELECT name, framework_hints FROM code_nodes "
            "WHERE scan_id = ? AND node_type = 'external_system'",
            (scan_id,),
        ).fetchall()
        endpoint_rows = conn.execute(
            "SELECT framework_hints FROM code_nodes "
            "WHERE scan_id = ? AND node_type = 'endpoint'",
            (scan_id,),
        ).fetchall()
    finally:
        conn.close()

    relationships = get_relationships(db_path, scan_id)

    # External systems grouped by kind.
    by_kind: dict[str, list[dict]] = {}
    for row in ext_rows:
        hints = json.loads(row["framework_hints"] or "{}")
        by_kind.setdefault(hints.get("kind", "other"), []).append({
            "name": row["name"], "source": hints.get("source", "code"),
        })

    # Endpoint surface by source.
    ep_by_source: dict[str, int] = {}
    for row in endpoint_rows:
        hints = json.loads(row["framework_hints"] or "{}")
        ep_by_source[hints.get("source", "ast")] = ep_by_source.get(hints.get("source", "ast"), 0) + 1

    if not by_kind and not relationships and not ep_by_source:
        return ""

    lines = [
        f"# Dependencies & Interfaces — {project_slug}",
        "",
        "> Deterministic inventory of external systems, entity relationships, and "
        "the API surface, built from extracted facts (no LLM).",
        "",
        "## Summary",
        "",
        f"- External systems: **{sum(len(v) for v in by_kind.values())}** "
        f"across {len(by_kind)} kinds",
        f"- Entity relationships (FK): **{len(relationships)}**",
        f"- Inbound endpoints: **{sum(ep_by_source.values())}** "
        f"({', '.join(f'{n} {s}' for s, n in sorted(ep_by_source.items()))})"
        if ep_by_source else "- Inbound endpoints: **0**",
        "",
    ]

    if by_kind:
        lines += ["## External Systems", ""]
        for kind in sorted(by_kind):
            label = _KIND_LABEL.get(kind, kind.title())
            lines.append(f"### {label}")
            lines.append("")
            for sysd in sorted(by_kind[kind], key=lambda d: d["name"]):
                lines.append(f"- **{sysd['name']}** _(declared in {sysd['source']})_")
            lines.append("")

    if relationships:
        lines += ["## Entity Relationships", "",
                  "| From | → | To | Cardinality | Source |",
                  "|---|---|---|---|---|"]
        for r in relationships:
            flag = " ⚠ inferred" if r.inferred else ""
            lines.append(
                f"| `{r.from_entity}` | {r.from_field or ''} | `{r.to_entity}` "
                f"| {r.cardinality or ''} | {r.source}{flag} |"
            )
        lines.append("")

    return "\n".join(lines)


def render_integration_map(integration_edges: list, source_repos: list[str]) -> str:
    """Render cross-repo provider→consumer integrations (the correlator output)
    as a human-readable map: a dependency matrix plus per-provider detail.

    `integration_edges` are IntegrationEdge objects (from integration_correlator).
    Returns "" when there are no cross-repo integrations.
    """
    if not integration_edges:
        return ""

    # Consumer→{providers} for a compact dependency overview.
    deps: dict[str, set[str]] = {}
    for e in integration_edges:
        deps.setdefault(e.consumer_repo, set()).add(e.provider_repo)

    lines = [
        "# Cross-Repo Integration Map",
        "",
        f"> Provider→consumer integrations correlated across {len(source_repos)} repos "
        "by matching outbound HTTP calls to inbound endpoints (method + normalized path).",
        "",
        "## Dependency Overview",
        "",
        "| Consumer | Depends on (providers) |",
        "|---|---|",
    ]
    for consumer in sorted(deps):
        lines.append(f"| `{consumer}` | {', '.join(f'`{p}`' for p in sorted(deps[consumer]))} |")
    lines += ["", "## Integration Edges", "",
              "| Provider | Endpoint | Consumer | Caller |", "|---|---|---|---|"]
    for e in sorted(integration_edges, key=lambda x: (x.provider_repo, x.path, x.consumer_repo)):
        lines.append(
            f"| `{e.provider_repo}` | `{e.method} {e.path}` | `{e.consumer_repo}` "
            f"| {e.consumer_caller or ''} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_dependency_catalog(db_path, scan_id: int, project_slug: str, docs_dir):
    """Write the catalog under `INTERFACES/`. Returns the path, or None if empty."""
    from pathlib import Path

    md = render_dependency_catalog(db_path, scan_id, project_slug)
    if not md:
        return None
    out_dir = Path(docs_dir) / "INTERFACES"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{project_slug}-dependencies.md"
    front = (
        f"---\ndoc_id: {project_slug}-dependencies\n"
        "title: \"Dependencies & Interfaces\"\n"
        "tags: [discovery-scan, dependencies, interfaces]\n"
        "source: discovery_scan\n---\n\n"
    )
    path.write_text(front + md, encoding="utf-8")
    return path
