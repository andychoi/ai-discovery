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
from pathlib import Path
from typing import Optional

from rich.console import Console

from .config import DiscoveryConfig
from .core.paths import ProjectLayout
from .db import (
    get_conn, now_iso, record_phase_start, record_phase_complete,
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
# Phases gated by _phase_should_run. 14-19 always run (or are config-driven,
# e.g. 16 via process_mining.enabled), so --skip-phases rejects them (M0.6).
_SKIPPABLE_PHASES = frozenset({2, 5, 6, 7, 8, 9, 10, 11, 12, 13})


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

def _count_tier1_targets(chunks: list, repo_root: Path | None = None) -> int:
    """Number of chunks that Phase 11 (Tier 1) will actually send to the LLM.

    Mirrors the filtering in summarize_chunks: skip structural types and test files.
    """
    from .ai.summarizer import _SKIP_TYPES, _is_test_chunk

    return sum(
        1 for c in chunks
        if c.chunk_type not in _SKIP_TYPES and not _is_test_chunk(c.file_path, repo_root)
    )


def _record_degradation(db_path: Path, scan_id: int, stage: str, kind: str, count: int,
                        sample: Optional[list] = None) -> None:
    """Persist a degradation (v1 ``degradations`` table) so the scan end panel shows it."""
    import sqlite3 as _sqlite3

    conn = get_conn(db_path)
    try:
        conn.execute("INSERT OR REPLACE INTO degradations (scan_id, stage, kind, count, sample_json) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (scan_id, stage, kind, count, json.dumps(sample) if sample else None))
        conn.commit()
    except _sqlite3.OperationalError as exc:  # pre-0002 DB without the table
        logger.warning("could not record degradation %s/%s: %s", stage, kind, exc)
    finally:
        conn.close()


def _build_rag_chunks(chunks: list, config: DiscoveryConfig, repo_root: Path | None = None) -> list:
    """Build the RAG chunk set, honoring config.rag filters."""
    from .ai.chunker import chunk_for_rag
    from .ai.summarizer import _is_test_chunk

    source = chunks
    if config.rag.skip_tests:
        source = [c for c in source if not _is_test_chunk(c.file_path, repo_root)]
    return chunk_for_rag(source, config.rag.chunk_size, config.rag.chunk_overlap)


def _release_node_source(nodes: list) -> int:
    """Free in-memory `source_code` once chunking (phase 9) is complete.

    P0-3 (memory): each in-memory CodeNode carries its full source text, which on
    a 100k-class / 500k-method corpus is the dominant sustained memory term. The
    source is durably persisted in `code_nodes` (phase 7) and the only in-memory
    consumers — the SQL/external extractors and the chunker — have all run by the
    end of phase 9. Releasing it here bounds memory across the LLM-heavy phases
    10–19 (where embeddings and LLM buffers accumulate) and is invisible to every
    downstream `all_nodes` consumer (they read structural fields only). Returns
    the approximate number of bytes freed. Idempotent.
    """
    freed = 0
    for n in nodes:
        sc = getattr(n, "source_code", "")
        if sc:
            freed += len(sc)
            n.source_code = ""
    return freed


def _is_metered(provider: str) -> bool:
    """True for providers that bill per token (M0.5: every paid provider, not
    just Bedrock). Local providers are free — same set the cost tracker rates at $0."""
    from .llm.catalog import _LOCAL_PROVIDERS

    return provider not in _LOCAL_PROVIDERS


def _llm_skipped(config: DiscoveryConfig, phase_label: str) -> bool:
    """True (and says so) when keyless mode disables this LLM step (M0.10)."""
    if getattr(config, "no_llm", False):
        console.print(f"[dim]{phase_label}: skipped (--no-llm)[/]")
        return True
    return False


def _budget_ok(llm_client, config: DiscoveryConfig, phase_label: str) -> bool:
    """Return True if budget still has room; print warning and return False otherwise.

    Local providers (ollama, mlx-*) have no cost — budget checks are skipped.
    For metered providers, budget_limit_usd: 0.0 means 'no LLM' (pipeline stops immediately).
    """
    if not _is_metered(config.provider):
        return True
    cost = llm_client.total_cost_usd()
    if cost >= config.budget_limit_usd:
        console.print(
            f"[red bold]Budget limit reached[/] (${cost:.2f} >= ${config.budget_limit_usd:.2f}) "
            f"before {phase_label} -- stopping pipeline."
        )
        return False
    return True


def _budget_exhausted_fn(llm_client, config: DiscoveryConfig):
    """P1-e: a cheap, side-effect-free predicate for *mid-phase* budget checks.

    `_budget_ok` gates phase entry but prints and is coarse; within a phase the
    tier1/tier2/tier3 loops can spend far past the limit before the next boundary.
    This returns a callable () -> bool (True once spend hits the limit) for those
    loops, or None when there's nothing to enforce (non-bedrock, or no limit set)
    so the loops skip the check entirely with zero overhead.
    """
    if not _is_metered(config.provider) or not config.budget_limit_usd:
        return None
    limit = config.budget_limit_usd
    return lambda: llm_client.total_cost_usd() >= limit


def _repo_keys(repo: str, resolved=None) -> list[str]:
    """Every string a previous scan_run may have stored for this repo (M0.6).

    The raw CLI argument differs between `./repo`, `repo` and an absolute path,
    so match on the argument, its absolute/resolved forms, and the resolver's
    canonical path and URL.
    """
    keys = {repo}
    if not _looks_like_url(repo):
        p = Path(repo).expanduser()
        keys.update({str(p.absolute()), str(p.resolve())})
    if resolved is not None:
        keys.add(str(resolved.repo_path))
        keys.add(str(Path(resolved.repo_path).resolve()))
        if getattr(resolved, "url", None):
            keys.add(resolved.url)
    return sorted(k for k in keys if k)


def _looks_like_url(repo: str) -> bool:
    return "://" in repo or repo.startswith("git@")


def _iter_with_deadline(futures, started_at, timeout: float, poll: float = 1.0):
    """Yield (future, expired) as futures finish or exceed *timeout* seconds of
    run time (M0.6). `as_completed` + `result(timeout)` never times out because
    it only yields finished futures. *started_at(future)* returns the monotonic
    start time, or None while the future is still queued (queue time is free).
    """
    from concurrent.futures import FIRST_COMPLETED, wait

    pending = set(futures)
    while pending:
        done, _ = wait(pending, timeout=poll, return_when=FIRST_COMPLETED)
        for fut in done:
            pending.discard(fut)
            yield fut, False
        now = time.monotonic()
        for fut in list(pending):
            t0 = started_at(fut)
            if t0 is not None and now - t0 >= timeout:
                pending.discard(fut)
                fut.cancel()
                yield fut, True


def _find_previous_run(db_path: Path, repo, branch: str, commit_sha: str | None = None):
    """Find the most recent scan_run for the same repo.

    *repo* is one key or a list of equivalent keys (see `_repo_keys`).
    Prefers an exact commit_sha match (repo-independent of branch name), then
    falls back to repo+branch. This handles repos whose default branch is
    'master' while the CLI default is 'main'.
    """
    keys = [repo] if isinstance(repo, str) else list(repo)
    marks = ",".join("?" * len(keys))
    repo_clause = f"(repo_url IN ({marks}) OR repo_path IN ({marks}))"
    conn = get_conn(db_path)
    try:
        if commit_sha:
            row = conn.execute(
                f"SELECT * FROM scan_runs WHERE {repo_clause} "
                "AND commit_sha = ? ORDER BY id DESC LIMIT 1",
                (*keys, *keys, commit_sha),
            ).fetchone()
            if row:
                return row
        row = conn.execute(
            f"SELECT * FROM scan_runs WHERE {repo_clause} AND branch = ? "
            "ORDER BY id DESC LIMIT 1",
            (*keys, *keys, branch),
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
    layout: "ProjectLayout | None" = None,
    only: list[str] | None = None,
) -> str | None:
    """Run the pipeline under the project's single-flight run lock (M1.4).

    Raises ``core.lock.LockHeld`` (CLI: exit 4, reason ``lock_held``) when
    another live scan of the same project is running. See
    ``_run_pipeline_unlocked`` for behaviour and return values.
    """
    from .core.lock import RunLock

    # M2.3: v1 scans pass a ProjectLayout (`<repo>/.discovery/`); legacy callers
    # still get the output-<slug> layout from output_dir/docs_root.
    layout = (layout or ProjectLayout.legacy(output_dir, project_slug, docs_root)).ensure()
    with RunLock(layout.lock_path, purpose="scan"):
        return _run_pipeline_unlocked(
            repo=repo, branch=branch, project_slug=project_slug, output_dir=output_dir,
            config=config, docs_root=docs_root, resume=resume, resume_from=resume_from,
            skip_phases=skip_phases, rescan=rescan, layout=layout, only=only,
        )


def _run_pipeline_unlocked(
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
    layout: "ProjectLayout | None" = None,
    only: list[str] | None = None,
) -> str | None:
    """Run the full discovery pipeline in offline mode.

    Returns the final scan status ("completed" or "budget_exceeded"); None for
    early exits that did no work (already scanned, no code nodes).

    Intermediate state (SQLite DB, repo clone, cache) lives under
    output_dir/{project_slug}/. Final markdown is written to
    docs_root/{project_slug}/{PREFIX}/{doc_id}.md. Delivery to DocHub/Gitea
    is a separate step — see `discover push` (page export).

    Args:
        resume: Continue from last complete phase
        resume_from: Start from a specific phase (phase number or name, e.g. "14", "self_review")
        skip_phases: List of phases to skip (e.g. ["16", "10"])
        only: Run only these stages (stage names, e.g. ["parse", "graph"]); other
            reloadable stages reuse stored output (``stages/runner.py``)
    """

    # M1.2: every project path comes from core.paths.ProjectLayout (ADR-0001).
    # The current CLI still uses the legacy `-o/--docs-root/-p` layout; the v1
    # CLI (M2) switches scans to ProjectLayout.for_repo (`<repo>/.discovery/`).
    layout = (layout or ProjectLayout.legacy(output_dir, project_slug, docs_root)).ensure()
    project_slug = layout.slug
    output_dir = layout.state_dir
    db_path = layout.db_path
    _pipeline_start = time.perf_counter()

    # ADR-0003 (M1.6–M1.8): every step lives in `stages/`; StageRunner runs them
    # in order with fingerprints and `--only`. Phase numbers remain only as
    # checkpoint-row aliases.
    from .ai.llm_client import LLMClient
    from .stages import resolve
    from .stages.context import RunContext
    from .stages.runner import StageRunner, normalize_only

    ctx = RunContext(repo=repo, branch=branch, config=config, layout=layout, resume=resume,
                     resume_from=resume_from, rescan=rescan, skip_phases=skip_phases or [],
                     pipeline_start=_pipeline_start,
                     only=normalize_only(only) if only else None)
    resolve.run(ctx)                      # steps 1–4: DB init, repo, resume/rescan checks, scan row
    if ctx.halted:
        return ctx.result

    # LLM client is constructed up-front (cheap — it only wraps config) so every
    # phase that needs it can use it. CRIT-1: it was previously built just before
    # Phase 10, which made Phase 2 (screen specs) raise UnboundLocalError on any
    # repo with a detectable menu. Keep this binding ahead of the screens stage.
    ctx.llm_client = LLMClient(config)
    # M1.11.b: repeated LLM calls are answered from the response cache ($0);
    # --full re-asks but still records. Test doubles may not support it.
    if not getattr(config, "no_llm", False) and hasattr(ctx.llm_client, "attach_cache"):
        ctx.llm_client.attach_cache(layout.cache_dir / "llm_cache.db", read=not rescan)

    # screens → walk → parse → graph → slices → sql → entities → embed → summaries
    # → flows → visuals → review → finalise (stages/runner.py; pages are built by the post-scan hook)
    return StageRunner(ctx).run()


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


def _stamp_file_hash(nodes: list, file_path) -> None:
    """Stamp SHA256(file bytes) on every node parsed from *file_path* (A-2).

    The hash is the cross-scan identity for "this file is unchanged" — it
    gates Tier-1 summary reuse on re-scans. Unreadable files leave the hash
    blank, which excludes their nodes from reuse (never a false match).
    """
    import hashlib

    try:
        file_hash = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    except OSError:
        return
    for node in nodes:
        node.file_hash = file_hash


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
            hints = json.loads(row["framework_hints"] or "{}")
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
                framework_hints=hints,
                domain=row["domain"],
                file_hash=row["file_hash"] or "",
                bases=list(hints.get("bases") or []),   # stored with the hints since M3.4
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
    """Phase 16: Per-scenario flow statistics (optional).

    Args:
        scenario_flows: List of ScenarioFlow objects with pseudo_event_log
        output_dir: Directory for mining reports

    Returns:
        dict[scenario_id] → MiningResult
    """
    logger.info("Phase 16: Flow statistics")

    # Extract pseudo event logs from scenario flows
    pseudo_logs = []
    scenario_id_map = {}
    for flow in scenario_flows:
        if hasattr(flow, 'pseudo_event_log') and flow.pseudo_event_log:
            pseudo_logs.append(flow.pseudo_event_log)
            scenario_id_map[flow.pseudo_event_log['case_id']] = flow.scenario_id

    if not pseudo_logs:
        logger.warning("No pseudo event logs available for flow statistics")
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

    logger.info(f"Phase 16 complete: {len(mining_results)} scenarios analysed")
    return mining_results


def _finalise_scan(
    scan_id: int,
    db_path: Path,
    status: str,
    llm_client=None,
    keep_snapshots: int = 3,
) -> None:
    """Mark scan_run as finished and persist LLM costs.

    M1.5: a ``completed`` scan becomes ``current_scan`` (what readers see) in
    the same transaction, then old snapshots beyond *keep_snapshots* are
    garbage-collected. Budget-stopped or failed scans are never flipped.
    """
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
        if status == "completed":
            from .store.scans import gc_snapshots, set_current_scan

            set_current_scan(conn, scan_id)
            gc_snapshots(conn, keep=keep_snapshots)
        conn.commit()
    finally:
        conn.close()
