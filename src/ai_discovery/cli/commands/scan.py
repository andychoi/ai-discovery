"""`discover scan` — v1 scan (roadmap item M2.3, design/cli-v1.md §3).

* Project = the repo found from ``-C``/cwd; state goes to ``<repo>/.discovery/``
  (or ``--state-dir``). Config comes from the project (``discover init``).
* Spend: ``--prose/--no-prose`` (default: the config's ``prose``; structure only
  when unset). A prose run passes the cost gate first (``cli/cost_gate.py``, M2.4):
  quoted from the stored scan of HEAD, or from a structure pass run first; ``--yes``
  pre-approves; ``--dry-run`` prints the per-stage estimate and writes nothing.
* Incremental: unchanged commits are reused and Tier-1 summaries are reused per
  file today; stage fingerprints take over with M1.8. ``--full`` forces a rescan.
* ``--only``: stage names (``stages/runner.py``); the other reloadable stages reuse
  their stored output, cheap ungated stages always run.
* After a completed scan: post-scan hooks (``core.hooks``), then an end panel
  with counts, degradations and at most two "What's next" commands.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from typing import Any, Optional

import typer

from ...core.errors import DiscoveryError
from ..context import Globals, get_globals
from ..output import ProgressEmitter, emit, handle_errors

QUALITY = ("draft", "final")


def _only_stages(only: str) -> list[str]:
    """Validate ``--only a,b`` into canonical stage names (StageRunner, M1.8).

    Legacy phase names/numbers (``domain_classify``, ``6``) are accepted as aliases.
    """
    from ...stages.runner import normalize_only, selectable

    try:
        return sorted(normalize_only(only.split(",")))
    except ValueError as exc:
        raise DiscoveryError(f"--only: {exc}", "contradictory_flags",
                             hint=f"choose from: {', '.join(selectable())}") from exc


@contextlib.contextmanager
def _pipeline_output(g: Globals):
    """Keep stdout clean for --json / --progress json; silence on -q / --progress none."""
    if g.progress == "none" or g.quiet:
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            yield
    elif g.json or g.progress == "json":
        with contextlib.redirect_stdout(sys.stderr):
            yield
    else:
        yield


def _summary(layout, scan_id: Optional[int], cfg, status: str) -> dict[str, Any]:
    """Counts, spend and degradations for the end panel (from the scan's DB rows)."""
    out: dict[str, Any] = {"status": status, "scan_id": scan_id, "db": str(layout.db_path),
                           "counts": {}, "spend": {}, "degradations": []}
    if scan_id is None or not layout.db_path.exists():
        return out
    conn = sqlite3.connect(f"file:{layout.db_path}?mode=ro", uri=True)
    try:
        q = lambda sql: conn.execute(sql, (scan_id,)).fetchone()[0]  # noqa: E731
        files = q("SELECT COUNT(DISTINCT file_path) FROM code_nodes WHERE scan_id = ?")
        out["counts"] = {
            "files": files,
            "symbols": q("SELECT COUNT(*) FROM code_nodes WHERE scan_id = ?"),
            "edges": q("SELECT COUNT(*) FROM call_edges WHERE scan_id = ?"),
            "unresolved_calls": _unresolved_calls(conn, scan_id),
            "domains": q("SELECT COUNT(*) FROM domains WHERE scan_id = ?"),
            "screens": q("SELECT COUNT(*) FROM screen_specs WHERE scan_id = ?"),
            "docs": q("SELECT COUNT(*) FROM generated_docs WHERE scan_id = ?"),
        }
        spent = q("SELECT COALESCE(SUM(est_usd), 0) FROM llm_costs WHERE scan_id = ?")
        out["spend"] = {"usd": round(spent or 0, 4), "budget_usd": cfg.budget_limit_usd}
        enc = q("SELECT COUNT(DISTINCT file_path) FROM code_nodes WHERE scan_id = ? "
                "AND framework_hints LIKE '%source_encoding%'")
        try:  # degradations recorded by pipeline stages (v1 table, migration 0002)
            stage_deg = conn.execute("SELECT stage, kind, count FROM degradations WHERE scan_id = ? "
                                     "ORDER BY stage, kind", (scan_id,)).fetchall()
        except sqlite3.OperationalError:
            stage_deg = []
    finally:
        conn.close()
    deg = out["degradations"]
    if enc:
        deg.append({"kind": "encoding_fallback", "count": enc,
                    "message": f"{enc} file(s) decoded with a legacy encoding (cp949/euc-kr/...)"})
    if out["counts"]["screens"] == 0 and files:
        deg.append({"kind": "no_screens", "count": 0,
                    "message": "0 screens: no supported menu format matched (screens skipped)"})
    messages = {"tier1_no_targets": "Tier 1 summarized nothing: all {n} chunks were filtered as tests/structural",
                "tier1_failed": "{n} Tier-1 summaries failed (not stored; the next scan retries them)",
                "parser_error": "{n} file(s) could not be parsed",
                "llm_truncated": "{n} LLM answer(s) hit max_tokens (treated as truncated)",
                "flow_inference_failed": "{n} scenario flow(s) could not be inferred",
                "self_review_failed": "{n} document review(s) failed",
                "self_review_timeout": "{n} document review(s) timed out"}
    seen = {d["kind"] for d in deg}
    for stage, kind, n in stage_deg:
        if kind in seen:          # e.g. encoding_fallback is also derived from code_nodes above
            continue
        deg.append({"kind": kind, "count": n, "stage": stage,
                    "message": messages.get(kind, f"{stage}: {kind} ({{n}})").format(n=n)})
    if out["counts"]["unresolved_calls"]:
        n = out["counts"]["unresolved_calls"]
        deg.append({"kind": "unresolved_calls", "count": n,
                    "message": f"{n} call(s) could not be resolved to a definition"})
    return out


def _unresolved_calls(conn: sqlite3.Connection, scan_id: int) -> int:
    """Calls the resolver refused to bind (ADR-0004: rows, never edges)."""
    try:
        return conn.execute("SELECT COUNT(*) FROM unresolved_calls WHERE scan_id = ?", (scan_id,)).fetchone()[0]
    except sqlite3.OperationalError:   # store older than migration 0002
        return 0


def _latest_scan(db_path) -> Optional[int]:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None


def _dry_run(g: Globals, layout, cfg, branch: str, use_prose: bool, run) -> None:
    """``--dry-run``: quote the prose cost without touching the project (M2.4)."""
    from .. import cost_gate

    quote = cost_gate.quote_from_index(layout, cfg, branch) \
        or cost_gate.quote_in_temp(layout, cfg, branch, run, _latest_scan)
    data: dict[str, Any] = {"dry_run": True, "prose": use_prose, "estimate": quote.as_dict() if quote else None}
    if quote is not None:
        data["gate"] = cost_gate.verdict(quote.estimate.expected_usd, cfg)

    def human(console) -> None:
        cost_gate.render(console, quote, cfg)
        if not use_prose:
            console.print("  [dim]prose is off for this project: `discover scan --prose` would spend this[/]")

    emit(g, data, human)


def _gate(g: Globals, layout, cfg, branch: str, yes: bool, resume: bool, run, *, reruns: bool):
    """Quote, then decide (M2.4). Raises cost_gate_no_tty / cost_gate_declined; returns (quote, decision)."""
    from rich.console import Console

    from .. import cost_gate

    quote = cost_gate.quote_from_index(layout, cfg, branch)
    if quote is not None and not reruns and cost_gate.completed_with_prose(layout.db_path, quote.scan_id):
        return None, None                    # HEAD already has its prose: the scan reports "unchanged", no spend
    if quote is None and yes:
        return None, "pre_approved"          # quoted after the run from its own graph (no extra pass)
    if quote is None and not resume:
        quote = cost_gate.quote_from_structure_scan(layout, cfg, branch, run, _latest_scan)
    can_ask = cost_gate.interactive() and not g.json and g.progress != "json"
    if quote is not None and not yes and can_ask \
            and cost_gate.verdict(quote.estimate.expected_usd, cfg) != cost_gate.RUN:
        cost_gate.render(Console(stderr=True), quote, cfg)
    try:
        decision = cost_gate.decide(quote, cfg, yes=yes, can_ask=can_ask)
    except DiscoveryError as exc:
        if exc.reason == "cost_gate_declined":
            cost_gate.record(layout.db_path, quote, "declined", None)
        raise
    return quote, decision


def _record(layout, cfg, branch: str, quote, decision: Optional[str]) -> None:
    """Estimate vs actual for the run that just ended (a --yes run without a stored quote is quoted now)."""
    from .. import cost_gate

    scan_id = _latest_scan(layout.db_path)
    if quote is None and scan_id is not None:
        try:
            from ...llm.estimate import estimate, shape_from_db

            shape = shape_from_db(layout.db_path, scan_id)
            quote = cost_gate.Quote(estimate(cost_gate._prose(cfg), shape), shape, "post_run", scan_id)
        except Exception:   # noqa: BLE001 — calibration must never fail a scan
            return
    try:
        cost_gate.record(layout.db_path, quote, decision or "pre_approved", scan_id)
    except Exception:   # noqa: BLE001
        pass


def _whats_next(app: typer.Typer, prose: bool, failed_hooks: int) -> list[str]:
    names = {(c.name or c.callback.__name__) for c in app.registered_commands}
    names |= {g.name for g in app.registered_groups if getattr(g, "name", None)}
    candidates: list[str] = []
    if failed_hooks:  # design/cli-v1.md: serve is suggested unless something failed
        candidates.append("discover doctor")
    elif "serve" in names:
        candidates.append("discover serve")
    if "mcp" in names:
        candidates.append("discover mcp install")
    if not prose:
        candidates.append("discover scan --prose")
    candidates.append("discover status")
    return candidates[:2]


def register(app: typer.Typer) -> None:
    @app.command("scan")
    @handle_errors
    def scan(
        ctx: typer.Context,
        full: bool = typer.Option(False, "--full", help="Ignore caches and rescan even an unchanged commit."),
        prose: Optional[bool] = typer.Option(None, "--prose/--no-prose", help="Enable LLM stages (default: config 'prose')."),
        only: Optional[str] = typer.Option(None, "--only", help="Run only these stages (comma-separated)."),
        resume: bool = typer.Option(False, "--resume", help="Continue the interrupted run for this commit."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Pre-approve the cost gate."),
        dry_run: bool = typer.Option(False, "--dry-run",
                                     help="Print the prose cost estimate per stage; write nothing, take no lock."),
        budget: Optional[float] = typer.Option(None, "--budget", help="Budget limit in USD (overrides config)."),
        quality: Optional[str] = typer.Option(None, "--quality", help="draft | final (final = deeper model for docs)."),
        branch: str = typer.Option("main", "--branch", "-b", help="Branch label for git repos."),
    ) -> None:
        """Scan this repository into <repo>/.discovery/ (structure only unless --prose)."""
        from ...config.loader import load_config
        from ...core.hooks import ScanContext, run_post_scan
        from ...pipeline import run_pipeline
        from ...store.scans import get_current_scan

        g = get_globals(ctx)
        if quality is not None and quality not in QUALITY:
            raise DiscoveryError(f"--quality must be one of {', '.join(QUALITY)}", "contradictory_flags")
        layout = g.layout()
        if layout.repo is None or not layout.repo.is_dir():
            raise DiscoveryError(f"{g.repo} is not a directory", "project_not_found")

        cfg = load_config(cwd=layout.repo)
        use_prose = cfg.prose if prose is None else prose
        cfg.no_llm = not use_prose
        if budget is not None:
            cfg.budget_limit_usd = budget
        if quality is not None:
            cfg.quality = quality
        only_stages = _only_stages(only) if only else None
        if dry_run and (resume or only):
            raise DiscoveryError("--dry-run cannot be combined with --resume or --only", "contradictory_flags")

        def run(*, layout, config, rescan_=full, resume_=False, only_=None):
            with _pipeline_output(g):
                return run_pipeline(
                    repo=str(layout.repo), branch=branch, project_slug=layout.slug,
                    output_dir=layout.state_dir, docs_root=layout.state_dir, config=config,
                    resume=resume_, rescan=rescan_, layout=layout, only=only_,
                )

        if dry_run:
            return _dry_run(g, layout, cfg, branch, use_prose, run)

        quote, decision = None, None
        if use_prose:
            quote, decision = _gate(g, layout, cfg, branch, yes, resume, run,
                                    reruns=bool(full or only_stages or resume))

        progress = ProgressEmitter(g)
        if g.progress == "json":
            progress.stage("scan", "start", repo=str(layout.repo), prose=use_prose)
        status = run(layout=layout, config=cfg, resume_=resume, only_=only_stages)
        if use_prose and decision is not None:
            _record(layout, cfg, branch, quote, decision)
        if status == "budget_exceeded":
            if g.progress == "json":
                progress.error("budget exceeded")
            raise DiscoveryError(f"budget limit ${cfg.budget_limit_usd:.2f} reached; LLM output is partial",
                                 "budget_exceeded", hint="raise --budget and re-run with --resume")
        if status == "resume_refused":
            raise DiscoveryError("cannot resume: HEAD moved since the recorded run", "resume_refused",
                                 hint="run `discover scan` without --resume")

        scan_id = None
        if layout.db_path.exists():
            conn = sqlite3.connect(layout.db_path)
            try:
                scan_id = get_current_scan(conn)
            finally:
                conn.close()
        outcome = "unchanged" if status is None else status
        hooks = run_post_scan(ScanContext(layout, cfg, scan_id, outcome)) if scan_id else []
        summary = _summary(layout, scan_id, cfg, outcome)
        for h in hooks:
            if not h.ok:
                summary["degradations"].append({"kind": "post_scan_hook", "count": 1,
                                                "message": f"post-scan step {h.name} failed: {h.detail}"})
        summary["hooks"] = [h.__dict__ for h in hooks]
        summary["next"] = _whats_next(app, use_prose, sum(not h.ok for h in hooks))
        summary["prose"] = use_prose
        if quote is not None:
            summary["cost_gate"] = {"decision": decision, "expected_usd": round(quote.estimate.expected_usd, 4),
                                    "source": quote.source}
        if g.progress == "json":  # the human end panel already lists these
            for d in summary["degradations"]:
                progress.warn(d["message"], kind=d["kind"], count=d["count"])
            progress.done(outcome)

        def human(console) -> None:
            c = summary["counts"]
            title = "already up to date" if outcome == "unchanged" else outcome
            console.print(f"\n[bold green]scan {title}[/]  [dim]{summary['db']}[/]", highlight=False)
            if c:
                console.print(f"  {c['files']} files, {c['symbols']} symbols, {c['edges']} edges "
                              f"({c['unresolved_calls']} unresolved calls), {c['domains']} domains, "
                              f"{c['screens']} screens, {c['docs']} docs", highlight=False)
                sp = summary["spend"]
                mode = "prose" if use_prose else "structure only"
                console.print(f"  spend ${sp['usd']:.2f} of ${sp['budget_usd']:.2f} ({mode})", highlight=False)
            for d in summary["degradations"]:
                console.print(f"  [yellow]![/] {d['message']}", highlight=False)
            if summary["next"]:
                console.print("What's next:")
                for cmd in summary["next"]:
                    console.print(f"  [bold]{cmd}[/]")
        emit(g, summary, human)
