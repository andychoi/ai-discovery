"""Orchestrate the markdown document ingestion pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Table

from .dedup import DuplicateGroup, SplitSuggestion, detect_duplicates, detect_splittable, detect_stale_files
from .frontmatter import DOC_TYPE_PREFIXES
from .reference_extractor import enrich_references
from .walker import IngestFile, batch_files, finalize_bodies, prepare_files

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class IngestResult:
    total: int = 0
    ingested: int = 0
    errors: list[dict] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)
    stale_count: int = 0
    duplicate_groups: int = 0
    split_suggestions: int = 0


def run_ingest(
    docs_dir: Path,
    project_slug: str,
    push_mode: str = "api",
    dry_run: bool = False,
    batch_size: int = 20,
    default_type: str = "as-is",
    domain_from_dirs: bool = True,
    detect_dups: bool = True,
    similarity_threshold: float = 0.85,
    api_url: str | None = None,
    api_key: str | None = None,
    api_token: str | None = None,
    gitea_url: str | None = None,
    gitea_token: str | None = None,
) -> IngestResult:
    """Run the full ingestion pipeline.

    Pipeline:
    1. Walk and classify all markdown files
    2. Extract references (doc IDs, req IDs from content)
    3. Infer links from traceability hierarchy
    4. Detect stale files (version/content signals)
    5. Detect duplicates (TF-IDF cosine similarity)
    6. Generate frontmatter (with links_to, req_ids, status=Deprecated for stale)
    7. Push to DocHub API in batches (or dry-run)
    """
    if not docs_dir.is_dir():
        console.print(f"[red]Directory not found:[/] {docs_dir}")
        return IngestResult()

    # Fetch starting IDs from DocHub
    start_ids = _fetch_start_ids(project_slug, api_url, api_key, api_token)

    # Step 1: Walk + classify
    console.print(f"[bold cyan]Scanning[/] {docs_dir} ...")
    files = prepare_files(
        docs_dir,
        default_type=default_type,
        domain_from_dirs=domain_from_dirs,
        start_ids=start_ids,
    )

    if not files:
        console.print("[yellow]No markdown files found.[/]")
        return IngestResult()

    console.print(f"  Found [bold]{len(files)}[/] markdown files")

    # Step 2-3: Extract references + infer hierarchy links
    console.print("[bold cyan]Extracting[/] references and links ...")
    enrich_references(files)

    # Step 4: Detect stale files
    console.print("[bold cyan]Detecting[/] stale files ...")
    detect_stale_files(files)

    # Step 5: Detect duplicates
    dup_groups: list[DuplicateGroup] = []
    if detect_dups:
        console.print("[bold cyan]Detecting[/] duplicates (TF-IDF) ...")
        dup_groups = detect_duplicates(files, threshold=similarity_threshold)

    # Step 6: Detect split candidates
    split_suggestions = detect_splittable(files)

    # Step 7: Generate frontmatter (status=Deprecated for stale/duplicate files)
    finalize_bodies(files, source_dir=str(docs_dir))

    # Build result
    result = IngestResult(total=len(files))
    for f in files:
        result.by_type[f.doc_type] = result.by_type.get(f.doc_type, 0) + 1
    result.stale_count = sum(1 for f in files if f.stale)
    result.duplicate_groups = len(dup_groups)
    result.split_suggestions = len(split_suggestions)

    # Print reports
    _print_summary_table(files, result)
    if dup_groups:
        _print_duplicate_report(dup_groups)
    if split_suggestions:
        _print_split_report(split_suggestions)

    if dry_run:
        _print_dry_run_table(files)
        console.print(f"\n[bold yellow]Dry run complete.[/] {len(files)} files classified. No changes made.")
        return result

    # Push
    if push_mode == "api":
        _push_api_batches(files, project_slug, batch_size, api_url, api_key, api_token, result)
    elif push_mode == "gitea":
        _push_gitea(files, project_slug, gitea_url, gitea_token, result)
    else:
        console.print(f"[red]Invalid push mode: {push_mode}[/]")
        return result

    # Final report
    console.print(f"\n[bold green]Done![/] {result.ingested}/{result.total} ingested")
    if result.stale_count:
        console.print(f"  [yellow]{result.stale_count} files ingested as Deprecated[/]")
    if result.errors:
        console.print(f"[red]{len(result.errors)} errors:[/]")
        for e in result.errors[:10]:
            console.print(f"  [red]x[/] {e['doc_id']}: {e['error']}")
        if len(result.errors) > 10:
            console.print(f"  ... and {len(result.errors) - 10} more")

    return result


def _fetch_start_ids(
    project_slug: str,
    api_url: str | None,
    api_key: str | None,
    api_token: str | None,
) -> dict[str, int]:
    """Fetch next available doc IDs from DocHub to avoid collisions."""
    if not api_url:
        return {}

    headers = _auth_headers(api_key, api_token)
    base = api_url.rstrip("/")
    start_ids: dict[str, int] = {}

    for doc_type in DOC_TYPE_PREFIXES:
        try:
            resp = httpx.get(
                f"{base}/api/docs/{project_slug}/{doc_type}/next-id",
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                next_id = data.get("next_id", "")
                if "-" in next_id:
                    seq = int(next_id.rsplit("-", 1)[1])
                    start_ids[doc_type] = seq
        except Exception as exc:
            logger.debug("Could not fetch next-id for %s: %s", doc_type, exc)

    return start_ids


def _push_api_batches(
    files: list[IngestFile],
    project_slug: str,
    batch_size: int,
    api_url: str | None,
    api_key: str | None,
    api_token: str | None,
    result: IngestResult,
) -> None:
    """Push files to DocHub API in batches."""
    if not api_url:
        console.print("[red]--api-url required for API push mode[/]")
        return

    headers = _auth_headers(api_key, api_token)
    base = api_url.rstrip("/")
    batches = batch_files(files, batch_size)

    with Progress(
        TextColumn("[bold cyan]Pushing"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("batches", total=len(batches))

        for i, batch in enumerate(batches):
            progress.update(task, description=f"batch {i+1}/{len(batches)}")
            payload = {
                "docs": [
                    {
                        "doc_id": f.doc_id,
                        "doc_type": f.doc_type,
                        "body": f.body,
                        "domain": f.domain,
                        "confidence": 0.0,
                    }
                    for f in batch
                ]
            }

            try:
                resp = httpx.post(
                    f"{base}/api/projects/{project_slug}/docs/ingest/batch",
                    headers=headers,
                    json=payload,
                    timeout=120.0,
                )
                resp.raise_for_status()
                data = resp.json()
                result.ingested += data.get("ingested", 0)
                for e in data.get("errors", []):
                    result.errors.append(e)
            except Exception as exc:
                logger.error("Batch %d failed: %s", i + 1, exc)
                for f in batch:
                    try:
                        resp = httpx.post(
                            f"{base}/api/projects/{project_slug}/docs/ingest",
                            headers=headers,
                            json={
                                "doc_id": f.doc_id,
                                "doc_type": f.doc_type,
                                "body": f.body,
                                "domain": f.domain,
                                "confidence": 0.0,
                            },
                            timeout=30.0,
                        )
                        resp.raise_for_status()
                        result.ingested += 1
                    except Exception as inner_exc:
                        result.errors.append({"doc_id": f.doc_id, "error": str(inner_exc)})

            progress.advance(task)


def _push_gitea(
    files: list[IngestFile],
    project_slug: str,
    gitea_url: str | None,
    gitea_token: str | None,
    result: IngestResult,
) -> None:
    """Push files to Gitea — delegates to existing push module."""
    from ..output.push import push_docs

    written_docs = []
    for f in files:
        tmp = Path(f"/tmp/ingest-{f.doc_id}.md")
        tmp.write_text(f.body)
        written_docs.append({
            "doc_id": f.doc_id,
            "doc_type": f.doc_type,
            "domain": f.domain,
            "file_path": str(tmp),
            "confidence": 0.0,
        })

    results = push_docs(
        written_docs, "gitea", project_slug,
        gitea_url=gitea_url, gitea_token=gitea_token,
    )

    for r in results:
        if r["status"] == "pushed":
            result.ingested += 1
        else:
            result.errors.append({"doc_id": r["doc_id"], "error": r.get("error", "unknown")})

    for f in files:
        tmp = Path(f"/tmp/ingest-{f.doc_id}.md")
        tmp.unlink(missing_ok=True)


def _auth_headers(api_key: str | None, api_token: str | None) -> dict[str, str]:
    if api_key:
        return {"X-Api-Key": api_key}
    elif api_token:
        return {"Authorization": f"Bearer {api_token}"}
    return {}


# ── Report printing ──────────────────────────────────────────────────────────

def _print_summary_table(files: list[IngestFile], result: IngestResult) -> None:
    table = Table(title="Classification Summary")
    table.add_column("Doc Type", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Stale", justify="right", style="yellow")
    for dt in sorted(result.by_type):
        stale_in_type = sum(1 for f in files if f.doc_type == dt and f.stale)
        stale_str = str(stale_in_type) if stale_in_type else ""
        table.add_row(dt, str(result.by_type[dt]), stale_str)
    table.add_row("[bold]Total", f"[bold]{len(files)}", f"[bold yellow]{result.stale_count}" if result.stale_count else "")
    console.print(table)


def _print_duplicate_report(groups: list[DuplicateGroup]) -> None:
    table = Table(title=f"Duplicate Groups ({len(groups)} found)")
    table.add_column("Primary (kept as Draft)", style="green", max_width=50)
    table.add_column("Duplicates (Deprecated)", style="yellow", max_width=60)
    table.add_column("Similarity", justify="right")
    for g in groups:
        dup_names = ", ".join(d.path.name for d in g.duplicates)
        table.add_row(g.primary.path.name, dup_names, f"{g.similarity:.2f}")
    console.print(table)


def _print_split_report(suggestions: list[SplitSuggestion]) -> None:
    table = Table(title=f"Split Suggestions ({len(suggestions)} found)")
    table.add_column("File", style="cyan", max_width=50)
    table.add_column("Detected Types", style="yellow")
    table.add_column("Size", justify="right")
    for s in suggestions:
        table.add_row(s.file.path.name, ", ".join(s.detected_types), f"{len(s.file.content):,}")
    console.print(table)


def _print_dry_run_table(files: list[IngestFile]) -> None:
    table = Table(title="File Classification (Dry Run)", show_lines=False)
    table.add_column("Doc ID", style="bold")
    table.add_column("Type", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Domain", style="yellow")
    table.add_column("Links", justify="right", style="dim")
    table.add_column("Reqs", justify="right", style="dim")
    table.add_column("Title", max_width=40)
    table.add_column("Source", style="dim", max_width=40)

    for f in files:
        status = "[red]Deprecated[/]" if f.stale else "Draft"
        table.add_row(
            f.doc_id, f.doc_type, status, f.domain,
            str(len(f.links_to)) if f.links_to else "",
            str(len(f.req_ids)) if f.req_ids else "",
            f.title, f.path.name,
        )

    console.print(table)
