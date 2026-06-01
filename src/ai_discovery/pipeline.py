"""Discovery pipeline orchestrator.

Wires all phases together: repo resolution, language detection, parsing,
call-graph + domain classification, chunking, RAG embedding, LLM
summarisation (Tier 1), flow analysis (Tier 2), doc rollup (Tier 3),
self-review, and optional push.

Supports --resume (continue an interrupted scan) and --rescan (force
re-scan even when commit SHA hasn't changed).
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .config import DiscoveryConfig
from .db import (
    init_db, get_conn, now_iso, record_phase_start, record_phase_complete,
    record_phase_error, get_all_checkpoints, get_last_complete_phase
)
from .ai.process_miner import mine_scenarios
from .ai.mining_reporter import MiningReporter

logger = logging.getLogger(__name__)
console = Console()

# Phase spec mapping (supports both number and name).
# Phase 2: screen-centric LLM spec generation (NEW, parallel to domain phases)
# Phases 5-19: domain-centric analysis (existing)
# Contiguous integers — no decimal sub-phases. Optional and
# previously-half-step stages (execution_slices, scenario_flow_inference,
# visual_artifacts, process_mining) get their own integer slot. Inline
# operations that are not checkpointed (link_entry_points, fsm_rollup_etc)
# are labelled with letter suffixes (8a, 8b) in code comments only and do
# not appear in this dict.
_PHASE_SPECS = {
    2: "screen_llm_specs",
    5: "lang_detect",
    6: "parse",
    7: "domain_classify",
    8: "execution_slices",
    9: "chunk",
    10: "rag_embed",
    11: "tier1_summarize",
    12: "tier2_flow_analysis",
    13: "scenario_flow_inference",
    14: "tier3_doc_rollup",
    15: "visual_artifacts",
    16: "process_mining",
    17: "self_review",
    18: "render_markdown",
    19: "finalise",
}
_PHASE_NAME_TO_NUM = {v: k for k, v in _PHASE_SPECS.items()}


def _parse_phase_spec(spec: str) -> float:
    """Parse a phase spec (number or name) to phase number.

    Examples: "14" -> 14.0, "self_review" -> 14.0
    """
    spec = spec.strip().lower()
    # Try as number first
    try:
        return float(spec)
    except ValueError:
        pass
    # Try as name
    if spec in _PHASE_NAME_TO_NUM:
        return _PHASE_NAME_TO_NUM[spec]
    raise ValueError(
        f"Invalid phase spec '{spec}'. Use phase number (5, 11, 14.0) or name "
        f"({', '.join(sorted(_PHASE_NAME_TO_NUM.keys()))})"
    )


def _next_phase(phase_num: float) -> float | None:
    """Return the next phase number after the given one, using the phase spec list."""
    sorted_phases = sorted(_PHASE_SPECS.keys())
    for p in sorted_phases:
        if p > phase_num:
            return p
    return None


def _get_start_phase(db_path: Path, scan_id: int, resume_from: str | None) -> float | None:
    """Determine which phase to start from.

    Returns:
        Phase number to start from, or None to start from beginning
    """
    if resume_from:
        return _parse_phase_spec(resume_from)
    # Find last complete phase and return the one after it
    last_complete = get_last_complete_phase(db_path, scan_id)
    if last_complete is not None:
        return _next_phase(last_complete)
    return None


def _phase_should_run(phase_num: float, start_phase: float | None, skip_phases: list[str]) -> bool:
    """Determine if a phase should run based on resume and skip settings.

    Returns False if:
    - phase_num < start_phase (already completed in previous run)
    - phase_num is in skip_phases (user explicitly skipping)
    """
    if start_phase is not None and phase_num < start_phase:
        return False
    if _should_skip_phase(phase_num, skip_phases):
        return False
    return True


def _should_skip_phase(phase_num: float, skip_phases: list[str]) -> bool:
    """Check if a phase should be skipped."""
    if not skip_phases:
        return False
    for skip_spec in skip_phases:
        try:
            skip_num = _parse_phase_spec(skip_spec)
            if abs(phase_num - skip_num) < 0.01:  # floating point comparison
                return True
        except ValueError:
            logger.warning(f"Invalid phase spec to skip: {skip_spec}")
    return False


@contextmanager
def _timed(label: str):
    """Context manager that prints elapsed time for a pipeline phase."""
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    console.print(f"  [dim]⏱  {label}: {elapsed:.1f}s[/]")


@contextmanager
def _with_checkpoint(
    db_path: Path,
    scan_id: int,
    phase_num: float,
    phase_name: str,
    metadata: dict = None,
):
    """Context manager that records phase start/complete/error to checkpoints.

    Usage:
        with _with_checkpoint(db_path, scan_id, 5, "lang_detect", {"langs": 3}):
            # do phase work
            pass
    """
    record_phase_start(db_path, scan_id, phase_num, phase_name)
    try:
        yield
        record_phase_complete(db_path, scan_id, phase_num, phase_name, metadata)
    except Exception as e:
        record_phase_error(db_path, scan_id, phase_num, phase_name, str(e))
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_tier1_targets(chunks: list) -> int:
    """Number of chunks that Phase 11 (Tier 1) will actually send to the LLM.

    Mirrors the filtering in summarize_chunks: skip structural types and test files.
    """
    from .ai.summarizer import _SKIP_TYPES, _is_test_chunk

    return sum(
        1 for c in chunks
        if c.chunk_type not in _SKIP_TYPES and not _is_test_chunk(c.file_path)
    )


def _build_rag_chunks(chunks: list, config: DiscoveryConfig) -> list:
    """Build the RAG chunk set, honoring config.rag filters."""
    from .ai.chunker import chunk_for_rag
    from .ai.summarizer import _is_test_chunk

    source = chunks
    if config.rag.skip_tests:
        source = [c for c in source if not _is_test_chunk(c.file_path)]
    return chunk_for_rag(source, config.rag.chunk_size, config.rag.chunk_overlap)


def _budget_ok(llm_client, config: DiscoveryConfig, phase_label: str) -> bool:
    """Return True if budget still has room; print warning and return False otherwise.

    Local providers (ollama) have no cost — budget checks are skipped.
    For Bedrock, budget_limit_usd: 0.0 means 'no LLM' (pipeline stops immediately).
    """
    if config.provider != "bedrock":
        return True
    cost = llm_client.total_cost_usd()
    if cost >= config.budget_limit_usd:
        console.print(
            f"[red bold]Budget limit reached[/] (${cost:.2f} >= ${config.budget_limit_usd:.2f}) "
            f"before {phase_label} -- stopping pipeline."
        )
        return False
    return True


def _find_previous_run(db_path: Path, repo: str, branch: str, commit_sha: str | None = None):
    """Find the most recent scan_run for the same repo.

    Prefers an exact commit_sha match (repo-independent of branch name), then
    falls back to repo+branch. This handles repos whose default branch is
    'master' while the CLI default is 'main'.
    """
    conn = get_conn(db_path)
    try:
        if commit_sha:
            row = conn.execute(
                "SELECT * FROM scan_runs WHERE (repo_url = ? OR repo_path = ?) "
                "AND commit_sha = ? ORDER BY id DESC LIMIT 1",
                (repo, repo, commit_sha),
            ).fetchone()
            if row:
                return row
        row = conn.execute(
            "SELECT * FROM scan_runs WHERE (repo_url = ? OR repo_path = ?) AND branch = ? "
            "ORDER BY id DESC LIMIT 1",
            (repo, repo, branch),
        ).fetchone()
        return row
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    repo: str,
    branch: str,
    project_slug: str,
    output_dir: Path,
    config: DiscoveryConfig,
    docs_root: Path = Path("./data"),
    resume: bool = False,
    resume_from: str | None = None,
    skip_phases: list[str] | None = None,
    rescan: bool = False,
) -> None:
    """Run the full discovery pipeline in offline mode.

    Intermediate state (SQLite DB, repo clone, cache) lives under
    output_dir/{project_slug}/. Final markdown is written to
    docs_root/{project_slug}/{PREFIX}/{doc_id}.md. Delivery to DocHub/Gitea
    is a separate step — see `discover ingest`.

    Args:
        resume: Continue from last complete phase
        resume_from: Start from a specific phase (phase number or name, e.g. "14", "self_review")
        skip_phases: List of phases to skip (e.g. ["16", "10"])
    """

    # Track 3: per-project output dir is `output/output-<slug>/`. Read-side
    # CLI commands (chat, view, ingest) fall back to legacy `output/<slug>/`
    # when only the legacy path exists, so previously-scanned repos keep
    # working without forced migration. New scans always create the new path.
    output_dir = Path(output_dir) / f"output-{project_slug}"
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = Path(docs_root) / f"output-{project_slug}"
    db_path = output_dir / f"discovery-{project_slug}.db"
    _pipeline_start = time.perf_counter()

    # ------------------------------------------------------------------
    # 1. Init DB
    # ------------------------------------------------------------------
    init_db(db_path)
    console.print(f"[bold]Pipeline initialised[/]  db={db_path}")

    # ------------------------------------------------------------------
    # 2. Resolve repo (early — needed for accurate branch/SHA matching)
    # ------------------------------------------------------------------
    from .repo.resolver import resolve_repo

    with _timed("repo resolve"), console.status("[bold cyan]Resolving repository..."):
        resolved = resolve_repo(repo, branch, output_dir)
    console.print(
        f"  Repo: [green]{resolved.repo_path}[/]  "
        f"commit=[dim]{resolved.commit_sha[:10]}[/]  branch={resolved.branch}"
    )

    # ------------------------------------------------------------------
    # 3. Resume / rescan checks (uses resolved branch + SHA for accuracy)
    # ------------------------------------------------------------------
    scan_id: int | None = None
    skip_phases = skip_phases or []
    # Validate skip_phases up front — an invalid spec silently no-ops inside
    # _should_skip_phase, which is surprising enough to be worth catching at
    # the entry point where the error can reference the offending value.
    for spec in skip_phases:
        try:
            _parse_phase_spec(spec)
        except ValueError as e:
            raise ValueError(f"--skip-phase {spec!r}: {e}") from e
    prev_run = _find_previous_run(db_path, repo, resolved.branch, commit_sha=resolved.commit_sha)

    if resume and prev_run:
        if prev_run["status"] in ("push_failed", "llm_complete"):
            console.print(
                f"[yellow]Scan #{prev_run['id']} finished LLM tiers — re-rendering markdown from DB.[/]"
            )
            _rewrite_docs_from_db(
                prev_run["id"], db_path, docs_dir, project_slug,
                repo_url=resolved.url or str(resolved.repo_path),
                repo_commit=resolved.commit_sha,
            )
            _finalise_scan(prev_run["id"], db_path, "completed")
            return
        if prev_run["status"] == "completed":
            console.print("[green]Previous scan already completed -- nothing to resume.[/]")
            return
        scan_id = prev_run["id"]
        console.print(f"[yellow]Resuming scan #{scan_id}[/]")
        console.print()
        _display_checkpoint_status(db_path, scan_id)
        console.print()

    if resume_from and prev_run:
        scan_id = prev_run["id"]
        try:
            target_phase = _parse_phase_spec(resume_from)
            console.print(f"[yellow]Resuming from phase {target_phase}: {_PHASE_SPECS.get(target_phase, 'unknown')}[/]")
        except ValueError as e:
            console.print(f"[red]Error parsing --resume-from: {e}[/]")
            raise
        console.print()
        _display_checkpoint_status(db_path, scan_id)
        console.print()

    # Compute start_phase: which phase to begin executing from
    start_phase: float | None = None
    if scan_id is not None:
        start_phase = _get_start_phase(db_path, scan_id, resume_from)
        if start_phase:
            console.print(f"[bold cyan]Will skip phases before {start_phase}[/]")

    # Rescan guard: skip if same commit already scanned (no --rescan flag)
    if not rescan and not resume and not resume_from and prev_run:
        if prev_run["commit_sha"] == resolved.commit_sha and prev_run["status"] == "completed":
            console.print(
                f"[green]Commit {resolved.commit_sha[:10]} already scanned "
                f"(run #{prev_run['id']}). Use --rescan to force.[/]"
            )
            return

    # Same-SHA fast-path: --rescan but commit unchanged and scan already done.
    # Skip parse + LLM tiers — reuse cached rollups and re-render markdown.
    # Only valid if the cached scan actually produced rollup docs; otherwise fall
    # through to a real rescan (a "completed" scan with zero docs is a sticky
    # cache trap — see prior bug where empty cache was reused forever).
    cached_doc_count = 0
    if rescan and prev_run and prev_run["commit_sha"] == resolved.commit_sha \
            and prev_run["status"] in ("completed", "push_failed", "llm_complete"):
        _conn = get_conn(db_path)
        try:
            _row = _conn.execute(
                "SELECT COUNT(*) AS n FROM generated_docs WHERE scan_id = ?",
                (prev_run["id"],),
            ).fetchone()
            cached_doc_count = _row["n"] if _row else 0
        finally:
            _conn.close()

    if rescan and prev_run and prev_run["commit_sha"] == resolved.commit_sha \
            and prev_run["status"] in ("completed", "push_failed", "llm_complete") \
            and cached_doc_count > 0:
        console.print(
            f"[cyan]Commit {resolved.commit_sha[:10]} already analysed "
            f"(scan #{prev_run['id']}, status={prev_run['status']}, "
            f"docs={cached_doc_count})[/]\n"
            f"[dim]Skipping parse + LLM tiers — re-rendering cached rollups.[/]"
        )
        _rewrite_docs_from_db(
            prev_run["id"], db_path, docs_dir, project_slug,
            repo_url=resolved.url or str(resolved.repo_path),
            repo_commit=resolved.commit_sha,
        )
        _finalise_scan(prev_run["id"], db_path, "completed")
        return

    # ------------------------------------------------------------------
    # 4. Create scan_run record (if not resuming)
    # ------------------------------------------------------------------
    if scan_id is None:
        conn = get_conn(db_path)
        try:
            cursor = conn.execute(
                "INSERT INTO scan_runs (repo_url, repo_path, commit_sha, branch, "
                "project_slug, started_at, status, config_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resolved.url,
                    str(resolved.repo_path),
                    resolved.commit_sha,
                    resolved.branch,
                    project_slug,
                    now_iso(),
                    "running",
                    json.dumps(asdict(config)),
                ),
            )
            scan_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()
        console.print(f"  Scan run [bold]#{scan_id}[/] created")

    # LLM client is constructed up-front (cheap — it only wraps config) so every
    # phase that needs it can use it. CRIT-1: it was previously built just before
    # Phase 10, which made Phase 2 (screen specs) raise UnboundLocalError on any
    # repo with a detectable menu, since `llm_client` is a function-local of
    # run_pipeline. Keep this binding ahead of Phase 2.
    from .ai.llm_client import LLMClient

    llm_client = LLMClient(config)

    # ------------------------------------------------------------------
    # 2. Screen-centric LLM spec generation (PARALLEL to domain analysis)
    # ------------------------------------------------------------------
    from .menu_detector import detect_and_build_screens
    from .screen_mapper import map_all_screens
    from .ai.screen_spec_generator import (
        generate_all_screen_specs, persist_screen_specs,
    )
    from .generators.screen_doc_writer import write_all_screen_specs

    if _phase_should_run(2, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 2, "screen_llm_specs"):
            console.print("[bold cyan]Phase 2: Generating screen specs...[/]")

            # Detect screens from menu
            with _timed("screen detect"), console.status("[bold cyan]Detecting screens..."):
                menu_items, screens = detect_and_build_screens(resolved.repo_path)

            if not screens:
                console.print("[yellow]No screens detected — skipping Phase 2 LLM.[/]")
            else:
                console.print(f"  Screens detected: [green]{len(screens)}[/]")

                # Map screens to backend
                with _timed("screen map"), console.status("[bold cyan]Mapping screens to backend..."):
                    screen_mappings = map_all_screens(screens, resolved.repo_path)

                # Generate specs via LLM
                if not _budget_ok(llm_client, config, "Screen spec generation"):
                    console.print("[yellow]Budget exceeded — skipping Phase 2 LLM.[/]")
                else:
                    with _timed("screen specs"), Progress(
                        SpinnerColumn(),
                        TextColumn("[bold cyan]Generating screen specs"),
                        BarColumn(),
                        TaskProgressColumn(),
                        console=console,
                    ) as progress:
                        ptask = progress.add_task("Generating", total=len(screen_mappings))

                        def _on_screen_progress(done: int, total: int):
                            progress.update(ptask, completed=done, total=total)

                        screen_specs = generate_all_screen_specs(
                            screen_mappings,
                            llm_client,
                            db_path,
                            max_workers=max(1, config.max_concurrent or 1),
                            on_progress=_on_screen_progress,
                        )

                    # Write specs to disk
                    if screen_specs:
                        with _timed("write screen specs"), console.status(
                            "[bold cyan]Writing screen specs to disk..."
                        ):
                            write_results = write_all_screen_specs(
                                screen_specs,
                                docs_dir,
                                project_slug,
                                repo_url=resolved.url or str(resolved.repo_path),
                                repo_commit=resolved.commit_sha,
                            )
                            written = sum(1 for r in write_results if r["status"] == "written")
                            console.print(f"  Screen specs: [green]{written}/{len(screen_specs)}[/] written")

                    # Persist to DB
                    persist_screen_specs(screen_specs, db_path, scan_id)
                    screen_cost = llm_client.get_costs().get("screen", {}).get("est_usd", 0.0)
                    console.print(
                        f"  Screen cost: ${screen_cost:.4f}  "
                        f"(total so far: ${llm_client.total_cost_usd():.4f})"
                    )
    else:
        console.print("[dim]Phase 2 (screen_llm_specs): skipped (already complete)[/]")

    # ------------------------------------------------------------------
    # 5. Detect languages
    # ------------------------------------------------------------------
    from .repo.lang_detector import detect_languages

    if _phase_should_run(5, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 5, "lang_detect"):
            with _timed("lang detect"), console.status("[bold cyan]Detecting languages..."):
                lang_stats = detect_languages(resolved.repo_path)
            lang_names = ", ".join(sorted(lang_stats.keys())) or "(none)"
            console.print(f"  Languages detected: [green]{lang_names}[/]")
    else:
        console.print("[dim]Phase 5 (lang_detect): skipped (already complete)[/]")
        with _timed("lang detect (cached)"), console.status("[bold cyan]Detecting languages (for resume)..."):
            lang_stats = detect_languages(resolved.repo_path)

    # ------------------------------------------------------------------
    # 6. Parse files
    # ------------------------------------------------------------------
    from .repo.file_walker import walk_repo
    from .parsers.python_parser import PythonParser
    from .parsers.csharp import CSharpParser
    from .parsers.java import JavaParser
    from .parsers.javascript import JavaScriptParser

    if _phase_should_run(6, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 6, "parse"):
            parsers = [PythonParser(), CSharpParser(), JavaParser(), JavaScriptParser()]
            all_nodes: list = []
            parse_errors = 0

            def _parse_one(file_path):
                """Parse a single file with the first matching parser. Returns
                (nodes, error_exc) — exc is None on success."""
                for p in parsers:
                    if p.can_parse(file_path):
                        try:
                            return p.parse_file(file_path), None
                        except Exception as exc:
                            return [], (file_path, exc)
                return [], None

            with _timed("parse"), Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]Parsing files..."),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                files = list(walk_repo(resolved.repo_path, set(lang_stats.keys())))
                task = progress.add_task("Parsing", total=len(files))

                # tree-sitter's C-level parse releases the GIL, so threads give
                # real parallelism here. Bounded by max_concurrent so we don't
                # starve the main thread or OS file-descriptor limits.
                from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

                workers = max(1, int(config.max_concurrent or 1))
                with ThreadPoolExecutor(max_workers=workers) as parse_pool:
                    futures = {parse_pool.submit(_parse_one, f): f for f in files}
                    for fut in _as_completed(futures):
                        try:
                            nodes, err = fut.result()
                        except Exception as exc:  # shouldn't happen — _parse_one catches
                            logger.warning("Parse future crashed: %s", exc)
                            parse_errors += 1
                            progress.advance(task)
                            continue
                        if err is not None:
                            fpath, exc = err
                            logger.warning("Parse error: %s: %s", fpath, exc)
                            parse_errors += 1
                        else:
                            all_nodes.extend(nodes)
                        progress.advance(task)

            console.print(
                f"  Parsed [green]{len(all_nodes)}[/] code nodes from "
                f"[green]{len(files)}[/] files"
                + (f"  ([yellow]{parse_errors} errors[/])" if parse_errors else "")
            )
    else:
        console.print("[dim]Phase 6 (parse): loading from DB...[/]")
        all_nodes = _load_code_nodes_from_db(db_path, scan_id)
        console.print(f"  Loaded [green]{len(all_nodes)}[/] code nodes from DB")

    if not all_nodes:
        console.print("[yellow]No code nodes found -- nothing to analyse.[/]")
        _finalise_scan(scan_id, db_path, "completed")
        return

    # ------------------------------------------------------------------
    # 7. Classify domains, then persist nodes, then build graphs
    # ------------------------------------------------------------------
    from .graph.call_graph import build_call_graph, ExecutionSliceBuilder
    from .graph.domain_classifier import classify_domains

    if _phase_should_run(7, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 7, "domain_classify"):
            with _timed("domain classify"), console.status("[bold cyan]Classifying domains..."):
                domains_dict = classify_domains(all_nodes)
            domains = list(domains_dict.values())
            console.print(f"  Domains: [green]{len(domains)}[/] ({', '.join(d.name for d in domains)})")

            with _timed("persist nodes"), console.status("[bold cyan]Persisting code nodes..."):
                conn = get_conn(db_path)
                try:
                    for node in all_nodes:
                        conn.execute(
                            "INSERT OR IGNORE INTO code_nodes "
                            "(scan_id, file_path, language, node_type, name, qualified_name, "
                            "line_start, line_end, source_code, annotations, params, "
                            "return_type, framework_hints, domain) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                scan_id,
                                node.file_path,
                                node.language,
                                node.node_type,
                                node.name,
                                node.qualified_name,
                                node.line_start,
                                node.line_end,
                                node.source_code,
                                json.dumps(node.annotations),
                                json.dumps(node.params),
                                node.return_type,
                                json.dumps(node.framework_hints),
                                node.domain,
                            ),
                        )
                    conn.commit()
                finally:
                    conn.close()
            console.print(f"  Persisted [green]{len(all_nodes)}[/] code nodes")

            with _timed("call graph"), console.status("[bold cyan]Building call graph..."):
                edges = build_call_graph(all_nodes)
            console.print(f"  Call graph: [green]{len(edges)}[/] edges")

            # Distribute call edges to domains
            node_domain_map = {n.qualified_name: n.domain for n in all_nodes}
            for edge in edges:
                caller_domain = node_domain_map.get(edge.caller)
                callee_domain = node_domain_map.get(edge.callee)
                if caller_domain and caller_domain in domains_dict:
                    if callee_domain == caller_domain:
                        domains_dict[caller_domain].internal_edges.append(edge)
                    else:
                        domains_dict[caller_domain].external_edges.append(edge)

            # Persist domains and edges
            conn = get_conn(db_path)
            try:
                for domain in domains:
                    conn.execute(
                        "INSERT OR IGNORE INTO domains (scan_id, name, node_count, entry_points, tech_stack) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            scan_id,
                            domain.name,
                            len(domain.nodes),
                            json.dumps([ep.qualified_name for ep in domain.entry_points]),
                            json.dumps(domain.tech_stack),
                        ),
                    )
                # Build a qualified_name -> id map for edges
                rows = conn.execute(
                    "SELECT id, qualified_name FROM code_nodes WHERE scan_id = ?",
                    (scan_id,),
                ).fetchall()
                qn_to_id = {r["qualified_name"]: r["id"] for r in rows}
                edge_rows = [
                    (
                        scan_id,
                        qn_to_id.get(edge.caller),
                        qn_to_id.get(edge.callee),
                        edge.callee,
                        edge.edge_type,
                        edge.confidence,
                    )
                    for edge in edges
                    if qn_to_id.get(edge.caller) is not None
                ]
                conn.executemany(
                    "INSERT INTO call_edges (scan_id, caller_id, callee_id, callee_name, edge_type, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                    edge_rows,
                )
                conn.commit()
            finally:
                conn.close()
    else:
        console.print("[dim]Phase 7 (domain_classify): loading from DB...[/]")
        domains_dict = _load_domains_from_db(db_path, scan_id, all_nodes)
        domains = list(domains_dict.values())
        edges = build_call_graph(all_nodes)
        console.print(f"  Loaded [green]{len(domains)}[/] domains, [green]{len(edges)}[/] edges from DB")

    # 8: Build Execution Slices
    if _phase_should_run(8, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 8, "execution_slices"):
            with _timed("execution slices"), console.status("[bold cyan]Building execution slices..."):
                slice_builder = ExecutionSliceBuilder(all_nodes, edges)
                scenarios = slice_builder.build_all_scenarios()
            console.print(f"  Execution slices: [green]{len(scenarios)}[/] scenarios identified")
    else:
        console.print("[dim]Phase 8 (execution_slices): rebuilding (cheap)...[/]")
        slice_builder = ExecutionSliceBuilder(all_nodes, edges)
        scenarios = slice_builder.build_all_scenarios()

    # 8a (inline): Link state transitions to entry points (UI/API/Batch/Event)
    from .graph.entry_point_linker import link_entry_points
    all_transitions = [
        n.state_transition
        for s in scenarios
        for n in s.nodes
        if n.state_transition is not None
    ]
    if all_transitions:
        with _timed("entry-point link"), console.status("[bold cyan]Linking state transitions to entry points..."):
            link_entry_points(all_transitions, all_nodes, edges)
        linked = sum(1 for t in all_transitions if t.entry_points)
        console.print(
            f"  Entry-point links: [green]{linked}/{len(all_transitions)}[/] transitions"
        )

    # 8b (inline, Phase 2a–2d): roll transitions up into per-entity FSMs,
    # consolidate name-variants (Order/OrderEntity/order_reducer → one FSM),
    # persist to SQLite, export JSON. This is the canonical backbone artifact
    # — every downstream BPMN/DMN/EARS view is supposed to consume the FSM,
    # not re-scan scenarios.
    from .graph.fsm_rollup import build_entity_state_machines, build_fsms_from_sql_nodes
    from .graph.fsm_identity import consolidate_entities
    from .graph.entity_correlator import (
        detect_denormalization_links,
        mine_cross_entity_transitions,
        mine_entity_conditions,
    )
    from .graph.entity_classifier import classify_entities
    from .graph.guard_parser import parse_cross_entity_guards
    from .graph.fsm_export import (
        write_cross_entity_links_json,
        write_entity_conditions_json,
        write_entity_state_machines_json,
    )
    from .graph.fsm_persistence import persist_entity_state_machines
    from .extractors import extract_sql_entities

    # Phase 2e-1: synthesize classless CodeNodes for tables/views referenced
    # in raw SQL string literals, plus placeholder FSMs for each. The Phase
    # 2d consolidator treats `sql_table` / `sql_view` as class-like, so a
    # synthetic FSM for `orders` (fields from SQL) merges with the classful
    # `Order` FSM (transitions from Python) when stems + fields overlap.
    with _timed("sql extract"), console.status("[bold cyan]Extracting SQL entities..."):
        sql_nodes = extract_sql_entities(all_nodes)
    sql_fsms = build_fsms_from_sql_nodes(sql_nodes) if sql_nodes else []
    if sql_nodes:
        view_count = sum(1 for n in sql_nodes if n.node_type == "sql_view")
        console.print(
            f"  SQL entities: [green]{len(sql_nodes)}[/] synthesized "
            f"([dim]{view_count} views[/])"
        )
    nodes_for_consolidation = all_nodes + sql_nodes

    node_file_index = {n.qualified_name: n.file_path for n in all_nodes}
    with _timed("fsm rollup"), console.status("[bold cyan]Aggregating state transitions into entity FSMs..."):
        raw_fsms = build_entity_state_machines(all_transitions, node_file_index=node_file_index)
    raw_fsms = raw_fsms + sql_fsms
    with _timed("fsm consolidate"), console.status("[bold cyan]Consolidating entity name-variants..."):
        fsms, projection_links = consolidate_entities(raw_fsms, nodes_for_consolidation)
    # Phase 3a: detect denormalized field copies across entities so the
    # classifier's junction rule can see through them.
    with _timed("denorm detect"), console.status("[bold cyan]Detecting denormalized fields..."):
        detect_denormalization_links(fsms)
    denorm_count = sum(1 for f in fsms if f.metadata.get("denormalized_fields"))
    if denorm_count:
        console.print(f"  Denormalized fields: [green]{denorm_count}[/] entities annotated")
    # Phase 2e-2: annotate each FSM with entity_kind so downstream generators
    # can branch on role (transactional vs. master vs. key vs. summary / …).
    with _timed("fsm classify"), console.status("[bold cyan]Classifying entity kinds..."):
        classify_entities(fsms)
    if fsms:
        fsm_json_path = output_dir / "entity_state_machines.json"
        write_entity_state_machines_json(fsms, fsm_json_path)
        if scan_id is not None:
            persist_entity_state_machines(db_path, scan_id, fsms)
        total_transitions = sum(len(f.transitions) for f in fsms)
        merged_count = len(raw_fsms) - len(fsms)
        merge_suffix = f", [yellow]{merged_count}[/] merged" if merged_count else ""
        proj_suffix = f", [dim]{len(projection_links)}[/] projection links" if projection_links else ""
        console.print(
            f"  Entity FSMs: [green]{len(fsms)}[/] entities"
            f"{merge_suffix}{proj_suffix}, "
            f"[green]{total_transitions}[/] deduped transitions → {fsm_json_path.name}"
        )
        kind_counts: dict[str, int] = {}
        for f in fsms:
            k = f.metadata.get("entity_kind", "unknown")
            kind_counts[k] = kind_counts.get(k, 0) + 1
        if kind_counts:
            parts = ", ".join(f"[green]{n}[/] {k}" for k, n in sorted(kind_counts.items()))
            console.print(f"  Entity kinds: {parts}")
        # Phase 3b: cross-entity transition sequence mining. Drives BPMN
        # cross-lane sequence flows and impact analysis.
        with _timed("cross-entity mining"), console.status("[bold cyan]Mining cross-entity transition sequences..."):
            cross_links = mine_cross_entity_transitions(fsms, scenarios)
        if cross_links:
            cross_json_path = output_dir / "cross_entity_transitions.json"
            write_cross_entity_links_json(cross_links, cross_json_path)
            console.print(
                f"  Cross-entity links: [green]{len(cross_links)}[/] sequences → {cross_json_path.name}"
            )
        # Phase 3c: parse guard expressions for cross-entity state refs.
        # Annotates transition metadata in place; downstream DMN/BPMN
        # generators can render these as multi-entity rule inputs.
        with _timed("guard parse"), console.status("[bold cyan]Parsing cross-entity guards..."):
            guarded = parse_cross_entity_guards(fsms)
        if guarded:
            console.print(f"  Cross-entity guards: [green]{guarded}[/] transitions annotated")
        # Phase 3d: mine entity-condition correlations. When a transition on
        # entity Y consistently fires while entity X is in a specific state,
        # that's a DMN rule input ("WHEN Order=submitted, Invoice → pending").
        # Unlike guards (syntactic, from source text), correlations are
        # statistical (from scenario walks) — complementary signals.
        with _timed("condition mining"), console.status("[bold cyan]Mining entity condition correlations..."):
            conditions = mine_entity_conditions(fsms, scenarios)
        if conditions:
            conditions_path = output_dir / "entity_conditions.json"
            write_entity_conditions_json(conditions, conditions_path)
            console.print(
                f"  Entity conditions: [green]{len(conditions)}[/] correlations → {conditions_path.name}"
            )
        # Phase 4: L1/L2 entity-backbone Mermaid view. First consumer of
        # entity_kind + cross_entity_transitions as a unified artifact.
        # Track 2: route into BPMN/ folder alongside ASIS/ASD/ASSC/PF so it
        # ships as a first-class DocHub artifact, not a flat scan-root file.
        from .generators.bpmn_generator import BPMNGenerator
        from .generators.doc_generator import _doc_type_prefix
        bpmn_dir = output_dir / _doc_type_prefix("entity-backbone")
        bpmn_dir.mkdir(parents=True, exist_ok=True)
        backbone_path = bpmn_dir / "entity-backbone.md"
        backbone_path.write_text(
            "# Entity Backbone (Mermaid)\n\n```mermaid\n"
            + BPMNGenerator().generate_entity_backbone_mermaid(fsms, cross_links)
            + "\n```\n"
        )
        # Keep a copy at the legacy path for tools that still read it directly
        # (e.g. external scripts) — same content, two locations during transition.
        (output_dir / "entity_backbone.mmd").write_text(
            BPMNGenerator().generate_entity_backbone_mermaid(fsms, cross_links)
        )
        console.print(f"  Entity backbone: → {backbone_path.relative_to(output_dir)}")

        # Phase 3 deliverable: DMN decision tables on guarded/conditioned
        # transitions. Consumes guards (3c) + conditions (3d) as merged
        # rule inputs; Markdown for PR review (DMN XML export can layer on).
        from .generators.dmn_generator import generate_entity_decisions_markdown
        decisions_md = generate_entity_decisions_markdown(fsms, conditions)
        if "No guarded or conditioned transitions" not in decisions_md:
            dmn_dir = output_dir / _doc_type_prefix("entity-decisions")
            dmn_dir.mkdir(parents=True, exist_ok=True)
            decisions_path = dmn_dir / "entity-decisions.md"
            decisions_path.write_text(decisions_md)
            (output_dir / "entity_decisions.md").write_text(decisions_md)  # legacy mirror
            console.print(f"  Entity decisions: → {decisions_path.relative_to(output_dir)}")

        # Phase 3 deliverable: EARS requirement skeletons. Unlike DMN, emits
        # for every transition with a to_state — each state change is a
        # behavioral fact worth documenting, not only the guarded ones.
        from .generators.ears_generator import generate_entity_ears_markdown
        ears_md = generate_entity_ears_markdown(fsms, conditions)
        if "No transitions found" not in ears_md:
            ears_dir = output_dir / _doc_type_prefix("entity-ears")
            ears_dir.mkdir(parents=True, exist_ok=True)
            ears_path = ears_dir / "entity-ears.md"
            ears_path.write_text(ears_md)
            (output_dir / "entity_ears.md").write_text(ears_md)  # legacy mirror
            console.print(f"  Entity EARS: → {ears_path.relative_to(output_dir)}")

    # ------------------------------------------------------------------
    # 9. Smart chunk
    # ------------------------------------------------------------------
    from .ai.chunker import chunk_code_nodes, chunk_for_rag

    if _phase_should_run(9, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 9, "chunk"):
            with _timed("chunk"), console.status("[bold cyan]Chunking code nodes..."):
                chunks = chunk_code_nodes(all_nodes)
                rag_chunks = _build_rag_chunks(chunks, config)
            tier1_count = _count_tier1_targets(chunks)
            console.print(
                f"  Chunks: [green]{len(chunks)}[/] parsed "
                f"([green]{tier1_count}[/] for Tier 1), "
                f"[green]{len(rag_chunks)}[/] RAG chunks"
            )
    else:
        console.print("[dim]Phase 9 (chunk): rebuilding (cheap)...[/]")
        chunks = chunk_code_nodes(all_nodes)
        rag_chunks = _build_rag_chunks(chunks, config)

    # ------------------------------------------------------------------
    # 10. Embed for RAG
    # ------------------------------------------------------------------
    from .rag.embedder import embed_chunks

    # llm_client is already constructed up-front (before Phase 2); see CRIT-1 note.

    if not config.rag.enabled:
        console.print("[dim]Phase 10 (rag_embed): disabled via config.rag.enabled=false[/]")
    elif _phase_should_run(10, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 10, "rag_embed"):
            # Pre-warm small models that stay resident for the whole run.
            # (No-op on Bedrock; on Ollama this pins them with keep_alive=2h so they
            # survive the 5-minute default unload timer across phase transitions.)
            if config.provider in ("ollama", "mlx-gemma", "mlx-qwen"):
                from ai_discovery.shared.model_defaults import MODELS
                emb_model = MODELS.get(config.provider, {}).get("embedding", config.rag.ollama_model)
                console.print(f"[dim]Warming embedding model ({emb_model})...[/]")
                llm_client.warm_embedding("2h")
                tier1_model = config.get_model("tier1")
                console.print(f"[dim]Warming tier1 ({tier1_model})...[/]")
                llm_client.warm("tier1", "2h")

            with _timed("RAG embed"):
                try:
                    workers = max(1, int(config.max_concurrent or 1))
                    with Progress(
                        TextColumn("[bold cyan]Embedding RAG chunks"),
                        BarColumn(),
                        MofNCompleteColumn(),
                        TextColumn("•"),
                        TimeElapsedColumn(),
                        TextColumn("•"),
                        TimeRemainingColumn(),
                        console=console,
                        transient=True,
                    ) as progress:
                        task = progress.add_task("embed", total=len(rag_chunks))

                        def _on_progress(done: int, total: int) -> None:
                            progress.update(task, completed=done)

                        embed_result = embed_chunks(
                            rag_chunks, db_path, llm_client,
                            max_workers=workers,
                            progress_callback=_on_progress,
                        )
                    if embed_result.get("resumed"):
                        console.print(
                            f"  RAG embeddings: [green]{embed_result['embedded']}[/] vectors "
                            f"[dim](resumed — skipped re-embedding)[/]"
                        )
                    else:
                        console.print(
                            f"  RAG embeddings: [green]{embed_result['embedded']}[/] vectors "
                            f"(dim={embed_result['dim']}, workers={workers})"
                        )
                except Exception as exc:
                    logger.warning("RAG embedding failed (continuing without RAG): %s", exc)
                    console.print(f"  [yellow]RAG embedding skipped:[/] {exc}")
    else:
        console.print("[dim]Phase 10 (rag_embed): skipped (already complete)[/]")

    # ------------------------------------------------------------------
    # 11. Tier 1: Summarize
    # ------------------------------------------------------------------
    from .ai.summarizer import summarize_chunks, persist_summaries

    if not _phase_should_run(11, start_phase, skip_phases):
        console.print("[dim]Phase 11 (tier1_summarize): loading from DB...[/]")
        summaries_dict = _load_summaries_from_db(db_path, scan_id)
        console.print(f"  Loaded [green]{len(summaries_dict)}[/] summaries from DB")
    elif not _budget_ok(llm_client, config, "Tier 1 summarization"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return
    else:
        with _with_checkpoint(db_path, scan_id, 11, "tier1_summarize"):
            console.print("[bold cyan]Tier 1: Summarizing code chunks...[/]")
            with _timed("Tier 1 summarize"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Tier 1 summaries"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                ptask = progress.add_task("Summarizing", total=len(chunks))

                def _on_summary_progress(done: int, total: int):
                    progress.update(ptask, completed=done, total=total)

                summaries = summarize_chunks(
                    chunks, llm_client, db_path, config.max_concurrent,
                    on_progress=_on_summary_progress,
                    scan_id=scan_id,
                    skip_rag=True,
                    skip_tests=True,
                )

            persist_summaries(summaries, scan_id, db_path)
            summaries_dict = {s["qualified_name"]: s for s in summaries}
            console.print(f"  Summaries: [green]{len(summaries)}[/] (cost so far: ${llm_client.total_cost_usd():.4f})")

    # ------------------------------------------------------------------
    # 12. Tier 2: Flow analysis
    # ------------------------------------------------------------------
    from .ai.flow_analyzer import analyze_all_domains, persist_flows

    if not _phase_should_run(12, start_phase, skip_phases):
        console.print("[dim]Phase 12 (tier2_flow_analysis): loading from DB...[/]")
        flows_by_domain = _load_flows_from_db(db_path, scan_id)
        console.print(f"  Loaded [green]{sum(len(v) for v in flows_by_domain.values())}[/] flows from DB")
    elif not _budget_ok(llm_client, config, "Tier 2 flow analysis"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return
    else:
        with _with_checkpoint(db_path, scan_id, 12, "tier2_flow_analysis"):
            if config.provider == "ollama":
                tier2_model = config.get_model("tier2")
                console.print(f"[dim]Warming tier2 ({tier2_model})...[/]")
                llm_client.warm("tier2", "1h")

            console.print("[bold cyan]Tier 2: Analyzing business flows...[/]")
            with _timed("Tier 2 flow analysis"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Tier 2 flows"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                ptask = progress.add_task("Analyzing", total=len(domains))

                def _on_flow_progress(done: int, total: int):
                    progress.update(ptask, completed=done, total=total)

                flows_by_domain = analyze_all_domains(
                    domains, summaries_dict, llm_client, on_progress=_on_flow_progress,
                    db_path=db_path, scan_id=scan_id,
                )

            total_flows = sum(len(v) for v in flows_by_domain.values())
            # Determine model used for Tier 2
            tier2_model = config.get_model("tier2")
            persist_flows(flows_by_domain, scan_id, db_path, model_used=tier2_model)
            console.print(f"  Flows: [green]{total_flows}[/] across {len(flows_by_domain)} domains")

    # 13: Scenario Flow Inference
    from .ai.flow_analyzer import ScenarioFlowInference
    if _phase_should_run(13, start_phase, skip_phases):
        with _with_checkpoint(db_path, scan_id, 13, "scenario_flow_inference"):
            console.print("[bold cyan]Phase 13: Reconstructing scenario flows...[/]")
            scenario_flows = []
            flow_inference = ScenarioFlowInference(llm_client)

            with _timed("scenario inference"), Progress(
                SpinnerColumn(),
                TextColumn("[bold]Scenario flow inference"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Inferring", total=len(scenarios))
                for scenario in scenarios:
                    try:
                        flow = flow_inference.infer_flow(scenario, summaries_dict)
                        scenario_flows.append(flow)
                    except Exception as e:
                        logger.warning(f"Failed to infer flow for {scenario.scenario_id}: {e}")
                    progress.advance(task)
            console.print(f"  Scenario flows: [green]{len(scenario_flows)}[/] reconstructed")
    else:
        console.print("[dim]Phase 13 (scenario_flow_inference): rebuilding (cheap)...[/]")
        scenario_flows = []
        flow_inference = ScenarioFlowInference(llm_client)
        for scenario in scenarios:
            try:
                flow = flow_inference.infer_flow(scenario, summaries_dict)
                scenario_flows.append(flow)
            except Exception:
                pass

    # Free tier2 (~17 GB) before loading tier3 (~20 GB).
    tier2_model = config.get_model("tier2")
    if config.provider == "ollama":
        console.print(f"[dim]Unloading tier2 ({tier2_model})...[/]")
        llm_client.unload("tier2")

    # ------------------------------------------------------------------
    # 14. Tier 3: Doc rollup
    # ------------------------------------------------------------------
    if not _budget_ok(llm_client, config, "Tier 3 doc rollup"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return

    from .ai.rollup import generate_all_docs, persist_rollups, DOC_TYPES

    with _with_checkpoint(db_path, scan_id, 14, "tier3_doc_rollup"):
        if config.provider == "ollama":
            tier3_model = config.get_model("tier3")
            console.print(f"[dim]Warming tier3 ({tier3_model})...[/]")
            llm_client.warm("tier3", "1h")

        console.print("[bold cyan]Tier 3: Generating SDLC documents...[/]")
        with _timed("Tier 3 doc rollup"), Progress(
            SpinnerColumn(),
            TextColumn("[bold]Tier 3 docs"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            total_docs = len(domains) * len(DOC_TYPES)
            ptask = progress.add_task("Generating", total=total_docs)

            def _on_rollup_progress(done: int, total: int):
                progress.update(ptask, completed=done, total=total)

            rollups = generate_all_docs(
                domains, summaries_dict, flows_by_domain, llm_client,
                on_progress=_on_rollup_progress,
                max_workers=config.max_concurrent,
                db_path=db_path,
                scan_id=scan_id,
            )

        persist_rollups(rollups, scan_id, db_path, project_slug)
        console.print(f"  Documents: [green]{len(rollups)}[/] generated")

    # 15: Generate Visual Artifacts (BPMN + Mermaid sequence/flowchart) and persist
    from .generators.bpmn_generator import BPMNGenerator
    from .ai.flow_analyzer import persist_scenario_flows

    with _with_checkpoint(db_path, scan_id, 15, "visual_artifacts"):
        bpmn_gen = BPMNGenerator()
        console.print("[bold cyan]Phase 15: Generating visual artifacts...[/]")

        scenario_artifacts: dict[str, dict] = {}
        for flow in scenario_flows:
            scenario_artifacts[flow.scenario_id] = {
                "mermaid": bpmn_gen.generate_mermaid_sequence(flow),
                "mermaid_flowchart": bpmn_gen.generate_mermaid_flowchart(flow),
                "bpmn": bpmn_gen.generate_bpmn_xml(flow),
                "ipo": bpmn_gen.generate_ipo_markdown(flow),
            }
        persist_scenario_flows(scenario_flows, scenario_artifacts, scan_id, db_path)
        console.print(f"  Visual artifacts: [green]{len(scenario_artifacts)}[/] scenarios persisted")

    # ------------------------------------------------------------------
    # 16 OPTIONAL: Process Mining & Conformance
    # ------------------------------------------------------------------
    mining_results: dict = {}
    mining_cfg = getattr(config, 'process_mining', None)
    mining_on = bool(mining_cfg and getattr(mining_cfg, 'enabled', False))
    if mining_on:
        with _with_checkpoint(db_path, scan_id, 16, "process_mining"):
            console.print("[bold cyan]Phase 16: Process mining & conformance analysis...[/]")
            with _timed("process mining"), console.status("[bold cyan]Mining scenarios..."):
                try:
                    mining_results = _mine_processes(scenario_flows, output_dir)
                    console.print(
                        f"  Process mining: [green]{len(mining_results)}[/] scenarios analysed"
                    )
                except Exception as e:
                    logger.error(f"Process mining failed (continuing): {e}")
                    console.print(f"  [yellow]Process mining skipped:[/] {e}")
    else:
        # No checkpoint written — disabled runs shouldn't leave a "16 complete"
        # marker that misleads resume logic.
        console.print("[dim]Phase 16: Process mining disabled (set process_mining.enabled=true to enable)[/]")

    # Free tier3 (~20 GB) so self-review (tier1) has headroom.
    if config.provider == "ollama":
        console.print(f"[dim]Unloading tier3 ({tier3_model})...[/]")
        llm_client.unload("tier3")

    # Checkpoint: LLM tiers done — resume can skip here on rerun
    _finalise_scan(scan_id, db_path, "llm_complete")

    # ------------------------------------------------------------------
    # 17. Self-review (before write so annotations appear in output files)
    # ------------------------------------------------------------------
    if not _budget_ok(llm_client, config, "self-review"):
        console.print("[yellow]Skipping self-review due to budget limit.[/]")
        record_phase_complete(db_path, scan_id, 17, "self_review", {"skipped": "budget"})
    elif not rollups:
        console.print("[yellow]Skipping self-review — no rollup documents were generated.[/]")
        record_phase_complete(db_path, scan_id, 17, "self_review", {"skipped": "no_rollups"})
    else:
        with _with_checkpoint(db_path, scan_id, 17, "self_review"):
            from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed, TimeoutError as _FuturesTimeout
            from .ai.self_review import review_document, get_review_summary, persist_claims, annotate_document

            console.print("[bold cyan]Self-review: verifying claims against source...[/]")

            claims_per_worker = max(1, config.max_concurrent // max(len(rollups), 1))

            # Aggregate totals shared across threads (GIL makes int updates atomic enough here)
            _sr_totals: dict[str, int] = {"verified": 0, "contradicted": 0, "unverified": 0}

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]Self-review[/]"),
                BarColumn(bar_width=30),
                MofNCompleteColumn(),
                TextColumn(
                    "  [green]✓{task.fields[verified]}[/]"
                    " [red]✗{task.fields[contradicted]}[/]"
                    " [yellow]⚠{task.fields[unverified]}[/]"
                ),
                console=console,
                transient=True,
            ) as progress:
                overall = progress.add_task(
                    "Self-review", total=len(rollups),
                    verified=0, contradicted=0, unverified=0,
                )

                def _make_callbacks(rollup):
                    # Per-doc counters (only used to build the completion line)
                    _doc: dict[str, int] = {"verified": 0, "contradicted": 0, "unverified": 0}

                    def on_total_known(n: int):
                        pass  # not needed for aggregate view

                    def on_claim_done(claim):
                        field = claim.status
                        _doc[field] = _doc.get(field, 0) + 1

                    return on_total_known, on_claim_done, _doc

                def _review_one(rollup):
                    on_total, on_done, doc_counts = _make_callbacks(rollup)
                    claims = review_document(
                        rollup.content_md, db_path, llm_client,
                        max_workers=claims_per_worker,
                        on_total_known=on_total,
                        on_claim_done=on_done,
                    )
                    return rollup, claims, doc_counts

                # Per-doc timeout: each rollup gets 5 minutes of wall-clock.
                # We use per-future result(timeout=) so one stuck rollup only
                # kills itself, not the whole phase.
                _PER_DOC_TIMEOUT = 300

                executor = ThreadPoolExecutor(max_workers=min(config.max_concurrent, len(rollups)))
                futures = {executor.submit(_review_one, r): r for r in rollups}
                try:
                    for future in _as_completed(futures):
                        rollup_submitted = futures[future]
                        try:
                            rollup, claims, doc_counts = future.result(timeout=_PER_DOC_TIMEOUT)
                        except _FuturesTimeout:
                            logger.warning(
                                "Self-review timed out for %s/%s after %ds",
                                rollup_submitted.domain, rollup_submitted.doc_type, _PER_DOC_TIMEOUT,
                            )
                            progress.update(overall, advance=1)
                            console.print(
                                f"  [red]✗[/] [dim]{rollup_submitted.domain}[/] › [bold]{rollup_submitted.doc_type}[/]"
                                f"  [red]timeout after {_PER_DOC_TIMEOUT}s[/]"
                            )
                            continue
                        except Exception as exc:
                            logger.warning(
                                "Self-review failed for %s/%s: %s",
                                rollup_submitted.domain, rollup_submitted.doc_type, exc,
                            )
                            progress.update(overall, advance=1)
                            console.print(
                                f"  [red]✗[/] [dim]{rollup_submitted.domain}[/] › [bold]{rollup_submitted.doc_type}[/]"
                                f"  [red]error: {exc}[/]"
                            )
                            continue

                        summary = get_review_summary(claims)

                        # Focused re-generation for sections with bad claims
                        if summary["unverified"] + summary["contradicted"] > 0:
                            from .ai.self_review import regenerate_sections
                            rollup.content_md = regenerate_sections(
                                rollup.content_md, claims, llm_client, db_path=db_path,
                            )

                        rollup.content_md = annotate_document(rollup.content_md, claims)
                        rollup.unverified_claims = summary["unverified"] + summary["contradicted"]
                        # Track 4: replace LLM self-asserted confidence with a
                        # deterministic blend of AST-verified rows + review verdicts.
                        # Verified rows (Track 1 tables) always count as 1.0;
                        # prose claims contribute per their review status.
                        from .ai.rollup import blend_confidence
                        rollup.confidence = blend_confidence(
                            rollup.verified_row_count, summary
                        )

                        # Update aggregate totals and advance overall bar
                        for k in _sr_totals:
                            _sr_totals[k] += doc_counts.get(k, 0)
                        progress.update(overall, advance=1, **_sr_totals)

                        # One compact line per completed doc
                        v = doc_counts["verified"]
                        c = doc_counts["contradicted"]
                        u = doc_counts["unverified"]
                        status = "[red]✗[/]" if c else "[green]✓[/]"
                        console.print(
                            f"  {status} [dim]{rollup.domain}[/] › [bold]{rollup.doc_type}[/]"
                            f"  [green]{v}✓[/] [red]{c}✗[/] [yellow]{u}⚠[/]"
                        )

                        # DB writes serialized after parallel LLM work
                        conn = get_conn(db_path)
                        try:
                            doc_row = conn.execute(
                                "SELECT id FROM generated_docs WHERE scan_id = ? AND domain = ? AND doc_type = ?",
                                (scan_id, rollup.domain, rollup.doc_type),
                            ).fetchone()
                            if doc_row:
                                doc_db_id = doc_row["id"]
                                conn.execute(
                                    "UPDATE generated_docs SET unverified_claims = ?, "
                                    "confidence = ? WHERE id = ?",
                                    (rollup.unverified_claims, rollup.confidence, doc_db_id),
                                )
                                conn.commit()
                                persist_claims(claims, doc_db_id, db_path)
                        finally:
                            conn.close()
                finally:
                    # Wait for in-flight threads to finish and cancel any still
                    # queued. Abandoning threads (wait=False) would let them keep
                    # writing to generated_docs/claims after phase 18 starts
                    # reading those tables — a data-race we refuse to ship.
                    executor.shutdown(wait=True, cancel_futures=True)

            console.print(
                f"  Self-review complete — "
                f"[green]{_sr_totals['verified']} verified[/]  "
                f"[red]{_sr_totals['contradicted']} contradicted[/]  "
                f"[yellow]{_sr_totals['unverified']} unverified[/]"
            )

    # ------------------------------------------------------------------
    # 18. Render markdown files (after self-review so annotations are included)
    # ------------------------------------------------------------------
    with _with_checkpoint(db_path, scan_id, 18, "render_markdown"):
        from .generators.doc_generator import write_docs

        # Compute cross-domain call graph for Layer 2 graph links
        with _timed("domain adjacency"), console.status("[bold cyan]Computing cross-domain links..."):
            domain_adjacency = _compute_domain_adjacency(db_path, scan_id)
        if domain_adjacency:
            console.print(
                f"  Cross-domain edges: [green]{sum(len(v) for v in domain_adjacency.values())}[/] "
                f"pairs across {len(domain_adjacency)} domains"
            )

        from .generators.doc_generator import write_scenario_docs

        with _timed("write markdown"), console.status("[bold cyan]Writing markdown files..."):
            # Track 5: thread scenario_flows into write_docs so ASD can link
            # to its PF scenarios; thread the rollup-domain set into
            # write_scenario_docs so PF only links to existing rollups.
            written = write_docs(
                rollups,
                docs_dir,
                project_slug,
                repo_url=resolved.url or str(resolved.repo_path),
                repo_commit=resolved.commit_sha,
                domain_adjacency=domain_adjacency,
                scenario_flows=scenario_flows,
            )
            scenario_written = write_scenario_docs(
                scenario_flows,
                scenario_artifacts,
                docs_dir,
                project_slug,
                repo_url=resolved.url or str(resolved.repo_path),
                repo_commit=resolved.commit_sha,
                mining_results=mining_results,
                rollup_domains={r.domain for r in rollups},
            )
            written.extend(scenario_written)
        console.print(f"  Written: [green]{len(written)}[/] markdown files to {docs_dir}")

        # Keep DB.doc_id aligned with the on-disk filenames so `discover ingest`
        # can locate every file without re-deriving paths.
        conn = get_conn(db_path)
        try:
            for doc in written:
                conn.execute(
                    "UPDATE generated_docs SET doc_id = ? WHERE scan_id = ? AND domain = ? AND doc_type = ?",
                    (doc["doc_id"], scan_id, doc["domain"], doc["doc_type"]),
                )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 19. Finalise (offline-only — no push)
    # ------------------------------------------------------------------
    with _with_checkpoint(db_path, scan_id, 19, "finalise"):
        _finalise_scan(scan_id, db_path, "completed", llm_client)

        # Print summary
        cost = llm_client.total_cost_usd()
        total_elapsed = time.perf_counter() - _pipeline_start
        console.print()
        console.rule("[bold green]Pipeline complete")
        console.print(f"  Scan ID:     [bold]{scan_id}[/]")
        console.print(f"  Code nodes:  {len(all_nodes)}")
        console.print(f"  Domains:     {len(domains)}")
        console.print(f"  Documents:   {len(rollups)}")
        console.print(f"  LLM cost:    [bold]${cost:.4f}[/]")
        console.print(f"  Total time:  [bold]{total_elapsed:.1f}s[/]")
        console.print(f"  Output:      {docs_dir}")

        # Display final checkpoint status
        console.print()
        console.print("[bold cyan]Phase completion summary:[/]")
        _display_checkpoint_status(db_path, scan_id)

        console.print()
        console.print(f"  [dim]Ingest:      discover ingest -p {project_slug} --target dochub[/]")
        console.print()

        # Instructions for resume
        last_complete = get_last_complete_phase(db_path, scan_id)
        if last_complete is not None:
            next_phase = _next_phase(last_complete)
            if next_phase is not None:
                console.print(f"  [dim]To resume from a specific phase: discover scan repo --resume --resume-from={int(next_phase)}[/]")
        console.print()


def _display_checkpoint_status(db_path: Path, scan_id: int) -> None:
    """Display progress of completed phases from checkpoints."""
    checkpoints = get_all_checkpoints(db_path, scan_id)
    if not checkpoints:
        return
    console.print("[bold cyan]Phase progress:[/]")
    for cp in checkpoints:
        phase_num = cp["phase_num"]
        phase_name = cp["phase_name"]
        status = cp["status"]
        symbol = "✓" if status == "complete" else ("✗" if status == "failed" else "⊘")
        color = "green" if status == "complete" else ("red" if status == "failed" else "yellow")
        # SQLite REAL column always returns a float — cast back to int for display
        # since all current phases are whole numbers.
        phase_display = int(phase_num) if phase_num == int(phase_num) else phase_num
        console.print(f"  [{color}]{symbol}[/] Phase {phase_display:>4} ({phase_name})")
        if status == "failed" and cp.get("error_msg"):
            console.print(f"      Error: {cp['error_msg']}")


def _load_code_nodes_from_db(db_path: Path, scan_id: int) -> list:
    """Load all code nodes from the DB for a scan."""
    from .graph.models import CodeNode  # Import here to avoid circular deps
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM code_nodes WHERE scan_id = ? ORDER BY id ASC",
            (scan_id,),
        ).fetchall()
        nodes = []
        for row in rows:
            import json
            node = CodeNode(
                file_path=row["file_path"],
                language=row["language"],
                node_type=row["node_type"],
                name=row["name"],
                qualified_name=row["qualified_name"],
                line_start=row["line_start"],
                line_end=row["line_end"],
                source_code=row["source_code"],
                annotations=json.loads(row["annotations"] or "{}"),
                params=json.loads(row["params"] or "{}"),
                return_type=row["return_type"],
                framework_hints=json.loads(row["framework_hints"] or "{}"),
                domain=row["domain"],
            )
            nodes.append(node)
        return nodes
    finally:
        conn.close()


def _load_domains_from_db(db_path: Path, scan_id: int, all_nodes: list) -> dict:
    """Load all domains from the DB for a scan."""
    from .graph.models import Domain  # Import here
    import json
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM domains WHERE scan_id = ? ORDER BY id ASC",
            (scan_id,),
        ).fetchall()
        domains = {}
        node_map = {n.qualified_name: n for n in all_nodes}
        for row in rows:
            domain_name = row["name"]
            entry_point_names = json.loads(row["entry_points"] or "[]")
            entry_points = [node_map[qn] for qn in entry_point_names if qn in node_map]
            tech_stack = json.loads(row["tech_stack"] or "[]")
            domain = Domain(
                name=domain_name,
                nodes=[n for n in all_nodes if n.domain == domain_name],
                entry_points=entry_points,
                tech_stack=tech_stack,
            )
            domains[domain_name] = domain
        return domains
    finally:
        conn.close()


def _load_summaries_from_db(db_path: Path, scan_id: int) -> dict:
    """Load all node summaries from the DB."""
    import json
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT ns.*, cn.qualified_name FROM node_summaries ns
               JOIN code_nodes cn ON cn.id = ns.node_id
               WHERE cn.scan_id = ?""",
            (scan_id,),
        ).fetchall()
        summaries = {}
        for row in rows:
            summary = {
                "qualified_name": row["qualified_name"],
                "tier": row["tier"],
                "model_used": row["model_used"],
                "purpose": row["purpose"],
                "business_rules": row["business_rules"],
                "io_summary": row["io_summary"],
                "tech_debt_signals": row["tech_debt_signals"],
                "raw_response": row["raw_response"],
                "tokens_in": row["tokens_in"],
                "tokens_out": row["tokens_out"],
            }
            summaries[row["qualified_name"]] = summary
        return summaries
    finally:
        conn.close()


