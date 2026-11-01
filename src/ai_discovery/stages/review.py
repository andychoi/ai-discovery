"""Stage ``review``: phase 17 — screen-spec source verification under ``quality: final`` (rollup review removed, M3.7.b)."""

from __future__ import annotations

import logging

from ..pipeline import _budget_ok, _llm_skipped, console, record_phase_complete
from .context import RunContext

# same logger name as before the move, so log output/filters are unchanged
logger = logging.getLogger("ai_discovery.pipeline")


def run(ctx: RunContext) -> None:
    """Phase 17: source verification of screen specs under ``quality: final`` (CRIT-3).

    Until M3.7.b this stage also claim-reviewed the Tier-3 ASIS/ASD/ASSC rollups; those documents are gone —
    page prose is grounded by ``pages/grounding.py`` (M3.8) instead.
    """
    config = ctx.config
    llm_client = ctx.llm_client
    db_path = ctx.db_path
    scan_id = ctx.scan_id
    docs_dir = ctx.docs_dir
    if _llm_skipped(config, "Phase 17 (self_review)"):
        record_phase_complete(db_path, scan_id, 17, "self_review", {"skipped": "no_llm"})
        return

    # CRIT-3: screen specs are generated in Phase 2 (before RAG exists), so
    # they're claim-verified here, where RAG is available. Kept OUTSIDE the
    # rollup-gated self-review block above so a tier-3 rollup failure (which
    # leaves `rollups` empty) doesn't also skip screen verification. Gated on
    # --prod (one review pass per screen).
    if config.is_final and _budget_ok(llm_client, config, "Screen verification"):
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
