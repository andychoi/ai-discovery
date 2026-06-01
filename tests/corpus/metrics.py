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
