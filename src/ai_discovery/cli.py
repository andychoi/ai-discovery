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


def _project_output_dir(output: Path, slug: str) -> Path:
    """Track 3: per-project output dir is `<output>/output-<slug>/`.

    Read-side commands fall back to the legacy `<output>/<slug>/` layout if
    the new path doesn't exist but the legacy one does — so existing scans
    keep working without a forced migration. Write-side commands (scan)
    always create the new path.
    """
    new_path = Path(output) / f"output-{slug}"
    legacy_path = Path(output) / slug
    if not new_path.exists() and legacy_path.exists():
        return legacy_path
    return new_path


@app.command()
def init(
    config: Path = typer.Option(
        Path("discovery.yaml"), "--config", "-c",
        help="Output path for generated config template."
    ),
    provider: str = typer.Option(
        "ollama", "--provider",
        help="Default LLM provider (bedrock|ollama|mlx-gemma|mlx-qwen)."
    ),
) -> None:
    """Generate a discovery.yaml config template.

    Creates a discovery.yaml file with all configurable options and helpful
    comments. Useful for getting started or documenting your setup.
    """
    from ai_discovery.config import (
        BedrockConfig, OllamaConfig, MLXGemmaConfig, MLXQwenConfig,
        RagConfig, ProcessMiningConfig, AdvisorConfig
    )

    config_path = Path(config)
    if config_path.exists():
        console.print(f"[yellow]File already exists: {config_path}[/]")
        if not typer.confirm("Overwrite?"):
            console.print("Aborted.")
            raise typer.Exit(code=0)

    # Build template as list of (key, value) tuples to preserve ordering
    # and allow duplicate blank-line entries.
    template: list[tuple[str, object]] = [
        ("# AI-Discovery Configuration", None),
        ("# Customize discovery behavior, model selection, and optional features", None),
        ("", None),
        ("# Provider: bedrock | ollama | mlx-gemma | mlx-qwen", None),
        ("provider", provider),
        ("", None),
        ("# LLM cost limit (USD) — stops pipeline if exceeded", None),
        ("budget_limit_usd", 50.0),
        ("", None),
        ("# Max concurrent workers for parallel processing", None),
        ("max_concurrent", 10),
        ("", None),
        ("# Production mode: use tier3p (deep) instead of tier3d (standard) for doc generation", None),
        ("prod", False),
    ]

    # Provider-specific sections
    if provider == "bedrock":
        bedrock = BedrockConfig()
        template.extend([
            ("", None),
            ("# Bedrock configuration", None),
            ("bedrock", {"region": bedrock.region, "tier1": bedrock.tier1,
                         "tier2": bedrock.tier2, "tier3d": bedrock.tier3d, "tier3p": bedrock.tier3p}),
        ])
    elif provider == "mlx-gemma":
        mlx = MLXGemmaConfig()
        template.extend([
            ("", None),
            ("# MLX Gemma configuration", None),
            ("mlx_gemma", {"base_url": mlx.base_url, "api_key": mlx.api_key,
                           "tier1": mlx.tier1, "tier2": mlx.tier2, "tier3d": mlx.tier3d,
                           "tier3p": mlx.tier3p, "tier1_num_ctx": mlx.tier1_num_ctx}),
        ])
    elif provider == "mlx-qwen":
        mlx = MLXQwenConfig()
        template.extend([
            ("", None),
            ("# MLX Qwen configuration", None),
            ("mlx_qwen", {"base_url": mlx.base_url, "api_key": mlx.api_key,
                          "tier1": mlx.tier1, "tier2": mlx.tier2, "tier3d": mlx.tier3d,
                          "tier3p": mlx.tier3p, "tier1_num_ctx": mlx.tier1_num_ctx}),
        ])
    else:  # ollama
        ollama = OllamaConfig()
        template.extend([
            ("", None),
            ("# Ollama configuration", None),
            ("ollama", {"base_url": ollama.base_url, "tier1": ollama.tier1,
                        "tier2": ollama.tier2, "tier3d": ollama.tier3d, "tier3p": ollama.tier3p,
                        "tier1_num_ctx": ollama.tier1_num_ctx}),
        ])

    # RAG config
    rag = RagConfig()
    template.extend([
        ("", None),
        ("# RAG configuration", None),
        ("rag", {"embedding_provider": rag.embedding_provider, "bedrock_model": rag.bedrock_model,
                 "ollama_model": rag.ollama_model, "chunk_size": rag.chunk_size,
                 "chunk_overlap": rag.chunk_overlap, "top_k": rag.top_k}),
    ])

    # Process Mining config
    mining = ProcessMiningConfig()
    template.extend([
        ("", None),
        ("# Process Mining Configuration (Stage 10.5) — OPTIONAL", None),
        ("# Set enabled: true to enable process mining and conformance analysis", None),
        ("process_mining", {"enabled": mining.enabled, "miner_variant": mining.miner_variant,
                            "fitness_threshold": mining.fitness_threshold,
                            "precision_threshold": mining.precision_threshold,
                            "generalization_threshold": mining.generalization_threshold,
                            "max_traces": mining.max_traces, "output_reports": mining.output_reports}),
    ])

    # Advisor config
    advisor = AdvisorConfig()
    template.extend([
        ("", None),
        ("# Advisor Configuration (beta) — OPTIONAL", None),
        ("# Set enabled: true to enable advisor tool integration", None),
        ("advisor", {"enabled": advisor.enabled, "provider": advisor.provider,
                     "model": advisor.model, "tiers": advisor.tiers,
                     "max_uses_per_call": advisor.max_uses_per_call,
                     "tier3_executor_override": advisor.tier3_executor_override}),
    ])

    # Write as YAML
    yaml_content = _template_to_yaml(template)
    config_path.write_text(yaml_content)
    console.print(f"[green]✓ Generated[/] {config_path}")
    console.print(f"  [dim]Next: edit {config_path} or use with --config {config_path}[/]")
    console.print(f"  [dim]Then: discover scan repo --project-slug=myapp --config {config_path}[/]")


