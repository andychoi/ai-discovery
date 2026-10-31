"""Score the agent-loop eval against the pre-registered bands (roadmap item M5.7).

Unit of analysis: the **task**. Each task × arm cell is the mean over its repetitions;
arms are compared on paired per-task differences; uncertainty is a percentile
bootstrap over tasks (seeded). Nothing is pooled across stacks for the verdict
without also being shown per stack.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Pre-registered bands (mirrored in the pre-registration page; a test keeps them in sync).
BANDS = {
    "H1_recall_gain_min": 0.10,          # mean per-task recall(discover) − recall(bare)
    "H2_precision_loss_max": 0.05,       # mean per-task precision(discover) − precision(bare) ≥ −0.05
    "H2_ci_floor": -0.10,                # … with the 95% CI lower bound ≥ −0.10
    "S1_output_token_ratio_max": 1.25,   # median per-task output-token ratio discover / bare
    "S3_tests_ran_min": 0.80,            # share of discover-auto runs that ran tests before finishing
}


def load_runs(runs_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(runs_dir.glob("*.json"))]


def _mean(xs: list) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def cells(runs: list[dict]) -> dict[tuple[str, str], dict]:
    """(task, arm) → mean metrics over repetitions."""
    acc: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        acc[(r["task"], r["arm"])].append(r)
    out = {}
    for key, rs in acc.items():
        out[key] = {
            "n": len(rs), "stack": rs[0].get("stack"),
            "recall": _mean([r["files"]["recall"] for r in rs]),
            "precision": _mean([r["files"]["precision"] for r in rs]),
            "output_tokens": _mean([(r.get("agent") or {}).get("output_tokens") for r in rs]),
            "tool_calls": _mean([(r.get("agent") or {}).get("tool_calls") for r in rs]),
            "cost_usd": _mean([(r.get("agent") or {}).get("cost_usd") for r in rs]),
            "build_pass": _mean([1.0 if r["build"]["status"] == "pass" else 0.0 for r in rs
                                 if r["build"]["status"] in ("pass", "fail")]),
            "test_pass": _mean([1.0 if r["test"]["status"] == "pass" else 0.0 for r in rs
                                if r["test"]["status"] in ("pass", "fail")]),
            "ran_tests": _mean([1.0 if (r.get("agent") or {}).get("ran_tests") else 0.0 for r in rs]),
            "finished": _mean([1.0 if (r.get("agent") or {}).get("finished") else 0.0 for r in rs]),
        }
    return out


def bootstrap_ci(diffs: list[float], *, resamples: int, seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(resamples))
    return round(means[int(0.025 * resamples)], 4), round(means[int(0.975 * resamples) - 1], 4)


def paired(c: dict, metric: str, a: str, b: str) -> list[tuple[str, float]]:
    """Per-task differences b − a where both cells have the metric."""
    tasks = sorted({t for t, _ in c})
    out = []
    for t in tasks:
        x, y = c.get((t, a), {}).get(metric), c.get((t, b), {}).get(metric)
        if x is not None and y is not None:
            out.append((t, y - x))
    return out


def verdict(runs: list[dict], *, resamples: int = 10_000, seed: int = 20261031) -> dict:
    c = cells(runs)
    res: dict = {"tasks": len({t for t, _ in c}), "runs": len(runs), "hypotheses": {}}
    rec = paired(c, "recall", "bare", "discover")
    prec = paired(c, "precision", "bare", "discover")
    if rec:
        d = [x for _, x in rec]
        lo, hi = bootstrap_ci(d, resamples=resamples, seed=seed)
        mean = round(statistics.fmean(d), 4)
        res["hypotheses"]["H1"] = {"mean_diff": mean, "ci95": [lo, hi], "n_tasks": len(d),
                                   "pass": mean >= BANDS["H1_recall_gain_min"] and lo > 0}
    if prec:
        d = [x for _, x in prec]
        lo, hi = bootstrap_ci(d, resamples=resamples, seed=seed + 1)
        mean = round(statistics.fmean(d), 4)
        res["hypotheses"]["H2"] = {"mean_diff": mean, "ci95": [lo, hi], "n_tasks": len(d),
                                   "pass": mean >= -BANDS["H2_precision_loss_max"] and lo >= BANDS["H2_ci_floor"]}
    ratios = [c[(t, "discover")]["output_tokens"] / c[(t, "bare")]["output_tokens"]
              for t in {t for t, _ in c}
              if c.get((t, "discover"), {}).get("output_tokens") and c.get((t, "bare"), {}).get("output_tokens")]
    if ratios:
        med = round(statistics.median(ratios), 3)
        res["hypotheses"]["S1"] = {"median_ratio": med, "pass": med <= BANDS["S1_output_token_ratio_max"]}
    auto = [r for r in runs if r["arm"] == "discover-auto"]
    if auto:
        share = round(sum(1 for r in auto if (r.get("agent") or {}).get("ran_tests")) / len(auto), 3)
        res["hypotheses"]["S3"] = {"tests_ran_share": share, "pass": share >= BANDS["S3_tests_ran_min"]}
    h = res["hypotheses"]
    res["ga"] = bool(h.get("H1", {}).get("pass") and h.get("H2", {}).get("pass"))
    res["by_stack"] = {}
    for stack in sorted({v["stack"] for v in c.values() if v["stack"]}):
        d = [x for t, x in rec if c[(t, "bare")]["stack"] == stack]
        res["by_stack"][stack] = {"recall_mean_diff": round(statistics.fmean(d), 4) if d else None, "n_tasks": len(d)}
    res["cells"] = {f"{t}/{a}": v for (t, a), v in sorted(c.items())}
    return res


def report_md(res: dict, *, prereg_commit: Optional[str]) -> str:
    lines = ["# Agent-loop eval — results", "",
             f"- Pre-registration commit: {prereg_commit or '(not frozen)'}",
             f"- Tasks scored: {res['tasks']} · runs: {res['runs']}", ""]
    if not res["runs"]:
        return "\n".join(lines + ["No runs yet. See `python -m bench.agent plan`.", ""])
    lines += ["| Hypothesis | Result | Pass |", "|---|---|---|"]
    for k, v in res["hypotheses"].items():
        shown = {x: y for x, y in v.items() if x != "pass"}
        lines.append(f"| {k} | {shown} | {'yes' if v['pass'] else 'no'} |")
    lines += ["", f"**GA rule (H1 and H2, with the M6.13 gate green): {'met' if res['ga'] else 'not met'}**", "",
              "| Task / arm | n | recall | precision | out tokens | tool calls | build pass | ran tests |",
              "|---|---|---|---|---|---|---|---|"]
    fmt = lambda x: "—" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))  # noqa: E731
    for key, v in res["cells"].items():
        lines.append(f"| {key} | {v['n']} | {fmt(v['recall'])} | {fmt(v['precision'])} | {fmt(v['output_tokens'])} | "
                     f"{fmt(v['tool_calls'])} | {fmt(v['build_pass'])} | {fmt(v['ran_tests'])} |")
    return "\n".join(lines) + "\n"
