"""Document-level confidence from verified facts and claim review (moved from the removed ``ai/rollup.py``, M3.7.b).

AST-verified rows count 1.0 each; reviewed prose claims count verified 1.0 / unverified 0.5 / contradicted 0.0.
Nothing to score means *unverifiable* (0.3), never certain (HIGH-1).
"""

from __future__ import annotations

UNVERIFIABLE_CONFIDENCE = 0.3


def blend_confidence(verified_row_count: int, review_summary: dict) -> float:
    n_ast = max(0, int(verified_row_count or 0))
    verified = int(review_summary.get("verified", 0))
    unverified = int(review_summary.get("unverified", 0))
    contradicted = int(review_summary.get("contradicted", 0))
    total = n_ast + verified + unverified + contradicted
    if total == 0:
        return UNVERIFIABLE_CONFIDENCE
    score = (n_ast * 1.0) + (verified * 1.0) + (unverified * 0.5)
    return round(score / total, 2)
