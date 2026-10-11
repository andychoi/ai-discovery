"""`discover scan` — v1 scan (roadmap item M2.3, design/cli-v1.md §3).

* Project = the repo found from ``-C``/cwd; state goes to ``<repo>/.discovery/``
  (or ``--state-dir``). Config comes from the project (``discover init``).
* Spend: ``--prose/--no-prose`` (default: the config's ``prose``; structure only
  when unset). The cost gate (``--yes``) arrives with M2.4.
* Incremental: unchanged commits are reused and Tier-1 summaries are reused per
  file today; stage fingerprints take over with M1.8. ``--full`` forces a rescan.
* ``--only``: until the stage engine (M1.8), it maps to the pipeline phases that
  can be skipped (``_SKIPPABLE_PHASES``) — everything else always runs.
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


def _only_to_skip(only: str) -> list[str]:
    """Translate ``--only a,b`` into the pipeline's skip list (pre-M1.8)."""
    from ...pipeline import _PHASE_NAME_TO_NUM, _PHASE_SPECS, _SKIPPABLE_PHASES

    wanted: set[int] = set()
    for raw in (x.strip() for x in only.split(",") if x.strip()):
        num = _PHASE_NAME_TO_NUM.get(raw) or (int(raw) if raw.isdigit() else None)
        if num not in _SKIPPABLE_PHASES:
            names = ", ".join(_PHASE_SPECS[p] for p in sorted(_SKIPPABLE_PHASES))
            raise DiscoveryError(f"--only {raw!r} is not a selectable stage", "contradictory_flags",
                                 hint=f"choose from: {names}")
        wanted.add(num)
    return [str(p) for p in sorted(_SKIPPABLE_PHASES - wanted)]


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
            "unresolved_edges": q("SELECT COUNT(*) FROM call_edges WHERE scan_id = ? AND resolved_by = 'unresolved'"),
            "domains": q("SELECT COUNT(*) FROM domains WHERE scan_id = ?"),
            "screens": q("SELECT COUNT(*) FROM screen_specs WHERE scan_id = ?"),
            "docs": q("SELECT COUNT(*) FROM generated_docs WHERE scan_id = ?"),
        }
        spent = q("SELECT COALESCE(SUM(est_usd), 0) FROM llm_costs WHERE scan_id = ?")
        out["spend"] = {"usd": round(spent or 0, 4), "budget_usd": cfg.budget_limit_usd}
        enc = q("SELECT COUNT(DISTINCT file_path) FROM code_nodes WHERE scan_id = ? "
                "AND framework_hints LIKE '%source_encoding%'")
    finally:
        conn.close()
    deg = out["degradations"]
    if enc:
        deg.append({"kind": "encoding_fallback", "count": enc,
                    "message": f"{enc} file(s) decoded with a legacy encoding (cp949/euc-kr/...)"})
    if out["counts"]["screens"] == 0 and files:
        deg.append({"kind": "no_screens", "count": 0,
                    "message": "0 screens: no supported menu format matched (screens skipped)"})
    if out["counts"]["unresolved_edges"]:
        n = out["counts"]["unresolved_edges"]
        deg.append({"kind": "unresolved_calls", "count": n,
                    "message": f"{n} call(s) could not be resolved to a definition"})
    return out


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
        yes: bool = typer.Option(False, "--yes", "-y", help="Pre-approve the cost gate (M2.4)."),
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
            cfg.prod = quality == "final"
        skip = _only_to_skip(only) if only else []

        progress = ProgressEmitter(g)
        if g.progress == "json":
            progress.stage("scan", "start", repo=str(layout.repo), prose=use_prose)
        with _pipeline_output(g):
            status = run_pipeline(
                repo=str(layout.repo), branch=branch, project_slug=layout.slug,
                output_dir=layout.state_dir, docs_root=layout.state_dir, config=cfg,
                resume=resume, skip_phases=skip, rescan=full, layout=layout,
            )
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
                              f"({c['unresolved_edges']} unresolved), {c['domains']} domains, "
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
