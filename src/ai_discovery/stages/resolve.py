"""Stage ``resolve``: DB init, repo resolve, resume/rescan checks, scan-run record.

Moved verbatim from ``pipeline._run_pipeline_unlocked`` steps 1–4 (M1.6).
Early exits that used to ``return`` from the pipeline call ``ctx.halt(result)``.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..core.redact import redact_mapping
    from ..db import get_conn, init_db, now_iso
    from ..pipeline import (
        _PHASE_SPECS, _SKIPPABLE_PHASES, _display_checkpoint_status, _finalise_scan,
        _find_previous_run, _get_start_phase, _parse_phase_spec, _repo_keys,
        _rewrite_docs_from_db, _timed, console,
    )

    repo, config = ctx.repo, ctx.config
    resume, resume_from, rescan = ctx.resume, ctx.resume_from, ctx.rescan
    db_path, docs_dir, output_dir, project_slug = ctx.db_path, ctx.docs_dir, ctx.output_dir, ctx.project_slug

    # ------------------------------------------------------------------
    # 1. Init DB
    # ------------------------------------------------------------------
    init_db(db_path)
    console.print(f"[bold]Pipeline initialised[/]  db={db_path}")

    # ------------------------------------------------------------------
    # 2. Resolve repo (early — needed for accurate branch/SHA matching)
    # ------------------------------------------------------------------
    from ..repo.resolver import resolve_repo

    with _timed("repo resolve"), console.status("[bold cyan]Resolving repository..."):
        resolved = resolve_repo(repo, ctx.branch, output_dir)
    ctx.resolved = resolved
    console.print(
        f"  Repo: [green]{resolved.repo_path}[/]  "
        f"commit=[dim]{resolved.commit_sha[:10]}[/]  branch={resolved.branch}"
    )

    # ------------------------------------------------------------------
    # 3. Resume / rescan checks (uses resolved branch + SHA for accuracy)
    # ------------------------------------------------------------------
    scan_id: int | None = None
    skip_phases = ctx.skip_phases
    # Validate skip_phases up front — an invalid spec silently no-ops inside
    # _should_skip_phase, which is surprising enough to be worth catching at
    # the entry point where the error can reference the offending value.
    for spec in skip_phases:
        try:
            num = _parse_phase_spec(spec)
        except ValueError as e:
            raise ValueError(f"--skip-phase {spec!r}: {e}") from e
        # M0.6: phases 14-19 have no skip gate; reject instead of silently no-op.
        if num not in _SKIPPABLE_PHASES:
            raise ValueError(
                f"--skip-phase {spec!r}: phase {num:g} ({_PHASE_SPECS.get(num, '?')}) cannot be skipped; "
                f"skippable phases: {', '.join(f'{p:g}' for p in sorted(_SKIPPABLE_PHASES))}"
            )
    prev_run = _find_previous_run(
        db_path, _repo_keys(repo, resolved), resolved.branch, commit_sha=resolved.commit_sha,
    )

    # M0.6: never resume a run recorded at a different commit — its checkpoints
    # describe other code. (Falls back to repo+branch match when HEAD moved.)
    if (resume or resume_from) and prev_run and prev_run["commit_sha"] \
            and prev_run["commit_sha"] != resolved.commit_sha:
        console.print(
            f"[red bold]Cannot resume scan #{prev_run['id']}[/]: it was recorded at commit "
            f"{prev_run['commit_sha'][:10]} but HEAD is {resolved.commit_sha[:10]}. "
            "Run without --resume/--resume-from to start a new scan."
        )
        return ctx.halt("resume_refused")

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
            return ctx.halt()
        if prev_run["status"] == "completed":
            console.print("[green]Previous scan already completed -- nothing to resume.[/]")
            return ctx.halt()
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
    ctx.start_phase = start_phase

    # Rescan guard: skip if same commit already scanned (no --rescan flag)
    if not rescan and not resume and not resume_from and prev_run:
        if prev_run["commit_sha"] == resolved.commit_sha and prev_run["status"] == "completed":
            console.print(
                f"[green]Commit {resolved.commit_sha[:10]} already scanned "
                f"(run #{prev_run['id']}). Use --rescan to force.[/]"
            )
            return ctx.halt()

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
        return ctx.halt()

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
                    # M0.8: never persist API keys/tokens with the run record.
                    json.dumps(redact_mapping(asdict(config))),
                ),
            )
            scan_id = cursor.lastrowid
            conn.commit()
        finally:
            conn.close()
        console.print(f"  Scan run [bold]#{scan_id}[/] created")
    ctx.scan_id = scan_id
