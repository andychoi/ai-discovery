"""Discovery CLI — Typer entry point for brownfield codebase analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

from ai_discovery.config import DiscoveryConfig

# Load .env from project root (AWS creds, tokens, etc.)
load_dotenv()

console = Console()
app = typer.Typer(name="discover", help="Brownfield codebase discovery & doc generation.")

_VALID_INGEST_TARGETS = {"dochub", "gitea"}
_VALID_PUSH_MODES = {"api", "gitea"}  # ingest-docs --push (ingest/runner.py)


def _load_config(config: Optional[Path]) -> "DiscoveryConfig":
    """Load layered config (M1.3); report invalid config cleanly (exit 2)."""
    from ai_discovery.config.loader import ConfigError

    try:
        return DiscoveryConfig.load(str(config) if config else None)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from None


def _layout(output: Path, slug: str, docs_root: Path = Path("./data")):
    """Read-side project layout for the current CLI (M1.2: one resolver,
    `core.paths.ProjectLayout`; falls back to pre-Track-3 `<output>/<slug>/`)."""
    from ai_discovery.core.paths import ProjectLayout

    return ProjectLayout.legacy(output, slug, docs_root, for_read=True)


@app.command()
def chat(
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Target project slug."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o", help="Output directory (same as scan)."
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider (bedrock|ollama)."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to YAML config file."),
    top_k: int = typer.Option(5, "--top-k", help="Number of chunks to retrieve per source."),
    no_index: bool = typer.Option(False, "--no-index", help="Skip re-indexing generated docs."),
    tier2: bool = typer.Option(False, "--tier2", help="Use tier2 model instead of default tier1."),
) -> None:
    """Chat with the discovery RAG database (code chunks + generated docs)."""
    from ai_discovery.ai.llm_client import LLMClient
    from ai_discovery.rag.chat import run_repl

    cfg = _load_config(config)
    if provider:
        cfg.provider = provider

    # Docs live under the scan's --docs-root (default ./data), not under the
    # intermediate dir — chat previously indexed a docs folder nothing writes.
    layout = _layout(output, project_slug)
    db_path = layout.db_path
    docs_dir = layout.docs_dir

    if not db_path.exists():
        console.print(f"[red]No discovery DB found:[/] {db_path}")
        console.print("  Run [bold]discover scan[/] first.")
        raise typer.Exit(code=1)

    llm_client = LLMClient(cfg)
    run_repl(db_path, docs_dir, llm_client, top_k=top_k, embed_docs_first=not no_index,
             llm_tier="tier2" if tier2 else "tier1")


@app.command()
def ingest(
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Target project slug."),
    target: str = typer.Option("dochub", "--target", help="Ingest target: dochub | gitea."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o",
        help="Intermediate output dir (same as scan). Used to locate the DB.",
    ),
    docs_root: Path = typer.Option(
        Path("./data"), "--docs-root",
        help="Root of generated docs tree ({docs_root}/{project_slug}/{PREFIX}/).",
    ),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="DocHub API base URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", envvar="DISCOVERY_API_KEY", help="Shared API key for DocHub ingest."),
    api_token: Optional[str] = typer.Option(None, "--api-token", envvar="DISCOVERY_API_TOKEN", help="Bearer JWT token for DocHub API (legacy)."),
    gitea_url: Optional[str] = typer.Option(None, "--gitea-url", help="Gitea base URL."),
    gitea_token: Optional[str] = typer.Option(None, "--gitea-token", envvar="DISCOVERY_GITEA_TOKEN", help="Gitea API token."),
    only_failed: bool = typer.Option(False, "--only-failed", help="Retry only docs with push_status failed/local."),
) -> None:
    """Batch-ingest already-generated docs from the offline tree to DocHub or Gitea."""
    from ai_discovery.db import get_conn
    from ai_discovery.generators.doc_generator import _doc_type_prefix
    from ai_discovery.generators.push import push_docs

    if target not in _VALID_INGEST_TARGETS:
        console.print(f"[red]Invalid --target '{target}'. Must be: dochub | gitea[/]")
        raise typer.Exit(code=1)

    layout = _layout(output, project_slug, docs_root)
    db_path = layout.db_path
    docs_dir = layout.docs_dir

    if not db_path.exists():
        console.print(f"[red]No discovery DB found:[/] {db_path}")
        console.print("  Run [bold]discover scan[/] first.")
        raise typer.Exit(code=1)

    # Load latest scan's generated docs from DB
    conn = get_conn(db_path)
    try:
        query_sql = (
            "SELECT d.doc_id, d.doc_type, d.domain, d.confidence, d.push_status "
            "FROM generated_docs d "
            "JOIN scan_runs r ON r.id = d.scan_id "
            "WHERE r.id = (SELECT MAX(id) FROM scan_runs WHERE project_slug = ?)"
        )
        if only_failed:
            query_sql += " AND d.push_status IN ('failed', 'local')"
        rows = conn.execute(query_sql, (project_slug,)).fetchall()
    finally:
        conn.close()

    if not rows:
        msg = "No docs to retry." if only_failed else "No generated docs found in DB. Run scan first."
        console.print(f"[yellow]{msg}[/]")
        raise typer.Exit(code=0 if only_failed else 1)

    written_docs: list[dict] = []
    missing: list[str] = []
    for row in rows:
        file_path = docs_dir / _doc_type_prefix(row["doc_type"]) / f"{row['doc_id']}.md"
        if not file_path.exists():
            missing.append(str(file_path))
            continue
        written_docs.append({
            "doc_id": row["doc_id"],
            "doc_type": row["doc_type"],
            "domain": row["domain"],
            "file_path": str(file_path),
            "confidence": row["confidence"] or 0.0,
        })

    if missing:
        console.print(f"[yellow]Warning: {len(missing)} doc file(s) not found on disk (skipped)[/]")
    if not written_docs:
        console.print("[red]No doc files found to ingest.[/]")
        raise typer.Exit(code=1)

    push_mode = "api" if target == "dochub" else "gitea"
    console.print(f"[bold green]discover ingest[/] project={project_slug}  target={target}  docs={len(written_docs)}")

    with console.status(f"[bold cyan]Ingesting docs ({target})..."):
        results = push_docs(
            written_docs, push_mode, project_slug,
            api_url=api_url, api_key=api_key, api_token=api_token,
            gitea_url=gitea_url, gitea_token=gitea_token,
            db_path=db_path,
        )

    ok = sum(1 for r in results if r["status"] == "pushed")
    failed = [r for r in results if r["status"] == "failed"]
    console.print(f"  [green]{ok} ingested[/]", end="")
    if failed:
        console.print(f", [red]{len(failed)} failed[/]")
        for r in failed:
            console.print(f"    [red]✗[/] {r['doc_id']}: {r['error']}")
        raise typer.Exit(code=1)
    else:
        console.print()


@app.command()
def query(
    sql: str = typer.Argument(..., help="SQL query to run against the discovery DB."),
    db: Path = typer.Option(
        Path("./data/discovery-output"), "--db", help="Path to the scan's SQLite DB (the discovery-*.db file under the scan output dir)."
    ),
) -> None:
    """Run an ad-hoc SQL query against the discovery database."""
    import sqlite3

    if not db.exists():
        console.print(f"[red]Database not found:[/] {db}")
        raise typer.Exit(code=1)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        if rows:
            cols = rows[0].keys()
            console.print("  ".join(cols))
            console.print("─" * 60)
            for row in rows:
                console.print("  ".join(str(row[c]) for c in cols))
        else:
            console.print("[dim]No results.[/]")
    except sqlite3.Error as exc:
        console.print(f"[red]SQL error:[/] {exc}")
        raise typer.Exit(code=1)
    finally:
        conn.close()


@app.command()
def impact(
    entity: str = typer.Argument(..., help="Entity name or entity_id to investigate."),
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Project slug whose artifacts to read."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o",
        help="Output dir (must match the scan's --output).",
    ),
    output_file: Optional[Path] = typer.Option(
        None, "--output-file", "-f",
        help="Write the impact report to this file. If omitted, prints to stdout.",
    ),
) -> None:
    """Show every code path and cross-entity interaction that touches an entity.

    Reads the canonical backbone artifacts written by `discover scan`:
    entity_state_machines.json, cross_entity_transitions.json, and
    entity_conditions.json. Failing to find one of these is almost always
    a sign the scan didn't reach Phase 3 — re-run with --resume.
    """
    from ai_discovery.graph.fsm_export import (
        load_cross_entity_links_json,
        load_entity_conditions_json,
        load_entity_state_machines_json,
    )
    from ai_discovery.graph.impact import EntityNotFound, query_entity_impact

    layout = _layout(output, project_slug)
    fsm_path = layout.artifact("entity_state_machines.json")
    if not fsm_path.exists():
        console.print(f"[red]No FSM artifact at {fsm_path}. Run `discover scan` first.[/]")
        raise typer.Exit(1)

    fsms = load_entity_state_machines_json(fsm_path)
    cross_path = layout.artifact("cross_entity_transitions.json")
    cross_links = load_cross_entity_links_json(cross_path) if cross_path.exists() else []
    cond_path = layout.artifact("entity_conditions.json")
    conditions = load_entity_conditions_json(cond_path) if cond_path.exists() else []

    try:
        report = query_entity_impact(entity, fsms, cross_links, conditions)
    except EntityNotFound as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(report)
        console.print(f"[green]Wrote impact report → {output_file}[/]")
    else:
        # Plain stdout so the caller can pipe to `less`, `glow`, a file, etc.
        print(report)


@app.command()
def federate(
    artifact_dirs: list[Path] = typer.Argument(
        ..., help="Per-repo artifact directories produced by `discover scan`.",
    ),
    output: Path = typer.Option(
        ..., "--output", "-o",
        help="Output directory for federated artifacts.",
    ),
    slugs: Optional[str] = typer.Option(
        None, "--slugs", "-s",
        help="Comma-separated repo slugs, one per artifact dir. "
             "Defaults to each dir's basename.",
    ),
    jaccard: float = typer.Option(
        0.7, "--jaccard",
        help="Field-Jaccard threshold for cross-repo entity merge (0-1).",
    ),
) -> None:
    """Merge per-repo backbone artifacts into a federated view.

    Scans already written their own entity_state_machines.json etc. This
    command reads those, merges FSMs whose normalized names and field sets
    match across repos, and writes a federated artifact set that the
    existing `discover impact` command can consume directly.

    Example:
      discover scan billing-svc/ -p billing -o ./out
      discover scan fulfillment-svc/ -p fulfillment -o ./out
      discover federate ./out/billing ./out/fulfillment -o ./out/federated
    """
    from ai_discovery.graph.federation import federate_workspace, write_federation

    slug_list: Optional[list[str]] = None
    if slugs:
        slug_list = [s.strip() for s in slugs.split(",") if s.strip()]
        if len(slug_list) != len(artifact_dirs):
            console.print(
                f"[red]--slugs must provide exactly one slug per artifact dir "
                f"({len(artifact_dirs)} dirs given).[/]"
            )
            raise typer.Exit(1)

    for d in artifact_dirs:
        if not d.is_dir():
            console.print(f"[red]Not a directory: {d}[/]")
            raise typer.Exit(1)

    try:
        federation = federate_workspace(
            artifact_dirs, repo_slugs=slug_list, jaccard_threshold=jaccard,
        )
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None

    paths = write_federation(federation, output)
    merged_count = sum(
        1 for f in federation["fsms"]
        if len(f.metadata.get("source_repos", {})) > 1
    )
    console.print(
        f"[green]Federated[/] {len(artifact_dirs)} repos → "
        f"[green]{len(federation['fsms'])}[/] entities "
        f"([yellow]{merged_count}[/] merged across repos), "
        f"[green]{len(federation['cross_links'])}[/] links, "
        f"[green]{len(federation['conditions'])}[/] conditions, "
        f"[green]{len(federation.get('integration_edges', []))}[/] integration edges"
    )
    for name, path in paths.items():
        console.print(f"  {name}: {path}")


@app.command()
def view(
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Project slug whose scan output to serve."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o",
        help="Output dir (must match the scan's --output).",
    ),
    docs_root: Path = typer.Option(
        Path("./data"), "--docs-root",
        help="Root containing rendered markdown at {docs_root}/{slug}/<BUCKET>/*.md.",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (default: localhost-only)."),
    port: int = typer.Option(8765, "--port", help="Bind port."),
    no_open: bool = typer.Option(False, "--no-open", help="Don't auto-open a browser window."),
) -> None:
    """Serve a local HTML quality dashboard for a completed `discover scan`.

    Renders markdown, mermaid, BPMN, and DMN diagrams client-side via CDN
    libraries — requires internet on first load. Server is read-only.

    Examples:

        discover view -p todoapp
        discover view -p todoapp --port 9000 --no-open
    """
    import webbrowser

    from ai_discovery.viewer.server import ViewerContext, make_server

    slug_output = _layout(output, project_slug, docs_root).state_dir.resolve()
    if not slug_output.is_dir():
        console.print(
            f"[red]No scan output at {slug_output}.[/] Run `discover scan` first, "
            f"or check --output/--project-slug."
        )
        raise typer.Exit(code=1)

    ctx = ViewerContext(slug=project_slug, output_dir=output, docs_root=docs_root)
    server = make_server(ctx, host=host, port=port)
    url = f"http://{host}:{port}/"
    console.print(f"[bold green]discover view[/] serving {project_slug} at [cyan]{url}[/]")
    console.print(f"  output_dir={slug_output}")
    if ctx.slug_docs_root.is_dir():
        console.print(f"  docs_root={ctx.slug_docs_root}")
    else:
        console.print(f"  docs_root=[yellow]{ctx.slug_docs_root}[/] [dim](missing — markdown viewer disabled)[/]")
    console.print("  [dim]Ctrl+C to stop[/]")

    if not no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down…[/]")
    finally:
        server.server_close()


@app.command("test-llm")
def test_llm(
    provider: Optional[str] = typer.Option(None, "--provider", help="Override LLM provider (bedrock|ollama|mlx-gemma|mlx-qwen)."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to YAML config file."),
    tier: str = typer.Option("tier1", "--tier", help="Which generation tier to probe (tier1|tier2|tier3; tier3 follows quality)."),
    skip_gen: bool = typer.Option(False, "--skip-gen", help="Skip text-generation probe."),
    skip_embedding: bool = typer.Option(False, "--skip-embedding", help="Skip embedding probe."),
) -> None:
    """Probe the configured LLM and embedding endpoints.

    Sends one tiny request to the generation tier and one to the embedding
    endpoint, reports which succeed. Use before `discover scan` to catch
    connectivity, credential, or model-ID problems in seconds instead of
    after minutes of pipeline work.
    """
    import time

    from ai_discovery.ai.llm_client import LLMClient

    cfg = _load_config(config)
    if provider:
        cfg.provider = provider

    console.print(f"[bold green]discover test-llm[/] provider={cfg.provider}")
    if cfg.provider != "bedrock":
        base_url, _ = cfg.get_endpoint()
        console.print(f"  endpoint={base_url}")
    else:
        console.print(f"  region={cfg.bedrock.region}")

    client = LLMClient(cfg)
    failures = 0

    if not skip_gen:
        from ai_discovery.llm import role_for_tier
        model = cfg.get_model(role_for_tier(tier))
        console.print(f"\n[bold]Generation[/] tier={tier} model={model}")
        t0 = time.monotonic()
        try:
            resp = client.invoke(tier, "Reply with the single word: ok", max_tokens=8)
            dt = time.monotonic() - t0
            console.print(
                f"  [green]✓[/] {dt:.2f}s  tokens_in={resp.tokens_in} "
                f"tokens_out={resp.tokens_out}  reply={resp.text.strip()!r}"
            )
        except Exception as exc:
            dt = time.monotonic() - t0
            console.print(f"  [red]✗[/] {dt:.2f}s  {exc}")
            failures += 1

    if not skip_embedding:
        emb_provider = cfg.rag.embedding_provider
        emb_model = (
            cfg.rag.bedrock_model if emb_provider == "bedrock"
            else client._embedding_model_for(cfg.provider)
        )
        console.print(f"\n[bold]Embedding[/] provider={emb_provider} model={emb_model}")
        t0 = time.monotonic()
        try:
            vec = client.get_embedding("connection test")
            dt = time.monotonic() - t0
            console.print(f"  [green]✓[/] {dt:.2f}s  dim={len(vec)}")
        except Exception as exc:
            dt = time.monotonic() - t0
            console.print(f"  [red]✗[/] {dt:.2f}s  {exc}")
            failures += 1

    if failures:
        console.print(f"\n[red]{failures} probe(s) failed.[/]")
        raise typer.Exit(code=1)
    console.print("\n[green]All probes passed.[/]")


@app.command("ingest-docs")
def ingest_docs(
    docs_dir: Path = typer.Argument(..., help="Path to directory containing markdown files to ingest."),
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Target project slug."),
    push_mode: str = typer.Option("api", "--push", help="Push mode: api | gitea (default: api)."),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="DocHub API base URL."),
    api_key: Optional[str] = typer.Option(None, "--api-key", envvar="DISCOVERY_API_KEY", help="Shared API key for DocHub ingest."),
    api_token: Optional[str] = typer.Option(None, "--api-token", envvar="DISCOVERY_API_TOKEN", help="Bearer JWT token for DocHub API (legacy)."),
    gitea_url: Optional[str] = typer.Option(None, "--gitea-url", help="Gitea base URL."),
    gitea_token: Optional[str] = typer.Option(None, "--gitea-token", envvar="DISCOVERY_GITEA_TOKEN", help="Gitea API token."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Classify and preview without pushing."),
    batch_size: int = typer.Option(20, "--batch-size", help="Documents per batch request."),
    default_type: str = typer.Option("as-is", "--default-type", help="Fallback doc type when classifier is unsure (any SDLC type)."),
    no_domain_dirs: bool = typer.Option(False, "--no-domain-dirs", help="Don't infer domain tags from directory names."),
    detect_duplicates: bool = typer.Option(True, "--detect-duplicates/--no-detect-duplicates", help="Enable/disable TF-IDF duplicate detection."),
    similarity_threshold: float = typer.Option(0.85, "--similarity-threshold", help="Cosine similarity threshold for duplicate detection (0.0-1.0)."),
) -> None:
    """Ingest existing markdown analysis documents into DocHub.

    Walks DOCS_DIR for .md files, auto-classifies each into one of 15 SDLC
    doc types (as-is, brd, design, spec, etc.), generates proper frontmatter
    with auto-incrementing doc IDs, extracts cross-references and requirement
    IDs, detects stale/duplicate files, and pushes via the DocHub API.

    Stale and duplicate files are ingested with status=Deprecated (not skipped).

    Supports incremental ingestion: fetches the next available doc ID from DocHub
    so subsequent runs won't collide with previously ingested documents.

    Examples:

        # Dry run — preview classification, dedup, and stale reports
        python -m app ingest-docs ./docs -p myproject --dry-run

        # Push to DocHub API
        python -m app ingest-docs ./docs -p myproject \\
            --push api --api-url http://localhost:8000 --api-key sk-xxx

        # Skip duplicate detection (faster for clean collections)
        python -m app ingest-docs ./docs -p myproject \\
            --no-detect-duplicates --push api --api-url http://localhost:8000
    """
    from ai_discovery.ingest.classifier import ALL_DOC_TYPES
    from ai_discovery.ingest.runner import run_ingest

    if push_mode not in _VALID_PUSH_MODES:
        console.print(f"[red]Invalid --push value '{push_mode}'. Must be: api | gitea[/]")
        raise typer.Exit(code=1)

    if default_type not in ALL_DOC_TYPES:
        console.print(f"[red]Invalid --default-type '{default_type}'.[/]")
        console.print(f"  Valid types: {', '.join(sorted(ALL_DOC_TYPES))}")
        raise typer.Exit(code=1)

    mode_label = "[yellow]DRY RUN[/]" if dry_run else f"push={push_mode}"
    console.print(f"[bold green]discover ingest-docs[/] dir={docs_dir} project={project_slug} {mode_label}")
    if detect_duplicates:
        console.print(f"  dedup=on threshold={similarity_threshold}")

    run_ingest(
        docs_dir=docs_dir,
        project_slug=project_slug,
        push_mode=push_mode,
        dry_run=dry_run,
        batch_size=batch_size,
        default_type=default_type,
        domain_from_dirs=not no_domain_dirs,
        detect_dups=detect_duplicates,
        similarity_threshold=similarity_threshold,
        api_url=api_url,
        api_key=api_key,
        api_token=api_token,
        gitea_url=gitea_url,
        gitea_token=gitea_token,
    )


@app.command("export-graph")
def export_graph(
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Project slug whose scan to export."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o",
        help="Output dir (must match the scan's --output).",
    ),
    out_file: Optional[Path] = typer.Option(
        None, "--out",
        help="Destination JSON path (default: <output-dir>/knowledge-graph-<slug>.json).",
    ),
    scan_id: Optional[int] = typer.Option(
        None, "--scan-id", help="Export a specific scan (default: latest).",
    ),
) -> None:
    """Export one canonical knowledge-graph JSON for a completed scan (A-4).

    Contains nodes, call edges (confidence + resolution stage), domains, FK
    relationships, entity FSMs, and a doc/screen index — a portable, diffable
    VIEW of the discovery DB. Commit it so teammates and tools can consume
    scan results without the SQLite file.

    Examples:

        discover export-graph -p todoapp
        discover export-graph -p todoapp --out ./kg.json --scan-id 3
    """
    from ai_discovery.generators.graph_export import write_graph_export

    layout = _layout(output, project_slug)
    db_path = layout.db_path.resolve()
    if not db_path.exists():
        console.print(
            f"[red]Database not found:[/] {db_path}. Run `discover scan` first, "
            f"or check --output/--project-slug."
        )
        raise typer.Exit(code=1)

    dest = out_file or layout.graph_export_path
    try:
        path = write_graph_export(db_path, project_slug, dest, scan_id=scan_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1)

    import json as _json
    graph = _json.loads(path.read_text())
    console.print(
        f"[bold green]export-graph[/] wrote [cyan]{path}[/]\n"
        f"  scan #{graph['scan']['id']} ({graph['scan']['commit_sha'] or 'no sha'}) — "
        f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
        f"{len(graph['domains'])} domains, {len(graph['fsms'])} FSMs, "
        f"{len(graph['docs'])} docs, {len(graph['screens'])} screens"
    )


@app.command()
def detect_screens(
    repo: str = typer.Argument(..., help="Path to repository to scan for screens."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o",
        help="Output directory for screen_map.yaml and menu_tree.json.",
    ),
    include_backend: bool = typer.Option(
        True, "--include-backend/--no-backend",
        help="Include backend mapping (controllers, services, tables)."
    ),
) -> None:
    """Auto-detect menu system and screens in a repository.

    Scans for menu definitions (JSON/YAML, TypeScript constants, framework routing)
    and extracts screen definitions. Optionally maps screens to backend components.
    Outputs screen_map.yaml and menu_tree.json.
    """
    from ai_discovery.menu_detector import detect_and_build_screens
    from ai_discovery.screen_mapper import ScreenMapper
    import yaml
    import json

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    repo_path = Path(repo)
    console.print(f"[bold green]discover detect-screens[/] repo={repo}")

    with console.status("[bold cyan]Detecting menu system..."):
        menu_items, screens = detect_and_build_screens(repo_path)

    if not screens:
        console.print("[yellow]No screens detected. Check that menu definitions exist in the repo.[/]")
        raise typer.Exit(code=1)

    # Map screens to backend components if requested
    screen_mappings = []
    if include_backend:
        with console.status("[bold cyan]Mapping screens to backend components..."):
            mapper = ScreenMapper(repo_path)
            for screen in screens:
                try:
                    mapping = mapper.map_screen(screen)
                    screen_mappings.append(mapping)
                except Exception as e:
                    console.print(f"[yellow]Warning: Failed to map {screen.screen_id}: {e}[/]")
                    screen_mappings.append(None)

    # Output screen_map.yaml with backend info if available
    if screen_mappings and any(m is not None for m in screen_mappings):
        screen_map = {
            "screens": [
                m.to_dict() if m is not None else s.to_dict()
                for m, s in zip(screen_mappings, screens)
            ]
        }
    else:
        screen_map = {"screens": [s.to_dict() for s in screens]}

    screen_map_file = output_dir / "screen_map.yaml"
    screen_map_file.write_text(yaml.dump(screen_map, default_flow_style=False))

    # Output menu_tree.json
    if menu_items:
        menu_tree = [item.to_dict() for item in menu_items]
        menu_tree_file = output_dir / "menu_tree.json"
        menu_tree_file.write_text(json.dumps(menu_tree, indent=2))

    console.print(f"[green]✓ Detected {len(screens)} screens[/]")
    console.print(f"  Wrote: {screen_map_file}")
    if menu_items:
        console.print(f"  Wrote: {menu_tree_file}")

    if include_backend:
        mapped_count = sum(1 for m in screen_mappings if m is not None)
        console.print(f"  [cyan]Backend mapping: {mapped_count}/{len(screens)} screens mapped[/]")



def _default_spec_dirs(repo_path: Path) -> list[Path]:
    """Where scans write screen specs: v1 layout first, then the legacy tree."""
    from ai_discovery.core.paths import ProjectLayout

    v1 = ProjectLayout.for_repo(repo_path)
    legacy = ProjectLayout.legacy(Path("./data"), v1.slug, Path("./data"), for_read=True)
    return [v1.docs_dir / "screens", legacy.docs_dir / "screens"]


@app.command()
def verify_drift(
    repo: str = typer.Argument(..., help="Path to repository."),
    spec_dir: Optional[Path] = typer.Option(
        None, "--spec-dir", "-s",
        help="Directory containing generated screen specs (.md files). "
             "Default: the scan's screens dir (<repo>/.discovery/docs/screens, "
             "else ./data/output-<repo-name>/screens).",
    ),
) -> None:
    """Check for drift between screen specs and source code.

    Reads source_hashes from spec frontmatter and compares against current files.
    Exits 1 if any drift is detected and 5 when there is nothing to check
    (no spec dir, or no specs in it) — a CI gate must not pass vacuously.
    """
    from ai_discovery.drift_checker import check_drift

    repo_path = Path(repo)

    if not repo_path.exists():
        console.print(f"[red]Repository not found: {repo_path}[/]")
        raise typer.Exit(code=1)

    candidates = [spec_dir] if spec_dir is not None else _default_spec_dirs(repo_path)
    found = next((d for d in candidates if d.is_dir()), None)
    if found is None:
        console.print(f"[red]Spec directory not found:[/] {', '.join(str(d) for d in candidates)}")
        console.print("  Nothing to check — run `discover scan` first, or pass --spec-dir.")
        raise typer.Exit(code=5)
    spec_dir = found
    if not any(spec_dir.glob("*.md")):
        console.print(f"[red]No specs (*.md) in {spec_dir}[/] — nothing to check.")
        raise typer.Exit(code=5)

    console.print(f"[bold green]discover verify-drift[/] repo={repo}")
    console.print(f"  specs={spec_dir}")

    with console.status("[bold cyan]Checking for drift..."):
        results, report = check_drift(repo_path, spec_dir)

    console.print(report)

    drifted = [r for r in results if r.is_drifted]
    if drifted:
        raise typer.Exit(code=1)
    else:
        console.print("\n[green]✓ All specs are in sync with source code[/]")
        raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
