"""Precision / recall / F1 scoring + actionable diff reports (HIGH-10, Phase 1).

Each dimension is scored by matching the extracted set against the human-labeled
ground-truth set. A miss is reported with the specific item so a failure says
"expected POST /api/orders, not found" rather than just a number.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Score:
    precision: float
    recall: float
    f1: float
    missing: list  # in ground truth, not extracted (false negatives)
    extra: list    # extracted, not in ground truth (false positives)

    def as_dict(self) -> dict:
        return {"precision": round(self.precision, 3), "recall": round(self.recall, 3),
                "f1": round(self.f1, 3)}


def score_sets(extracted: set, expected: set) -> Score:
    """Set-based precision/recall/F1. Both empty -> perfect (nothing to get wrong)."""
    if not expected and not extracted:
        return Score(1.0, 1.0, 1.0, [], [])
    tp = len(extracted & expected)
    precision = tp / len(extracted) if extracted else (1.0 if not expected else 0.0)
    recall = tp / len(expected) if expected else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Score(
        precision, recall, f1,
        missing=sorted(expected - extracted, key=str),
        extra=sorted(extracted - expected, key=str),
    )


def score_entity_fields(extracted: dict[str, set[str]], expected: list[dict]) -> Score:
    """Entities scored on (table present) AND field-set overlap.

    An entity counts as a true positive only if its table is extracted AND its
    field-set F1 >= 0.5 (so a table with the wrong columns isn't a free pass).
    """
    exp_by_table = {e["table"]: set(e["fields"]) for e in expected}
    if not exp_by_table and not extracted:
        return Score(1.0, 1.0, 1.0, [], [])

    tp, missing, extra = 0, [], []
    for table, exp_fields in exp_by_table.items():
        got = extracted.get(table)
        if got is None:
            missing.append(f"{table} (entity absent)")
            continue
        inter = len(got & exp_fields)
        f_prec = inter / len(got) if got else 0.0
        f_rec = inter / len(exp_fields) if exp_fields else 1.0
        f_f1 = (2 * f_prec * f_rec / (f_prec + f_rec)) if (f_prec + f_rec) else 0.0
        if f_f1 >= 0.5:
            tp += 1
        else:
            missing.append(f"{table} (fields {sorted(exp_fields - got)} missing)")
    for table in extracted:
        if table not in exp_by_table:
            extra.append(f"{table} (unexpected entity)")

    precision = tp / len(extracted) if extracted else (1.0 if not exp_by_table else 0.0)
    recall = tp / len(exp_by_table) if exp_by_table else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return Score(precision, recall, f1, missing, extra)


def score_call_edges(edges, expected: list[dict]) -> Score:
    """Recall over a CURATED set of known-important call edges (Phase 2).

    Ground truth labels only the meaningful resolvable chains (controller→service
    etc.), not every edge, so precision over the full edge set is not meaningful —
    the question is *resolution recall*: did the resolver reach the right target
    at >= min_confidence? An edge matches when caller (if a suffix is given) and
    callee end with the labeled suffixes and confidence clears the bar. f1 mirrors
    recall for this dimension.
    """
    if not expected:
        return Score(1.0, 1.0, 1.0, [], [])
    found, missing = 0, []
    for want in expected:
        caller_suffix = want.get("caller_suffix")
        callee_suffix = want["callee_suffix"]
        min_conf = want.get("min_confidence", 0.0)
        hit = any(
            e.callee.endswith(callee_suffix)
            and (caller_suffix is None or e.caller.endswith(caller_suffix))
            and e.confidence >= min_conf
            for e in edges
        )
        if hit:
            found += 1
        else:
            label = f"{caller_suffix or '*'} -> {callee_suffix} (>= {min_conf})"
            missing.append(f"{label} not resolved")
    recall = found / len(expected)
    return Score(recall, recall, recall, missing, [])


def score_forbidden_edges(edges, forbidden: list[dict]) -> Score:
    """Fraction of FORBIDDEN edges that are correctly ABSENT (HIGH-3 gauge).

    A forbidden edge is a known false resolution — e.g. `orderService.process()`
    resolving to `PaymentService.process` because short-name matching ignores the
    receiver's declared type. Score 1.0 = none present (good); 0.0 = all present
    (the fan-out bug). This is the dimension that DI/receiver-type resolution
    (HIGH-3) will move from 0 to 1 — the harness proves the fix.
    """
    if not forbidden:
        return Score(1.0, 1.0, 1.0, [], [])
    present = []
    for bad in forbidden:
        caller_suffix = bad.get("caller_suffix")
        callee_suffix = bad["callee_suffix"]
        hit = any(
            e.callee.endswith(callee_suffix)
            and (caller_suffix is None or e.caller.endswith(caller_suffix))
            for e in edges
        )
        if hit:
            present.append(f"{caller_suffix or '*'} -> {callee_suffix} (false edge present)")
    score = 1.0 - (len(present) / len(forbidden))
    return Score(score, score, score, present, [])