def _load_flows_from_db(db_path: Path, scan_id: int) -> dict:
    """Load all business flows from the DB, grouped by domain."""
    import json
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM business_flows WHERE scan_id = ? ORDER BY domain, id ASC",
            (scan_id,),
        ).fetchall()
        flows_by_domain = {}
        for row in rows:
            domain = row["domain"]
            if domain not in flows_by_domain:
                flows_by_domain[domain] = []
            flow = {
                "flow_type": row["flow_type"],
                "name": row["name"],
                "description": row["description"],
                "node_ids": json.loads(row["node_ids"] or "[]"),
                "model_used": row["model_used"],
            }
            flows_by_domain[domain].append(flow)
        return flows_by_domain
    finally:
        conn.close()


def _compute_domain_adjacency(
    db_path: Path,
    scan_id: int,
    min_edges: int = 3,
) -> dict[str, list[str]]:
    """Return {caller_domain: [callee_domain, ...]} from cross-domain call_edges.

    Only pairs with >= min_edges calls are included to suppress noisy one-off
    references that don't represent real architectural dependencies.
    """
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                cn_caller.domain  AS caller_domain,
                cn_callee.domain  AS callee_domain,
                COUNT(*)          AS edge_count
            FROM call_edges ce
            JOIN code_nodes cn_caller
                ON cn_caller.id = ce.caller_id AND cn_caller.scan_id = ce.scan_id
            JOIN code_nodes cn_callee
                ON cn_callee.id = ce.callee_id AND cn_callee.scan_id = ce.scan_id
            WHERE ce.scan_id = ?
              AND ce.callee_id IS NOT NULL
              AND cn_caller.domain IS NOT NULL
              AND cn_callee.domain IS NOT NULL
              AND cn_caller.domain != cn_callee.domain
            GROUP BY cn_caller.domain, cn_callee.domain
            HAVING COUNT(*) >= ?
            ORDER BY edge_count DESC
            """,
            (scan_id, min_edges),
        ).fetchall()
    finally:
        conn.close()

    adjacency: dict[str, list[str]] = {}
    for row in rows:
        adjacency.setdefault(row["caller_domain"], []).append(row["callee_domain"])
    return adjacency


def _mine_processes(
    scenario_flows: list,
    output_dir: Path,
) -> dict:
    """Stage 10.5: Process mining and conformance analysis (optional).

    Args:
        scenario_flows: List of ScenarioFlow objects with pseudo_event_log
        output_dir: Directory for mining reports

    Returns:
        dict[scenario_id] → MiningResult
    """
    logger.info("Stage 10.5: Process mining and conformance analysis")

    # Extract pseudo event logs from scenario flows
    pseudo_logs = []
    scenario_id_map = {}
    for flow in scenario_flows:
        if hasattr(flow, 'pseudo_event_log') and flow.pseudo_event_log:
            pseudo_logs.append(flow.pseudo_event_log)
            scenario_id_map[flow.pseudo_event_log['case_id']] = flow.scenario_id

    if not pseudo_logs:
        logger.warning("No pseudo event logs available for mining")
        return {}

    # Mine all scenarios
    mining_results = mine_scenarios(pseudo_logs)

    # Save mining reports to disk
    mining_reports_dir = output_dir / "mining_reports"
    mining_reports_dir.mkdir(parents=True, exist_ok=True)

    for scenario_id, result in mining_results.items():
        try:
            MiningReporter.save_reports(result, mining_reports_dir)
        except Exception as e:
            logger.error(f"Failed to save mining reports for {scenario_id}: {e}")

    logger.info(f"Stage 10.5 complete: {len(mining_results)} scenarios mined")
    return mining_results


def _finalise_scan(
    scan_id: int,
    db_path: Path,
    status: str,
    llm_client=None,
) -> None:
    """Mark scan_run as finished and persist LLM costs."""
    if llm_client is not None:
        try:
            llm_client.persist_costs(scan_id, db_path)
        except Exception as exc:
            logger.warning("Failed to persist LLM costs: %s", exc)

    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE scan_runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, now_iso(), scan_id),
        )
        conn.commit()
    finally:
        conn.close()


def _rewrite_docs_from_db(
    scan_id: int,
    db_path: Path,
    docs_dir: Path,
    project_slug: str,
    *,
    repo_url: str = "",
    repo_commit: str = "",
) -> None:
    """Re-materialise markdown from cached rollups without re-running LLM tiers.

    Used when a previous scan reached llm_complete (Tier 3 done, write skipped
    or crashed) or when `--rescan` hits a same-SHA cache. Writes each doc to
    docs_dir/{PREFIX}/{doc_id}.md using content_md stored in generated_docs.
    """
    from .generators.doc_generator import _doc_type_prefix

    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT doc_id, doc_type, domain, confidence, content_md "
            "FROM generated_docs WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        console.print("[yellow]No cached rollups to re-render.[/]")
        return

    written = 0
    for row in rows:
        if not row["content_md"]:
            continue
        prefix_dir = docs_dir / _doc_type_prefix(row["doc_type"])
        prefix_dir.mkdir(parents=True, exist_ok=True)
        (prefix_dir / f"{row['doc_id']}.md").write_text(row["content_md"], encoding="utf-8")
        written += 1

    console.print(f"  Re-rendered [green]{written}[/] markdown file(s) to {docs_dir}")
