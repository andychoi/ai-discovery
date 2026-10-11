"""Re-score a hand-graded precision sheet.

``verdict`` is one of ``correct`` / ``wrong`` / ``unknown``. Unknown and
ungraded rows are reported separately and charged to no one (protocol rule 4).
Precision is reported per stratum with a Wilson 95% interval — never pooled
into one headline number across strata.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

from .sampler import VERDICTS


def wilson(correct: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = correct / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3))


def score_sheet(path: Path) -> dict:
    tally: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {v: 0 for v in (*VERDICTS, "ungraded")})
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            verdict = (row.get("verdict") or "").strip().lower()
            if verdict and verdict not in VERDICTS:
                raise ValueError(f"{path.name}: bad verdict {verdict!r} (use {', '.join(VERDICTS)})")
            tally[(row["language"], row["origin"])][verdict or "ungraded"] += 1
    strata = []
    for (lang, origin), t in sorted(tally.items()):
        n = t["correct"] + t["wrong"]
        strata.append({"language": lang, "origin": origin, "graded": n, "correct": t["correct"],
                       "precision": round(t["correct"] / n, 3) if n else None,
                       "ci95": list(wilson(t["correct"], n)) if n else None,
                       "unknown": t["unknown"], "ungraded": t["ungraded"]})
    return {"sheet": path.name, "strata": strata}