def _yaml_scalar(v: object) -> str:
    """Format a scalar value safely for YAML output."""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return str(v)
    # String: quote if it contains special YAML chars or newlines
    s = str(v)
    if not s or '\n' in s or ':' in s or '#' in s or s.startswith('{') or s.startswith('['):
        return f'"{s}"'
    return s


def _template_to_yaml(entries: list[tuple[str, object]]) -> str:
    """Convert a list of (key, value) tuples to YAML text with comments and blank lines."""
    lines = []
    for key, value in entries:
        if key.startswith("#"):
            lines.append(key)
        elif key == "":
            lines.append("")
        elif value is None:
            continue
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"  {k}: {_yaml_scalar(v)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    return "\n".join(lines) + "\n"


@app.command()
def scan(
    repo: str = typer.Argument(..., help="Path to a folder/git repo, or URL of a git repository to scan."),
    project_slug: str = typer.Option(..., "--project-slug", "-p", help="Target project slug."),
    branch: str = typer.Option("main", "--branch", "-b", help="Git branch to analyse (git scans only)."),
    output: Path = typer.Option(
        Path("./data/discovery-output"), "--output", "-o",
        help="Intermediate output dir (DB, repo clone, cache).",
    ),
    docs_root: Path = typer.Option(
        Path("./data"), "--docs-root",
        help="Root under which generated docs land at {docs_root}/{project_slug}/{PREFIX}/.",
    ),
    provider: Optional[str] = typer.Option(None, "--provider", help="LLM provider (bedrock|ollama)."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to YAML config file."),
    resume: bool = typer.Option(False, "--resume", help="Resume a previous interrupted run."),
    resume_from: Optional[str] = typer.Option(None, "--resume-from", help="Resume from specific phase (e.g. 17, self_review, tier1)."),
    skip_phases: Optional[str] = typer.Option(None, "--skip-phases", help="Skip specific phases (comma-separated, e.g. 16,10)."),
    rescan: bool = typer.Option(False, "--rescan", help="Force full rescan (ignore cache)."),
    budget: Optional[float] = typer.Option(None, "--budget", help="Budget limit in USD."),
    prod: bool = typer.Option(False, "--prod", help="Use production model (tier3p) for doc generation instead of dev model (tier3d)."),
) -> None:
    """Scan a repository, analyse its codebase, and generate SDLC documents offline.

    Writes markdown to {docs_root}/{project_slug}/{PREFIX}/{doc_id}.md — no push.
    Run `discover ingest` afterwards to batch-upsert to DocHub or Gitea.
    """
    from ai_discovery.pipeline import run_pipeline

    # Load config (file → defaults), then apply CLI overrides
    cfg = DiscoveryConfig.load(str(config) if config else None)

    if provider:
        cfg.provider = provider
    if budget is not None:
        cfg.budget_limit_usd = budget
    cfg.prod = prod

    console.print(f"[bold green]discover scan[/] repo={repo} branch={branch} project={project_slug}")
    tier3_label = "tier3p [bold](prod)[/]" if prod else "tier3d [dim](dev)[/]"
    console.print(f"  provider={cfg.provider}  budget=${cfg.budget_limit_usd:.2f}  tier3={tier3_label}")
    # Print the resolved endpoint + tier1 model so silent URL-routing bugs
    # (e.g. localhost inside a container) become visible on the first log line.
    if cfg.provider != "bedrock":
        base_url, _ = cfg.get_endpoint()
        console.print(f"  endpoint={base_url}  tier1={cfg.get_model('tier1')}")

    run_pipeline(
        repo=repo,
        branch=branch,
        project_slug=project_slug,
        output_dir=output,
        docs_root=docs_root,
        config=cfg,
        resume=resume,
        resume_from=resume_from,
        skip_phases=[p.strip() for p in skip_phases.split(",")] if skip_phases else [],
        rescan=rescan,
    )


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

    cfg = DiscoveryConfig.load(str(config) if config else None)
    if provider:
        cfg.provider = provider

    output_dir = _project_output_dir(output, project_slug)
    db_path = output_dir / f"discovery-{project_slug}.db"
    docs_dir = output_dir / "docs"

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

    intermediate = _project_output_dir(output, project_slug)
    db_path = intermediate / f"discovery-{project_slug}.db"
    docs_dir = _project_output_dir(docs_root, project_slug)

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
        Path("./data/discovery-output"), "--db", help="Path to discovery SQLite DB (e.g. data/discovery-output/{slug}/discovery-{slug}.db)."
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

    artifacts_dir = _project_output_dir(output, project_slug)
    fsm_path = artifacts_dir / "entity_state_machines.json"
    if not fsm_path.exists():
        console.print(f"[red]No FSM artifact at {fsm_path}. Run `discover scan` first.[/]")
        raise typer.Exit(1)

    fsms = load_entity_state_machines_json(fsm_path)
    cross_path = artifacts_dir / "cross_entity_transitions.json"
    cross_links = load_cross_entity_links_json(cross_path) if cross_path.exists() else []
    cond_path = artifacts_dir / "entity_conditions.json"
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
        f"[green]{len(federation['conditions'])}[/] conditions"
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

    slug_output = _project_output_dir(output, project_slug).resolve()
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
    tier: str = typer.Option("tier1", "--tier", help="Which generation tier to probe (tier1|tier2|tier3d|tier3p)."),
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

    cfg = DiscoveryConfig.load(str(config) if config else None)
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
        model = cfg.get_model(tier)
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


if __name__ == "__main__":
    app()
