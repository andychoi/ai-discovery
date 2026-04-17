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
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .config import DiscoveryConfig
from .db import init_db, get_conn, now_iso

logger = logging.getLogger(__name__)
console = Console()


@contextmanager
def _timed(label: str):
    """Context manager that prints elapsed time for a pipeline phase."""
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    console.print(f"  [dim]⏱  {label}: {elapsed:.1f}s[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    rescan: bool = False,
) -> None:
    """Run the full discovery pipeline in offline mode.

    Intermediate state (SQLite DB, repo clone, cache) lives under
    output_dir/{project_slug}/. Final markdown is written to
    docs_root/{project_slug}/{PREFIX}/{doc_id}.md. Delivery to DocHub/Gitea
    is a separate step — see `discover ingest`.
    """

    output_dir = Path(output_dir) / project_slug
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = Path(docs_root) / project_slug
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

    # Rescan guard: skip if same commit already scanned (no --rescan flag)
    if not rescan and not resume and prev_run:
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

    # ------------------------------------------------------------------
    # 5. Detect languages
    # ------------------------------------------------------------------
    from .repo.lang_detector import detect_languages

    with _timed("lang detect"), console.status("[bold cyan]Detecting languages..."):
        lang_stats = detect_languages(resolved.repo_path)
    lang_names = ", ".join(sorted(lang_stats.keys())) or "(none)"
    console.print(f"  Languages detected: [green]{lang_names}[/]")

    # ------------------------------------------------------------------
    # 6. Parse files
    # ------------------------------------------------------------------
    from .repo.file_walker import walk_repo
    from .parsers.python_parser import PythonParser
    from .parsers.csharp import CSharpParser
    from .parsers.java import JavaParser
    from .parsers.javascript import JavaScriptParser

    parsers = [PythonParser(), CSharpParser(), JavaParser(), JavaScriptParser()]
    all_nodes = []
    parse_errors = 0

    with _timed("parse"), Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]Parsing files..."),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        files = list(walk_repo(resolved.repo_path, set(lang_stats.keys())))
        task = progress.add_task("Parsing", total=len(files))
        for file_path in files:
            for p in parsers:
                if p.can_parse(file_path):
                    try:
                        nodes = p.parse_file(file_path)
                        all_nodes.extend(nodes)
                    except Exception as exc:
                        logger.warning("Parse error: %s: %s", file_path, exc)
                        parse_errors += 1
                    break
            progress.advance(task)

    console.print(
        f"  Parsed [green]{len(all_nodes)}[/] code nodes from "
        f"[green]{len(files)}[/] files"
        + (f"  ([yellow]{parse_errors} errors[/])" if parse_errors else "")
    )

    if not all_nodes:
        console.print("[yellow]No code nodes found -- nothing to analyse.[/]")
        _finalise_scan(scan_id, db_path, "completed")
        return

    # ------------------------------------------------------------------
    # 7. Classify domains, then persist nodes, then build graphs
    # ------------------------------------------------------------------
    from .graph.call_graph import build_call_graph, ExecutionSliceBuilder
    from .graph.domain_classifier import classify_domains

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

    # 8.5 NEW: Build Execution Slices
    with _timed("execution slices"), console.status("[bold cyan]Building execution slices..."):
        slice_builder = ExecutionSliceBuilder(all_nodes, edges)
        scenarios = slice_builder.build_all_scenarios()
    console.print(f"  Execution slices: [green]{len(scenarios)}[/] scenarios identified")

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

    # ------------------------------------------------------------------
    # 9. Smart chunk
    # ------------------------------------------------------------------
    from .ai.chunker import chunk_code_nodes, chunk_for_rag

    with _timed("chunk"), console.status("[bold cyan]Chunking code nodes..."):
        chunks = chunk_code_nodes(all_nodes)
        rag_chunks = chunk_for_rag(chunks, config.rag.chunk_size, config.rag.chunk_overlap)
    console.print(
        f"  Chunks: [green]{len(chunks)}[/] LLM chunks, "
        f"[green]{len(rag_chunks)}[/] RAG chunks"
    )

    # ------------------------------------------------------------------
    # 10. Embed for RAG
    # ------------------------------------------------------------------
    from .ai.llm_client import LLMClient
    from .rag.embedder import embed_chunks

    llm_client = LLMClient(config)

    # Pre-warm small models that stay resident for the whole run.
    # (No-op on Bedrock; on Ollama this pins them with keep_alive=2h so they
    # survive the 5-minute default unload timer across phase transitions.)
    if config.provider in ("ollama", "mlx-gemma", "mlx-qwen"):
        from app.shared.model_defaults import MODELS
        emb_model = MODELS.get(config.provider, {}).get("embedding", config.rag.ollama_model)
        console.print(f"[dim]Warming embedding model ({emb_model})...[/]")
        llm_client.warm_embedding("2h")
        tier1_model = config.get_model("tier1")
        console.print(f"[dim]Warming tier1 ({tier1_model})...[/]")
        llm_client.warm("tier1", "2h")

    with _timed("RAG embed"), console.status("[bold cyan]Embedding chunks for RAG..."):
        try:
            embed_result = embed_chunks(rag_chunks, db_path, llm_client)
            if embed_result.get("resumed"):
                console.print(
                    f"  RAG embeddings: [green]{embed_result['embedded']}[/] vectors "
                    f"[dim](resumed — skipped re-embedding)[/]"
                )
            else:
                console.print(
                    f"  RAG embeddings: [green]{embed_result['embedded']}[/] vectors "
                    f"(dim={embed_result['dim']})"
                )
        except Exception as exc:
            logger.warning("RAG embedding failed (continuing without RAG): %s", exc)
            console.print(f"  [yellow]RAG embedding skipped:[/] {exc}")

    # ------------------------------------------------------------------
    # 11. Tier 1: Summarize
    # ------------------------------------------------------------------
    if not _budget_ok(llm_client, config, "Tier 1 summarization"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return

    from .ai.summarizer import summarize_chunks, persist_summaries

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
        )

    persist_summaries(summaries, scan_id, db_path)
    summaries_dict = {s["qualified_name"]: s for s in summaries}
    console.print(f"  Summaries: [green]{len(summaries)}[/] (cost so far: ${llm_client.total_cost_usd():.4f})")

    # ------------------------------------------------------------------
    # 12. Tier 2: Flow analysis
    # ------------------------------------------------------------------
    if not _budget_ok(llm_client, config, "Tier 2 flow analysis"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return

    from .ai.flow_analyzer import analyze_all_domains, persist_flows

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

    # 12.5 NEW: Scenario Flow Inference
    from .ai.flow_analyzer import ScenarioFlowInference
    console.print("[bold cyan]Tier 2.5: Reconstructing scenario flows...[/]")
    scenario_flows = []
    flow_inference = ScenarioFlowInference(llm_client)
    
    with _timed("scenario inference"), Progress(
        SpinnerColumn(),
        TextColumn("[bold]Tier 2.5 flows"),
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

    # Free tier2 (~17 GB) before loading tier3 (~20 GB).
    if config.provider == "ollama":
        console.print(f"[dim]Unloading tier2 ({tier2_model})...[/]")
        llm_client.unload("tier2")

    # ------------------------------------------------------------------
    # 13. Tier 3: Doc rollup
    # ------------------------------------------------------------------
    if not _budget_ok(llm_client, config, "Tier 3 doc rollup"):
        _finalise_scan(scan_id, db_path, "budget_exceeded", llm_client)
        return

    from .ai.rollup import generate_all_docs, persist_rollups, DOC_TYPES

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

    # 13.5 NEW: Generate Visual Artifacts (BPMN/Mermaid/PlantUML) and persist
    from .output.bpmn_generator import BPMNGenerator
    from .ai.flow_analyzer import persist_scenario_flows
    bpmn_gen = BPMNGenerator()
    console.print("[bold cyan]Step 13.5: Generating visual artifacts...[/]")

    scenario_artifacts: dict[str, dict] = {}
    for flow in scenario_flows:
        scenario_artifacts[flow.scenario_id] = {
            "mermaid": bpmn_gen.generate_mermaid_sequence(flow),
            "plantuml": bpmn_gen.generate_plantuml(flow),
            "bpmn": bpmn_gen.generate_bpmn_xml(flow),
            "ipo": bpmn_gen.generate_ipo_markdown(flow),
        }
    persist_scenario_flows(scenario_flows, scenario_artifacts, scan_id, db_path)
    console.print(f"  Visual artifacts: [green]{len(scenario_artifacts)}[/] scenarios persisted")

    # Free tier3 (~20 GB) so self-review (tier1) has headroom.
    if config.provider == "ollama":
        console.print(f"[dim]Unloading tier3 ({tier3_model})...[/]")
        llm_client.unload("tier3")

    # Checkpoint: LLM tiers done — resume can skip here on rerun
    _finalise_scan(scan_id, db_path, "llm_complete")

    # ------------------------------------------------------------------
    # 14. Self-review (before write so annotations appear in output files)
    # ------------------------------------------------------------------
    if not _budget_ok(llm_client, config, "self-review"):
        console.print("[yellow]Skipping self-review due to budget limit.[/]")
    elif not rollups:
        console.print("[yellow]Skipping self-review — no rollup documents were generated.[/]")
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed, TimeoutError as _FuturesTimeout
        from .ai.self_review import review_document, get_review_summary, persist_claims, annotate_document
        from rich.progress import MofNCompleteColumn

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

            # Per-doc timeout: 5 min per rollup, hard cap 30 min total
            _REVIEW_TIMEOUT = max(300, len(rollups) * 60)

            executor = ThreadPoolExecutor(max_workers=min(config.max_concurrent, len(rollups)))
            try:
                futures = {executor.submit(_review_one, r): r for r in rollups}
                try:
                    for future in _as_completed(futures, timeout=_REVIEW_TIMEOUT):
                        try:
                            rollup, claims, doc_counts = future.result()
                            summary = get_review_summary(claims)

                            # Focused re-generation for sections with bad claims
                            if summary["unverified"] + summary["contradicted"] > 0:
                                from .ai.self_review import regenerate_sections
                                rollup.content_md = regenerate_sections(
                                    rollup.content_md, claims, llm_client, db_path=db_path,
                                )

                            rollup.content_md = annotate_document(rollup.content_md, claims)
                            rollup.unverified_claims = summary["unverified"] + summary["contradicted"]

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
                                        "UPDATE generated_docs SET unverified_claims = ? WHERE id = ?",
                                        (rollup.unverified_claims, doc_db_id),
                                    )
                                    conn.commit()
                                    persist_claims(claims, doc_db_id, db_path)
                            finally:
                                conn.close()
                        except Exception as exc:
                            rollup = futures[future]
                            logger.warning(
                                "Self-review failed for %s/%s: %s",
                                rollup.domain, rollup.doc_type, exc,
                            )
                            progress.update(overall, advance=1)
                            console.print(
                                f"  [red]✗[/] [dim]{rollup.domain}[/] › [bold]{rollup.doc_type}[/]"
                                f"  [red]error: {exc}[/]"
                            )
                except _FuturesTimeout:
                    console.print("[yellow]  Self-review timed out — abandoning remaining verifications.[/]")
                    for f in futures:
                        f.cancel()
            finally:
                executor.shutdown(wait=False)  # don't block on hung LLM threads

        console.print(
            f"  Self-review complete — "
            f"[green]{_sr_totals['verified']} verified[/]  "
            f"[red]{_sr_totals['contradicted']} contradicted[/]  "
            f"[yellow]{_sr_totals['unverified']} unverified[/]"
        )

    # ------------------------------------------------------------------
    # 15. Render markdown files (after self-review so annotations are included)
    # ------------------------------------------------------------------
    from .output.doc_generator import write_docs

    # Compute cross-domain call graph for Layer 2 graph links
    with _timed("domain adjacency"), console.status("[bold cyan]Computing cross-domain links..."):
        domain_adjacency = _compute_domain_adjacency(db_path, scan_id)
    if domain_adjacency:
        console.print(
            f"  Cross-domain edges: [green]{sum(len(v) for v in domain_adjacency.values())}[/] "
            f"pairs across {len(domain_adjacency)} domains"
        )

    from .output.doc_generator import write_scenario_docs

    with _timed("write markdown"), console.status("[bold cyan]Writing markdown files..."):
        written = write_docs(
            rollups,
            docs_dir,
            project_slug,
            repo_url=resolved.url or str(resolved.repo_path),
            repo_commit=resolved.commit_sha,
            domain_adjacency=domain_adjacency,
        )
        scenario_written = write_scenario_docs(
            scenario_flows,
            scenario_artifacts,
            docs_dir,
            project_slug,
            repo_url=resolved.url or str(resolved.repo_path),
            repo_commit=resolved.commit_sha,
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
    # 16. Finalise (offline-only — no push)
    # ------------------------------------------------------------------
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
    console.print(f"  [dim]Ingest:      discover ingest -p {project_slug} --target dochub[/]")
    console.print()


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
    from .output.doc_generator import _doc_type_prefix

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
