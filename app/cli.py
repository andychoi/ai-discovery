"""Discovery CLI — Typer entry point for brownfield codebase analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

from app.config import DiscoveryConfig

# Load .env from project root (AWS creds, tokens, etc.)
load_dotenv()

console = Console()
app = typer.Typer(name="discover", help="Brownfield codebase discovery & doc generation.")

_VALID_INGEST_TARGETS = {"dochub", "gitea"}


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
    rescan: bool = typer.Option(False, "--rescan", help="Force full rescan (ignore cache)."),
    budget: Optional[float] = typer.Option(None, "--budget", help="Budget limit in USD."),
    prod: bool = typer.Option(False, "--prod", help="Use production model (tier3p) for doc generation instead of dev model (tier3d)."),
) -> None:
    """Scan a repository, analyse its codebase, and generate SDLC documents offline.

    Writes markdown to {docs_root}/{project_slug}/{PREFIX}/{doc_id}.md — no push.
    Run `discover ingest` afterwards to batch-upsert to DocHub or Gitea.
    """
    from app.pipeline import run_pipeline

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
    from app.ai.llm_client import LLMClient
    from app.rag.chat import run_repl

    cfg = DiscoveryConfig.load(str(config) if config else None)
    if provider:
        cfg.provider = provider

    output_dir = Path(output) / project_slug
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
    from app.db import get_conn
    from app.output.doc_generator import _doc_type_prefix
    from app.output.push import push_docs

    if target not in _VALID_INGEST_TARGETS:
        console.print(f"[red]Invalid --target '{target}'. Must be: dochub | gitea[/]")
        raise typer.Exit(code=1)

    intermediate = Path(output) / project_slug
    db_path = intermediate / f"discovery-{project_slug}.db"
    docs_dir = Path(docs_root) / project_slug

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
    from app.ingest.classifier import ALL_DOC_TYPES
    from app.ingest.runner import run_ingest

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
