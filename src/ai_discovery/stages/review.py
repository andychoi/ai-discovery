"""Stage ``review``: phase 17 ``self_review`` (+ prod screen verification), moved verbatim from ``pipeline.py`` (M1.7)."""

from __future__ import annotations

import logging

import time

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from ..pipeline import (
    _budget_ok, _iter_with_deadline, _llm_skipped, _with_checkpoint, console, get_conn, record_phase_complete,
)
from .context import RunContext

# same logger name as before the move, so log output/filters are unchanged
logger = logging.getLogger("ai_discovery.pipeline")


def run(ctx: RunContext) -> None:
    # inputs from earlier stages, under their old pipeline-local names
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    docs_dir = ctx.docs_dir
    rollups = ctx.rollups
    # ------------------------------------------------------------------
    # 17. Self-review (before write so annotations appear in output files)
    # ------------------------------------------------------------------
    if _llm_skipped(config, "Phase 17 (self_review)"):
        record_phase_complete(db_path, scan_id, 17, "self_review", {"skipped": "no_llm"})
    elif not _budget_ok(llm_client, config, "self-review"):
        console.print("[yellow]Skipping self-review due to budget limit.[/]")
        record_phase_complete(db_path, scan_id, 17, "self_review", {"skipped": "budget"})
    elif not rollups:
        console.print("[yellow]Skipping self-review — no rollup documents were generated.[/]")
        record_phase_complete(db_path, scan_id, 17, "self_review", {"skipped": "no_rollups"})
    else:
        with _with_checkpoint(db_path, scan_id, 17, "self_review"):
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
            from ..ai.self_review import review_document, get_review_summary, persist_claims, annotate_document

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

                _review_started: dict[int, float] = {}

                def _review_one(rollup):
                    _review_started[id(rollup)] = time.monotonic()
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
                _timed_out_reviews = 0
                _failed_reviews: list[str] = []
                try:
                    for future, _expired in _iter_with_deadline(
                        futures, lambda f: _review_started.get(id(futures[f])), _PER_DOC_TIMEOUT,
                    ):
                        rollup_submitted = futures[future]
                        try:
                            if _expired:
                                _timed_out_reviews += 1
                                raise _FuturesTimeout()
                            rollup, claims, doc_counts = future.result()
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
                            logger.debug("self-review traceback", exc_info=True)
                            _failed_reviews.append(f"{rollup_submitted.domain}/{rollup_submitted.doc_type}")
                            progress.update(overall, advance=1)
                            console.print(
                                f"  [red]✗[/] [dim]{rollup_submitted.domain}[/] › [bold]{rollup_submitted.doc_type}[/]"
                                f"  [red]error: {exc}[/]"
                            )
                            continue

                        summary = get_review_summary(claims)

                        # Focused re-generation for sections with bad claims
                        if summary["unverified"] + summary["contradicted"] > 0:
                            from ..ai.self_review import regenerate_sections, review_document
                            rollup.content_md = regenerate_sections(
                                rollup.content_md, claims, llm_client, db_path=db_path,
                            )
                            # HIGH-2: the rewrite changes the prose, so the old
                            # verdicts no longer describe what ships. Re-verify the
                            # regenerated content and recompute summary/confidence
                            # from the NEW claims — otherwise the published score
                            # reflects pre-rewrite claims, not the doc on disk.
                            try:
                                claims = review_document(
                                    rollup.content_md, db_path, llm_client,
                                    max_workers=2,
                                )
                                summary = get_review_summary(claims)
                            except Exception as exc:
                                logger.warning(
                                    "Re-verification after regeneration failed for %s/%s: %s",
                                    rollup.domain, rollup.doc_type, exc,
                                )
                                logger.debug("re-verification traceback", exc_info=True)
                                _failed_reviews.append(f"{rollup.domain}/{rollup.doc_type} (re-verify)")

                        rollup.content_md = annotate_document(rollup.content_md, claims)
                        rollup.unverified_claims = summary["unverified"] + summary["contradicted"]
                        # Track 4: replace LLM self-asserted confidence with a
                        # deterministic blend of AST-verified rows + review verdicts.
                        # Verified rows (Track 1 tables) always count as 1.0;
                        # prose claims contribute per their review status.
                        from ..ai.rollup import blend_confidence
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
                    # Exception (M0.6): when a review timed out, its thread may be
                    # stuck forever. _review_one only *reads* (claims are persisted
                    # here in the main loop), so abandoning it cannot race writes.
                    executor.shutdown(wait=not _timed_out_reviews, cancel_futures=True)
                    ctx.degrade("review", "self_review_timeout", _timed_out_reviews)
                    ctx.degrade("review", "self_review_failed", len(_failed_reviews), _failed_reviews[:5])

            console.print(
                f"  Self-review complete — "
                f"[green]{_sr_totals['verified']} verified[/]  "
                f"[red]{_sr_totals['contradicted']} contradicted[/]  "
                f"[yellow]{_sr_totals['unverified']} unverified[/]"
            )

    # CRIT-3: screen specs are generated in Phase 2 (before RAG exists), so
    # they're claim-verified here, where RAG is available. Kept OUTSIDE the
    # rollup-gated self-review block above so a tier-3 rollup failure (which
    # leaves `rollups` empty) doesn't also skip screen verification. Gated on
    # --prod (one review pass per screen).
    if config.prod and _budget_ok(llm_client, config, "Screen verification"):
        from ..ai.screen_spec_generator import verify_screen_specs
        try:
            n_verified = verify_screen_specs(
                db_path, scan_id, llm_client, docs_dir,
                max_workers=max(1, config.max_concurrent or 1),
            )
            if n_verified:
                console.print(f"  Screen specs source-verified: [green]{n_verified}[/]")
        except Exception as exc:
            logger.warning("Screen verification phase failed: %s", exc)
            logger.debug("screen verification traceback", exc_info=True)
            ctx.degrade("review", "screen_verification_failed", 1, [type(exc).__name__])


execute = run
