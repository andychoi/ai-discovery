"""Accuracy gates (roadmap item M6.13, design/benchmark-protocol.md §1, §4).

Two layers:

**Offline (every CI run).** Scores every fixture project (``tests/fixtures/projects``)
and every *dev* hard case (``tests/corpus/hard``) against its committed baseline.
A drop beyond ``TOLERANCE`` in any dimension fails; a rise is reported so the
baseline can be refreshed on purpose (``--update``, with a PROGRESS.md line).
Hard cases are split into dev and sealed halves by a fixed seed (``SPLIT_SEED``);
the **sealed** half is evaluated only with ``--sealed`` (milestone gates) and
prints counts, never per-case detail, so it cannot be used for tuning.

**External (``--external``, network).** For each pinned repo with a hand-graded
precision sheet: scan at the pinned SHA, then

- *precision* per language × origin — of the graded edges still emitted, the share
  graded correct; fails when it falls below the baseline's Wilson 95% lower bound
  (edges graded wrong that are still emitted are listed as a to-fix list);
- *recall* per repo (never pooled) — key-edge recall vs the recorded run; a drop
  fails unless unresolved calls rose with it (the M1.12 trade-off);
- *drift* — when an origin's edge count moved more than ``RESAMPLE_SHARE`` since the
  recorded run, the sample no longer represents it: re-sample (a warning, not a failure).
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "projects"
HARD = ROOT / "tests" / "corpus" / "hard"
FIXTURE_BASELINE = ROOT / "tests" / "corpus" / "baseline.json"
HARD_BASELINES = {"dev": HARD / "baseline_dev.json", "sealed": HARD / "baseline_sealed.json"}
SHEETS = Path(__file__).parent / "precision" / "sheets"
RAW = ROOT / "docs" / "roadmap" / "benchmarks" / "raw"
CURRENT = SHEETS / "CURRENT.json"           # repo id → {sheet, run}: the graded sample and its recorded run
SPLIT_SEED = 6013
TOLERANCE = 1e-3
RESAMPLE_SHARE = 0.20


@dataclass
class GateResult:
    failures: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


# ── offline: fixtures + hard cases ────────────────────────────────────────

def split_of(case: str, seed: int = SPLIT_SEED) -> str:
    """Fixed, seed-derived split: nobody chooses which hard cases are sealed."""
    return "sealed" if hashlib.sha256(f"{seed}:{case}".encode()).digest()[0] % 2 else "dev"


def _projects(root: Path) -> list[tuple[Path, dict]]:
    return [(p.parent, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(root.glob("*/ground_truth.json"))]


def score_tree(root: Path, *, only: Optional[str] = None) -> dict[str, dict]:
    """{project: {dimension: {precision, recall, f1, missing, extra}}}; ``only`` = dev | sealed for hard cases."""
    from tests.corpus.scoring import score_project

    out = {}
    for path, gt in _projects(root):
        if only and split_of(gt["project"]) != only:
            continue
        out[gt["project"]] = {d: {**s.as_dict(), "missing": list(s.missing), "extra": list(s.extra)}
                              for d, s in score_project(path, gt).items()}
    return out


def compare(current: dict, baseline: dict, *, label: str, detail: bool = True) -> GateResult:
    res = GateResult()
    for project, dims in sorted(current.items()):
        base = baseline.get(project)
        if base is None:
            res.warnings.append(f"{label}/{project}: no baseline yet (run `python -m bench gate --update`)")
            continue
        for dim, sc in sorted(dims.items()):
            if dim not in base:
                continue
            was, now = base[dim]["f1"], sc["f1"]
            where = f"{label}/{project}/{dim}" if detail else label
            if now < was - TOLERANCE:
                why = f" missing={sc['missing']} extra={sc['extra']}" if detail else ""
                res.failures.append(f"{where}: F1 {was:.3f} → {now:.3f}{why}")
            elif now > was + TOLERANCE:
                res.improvements.append(f"{where}: F1 {was:.3f} → {now:.3f}")
    for project in sorted(set(baseline) - set(current)):
        res.warnings.append(f"{label}/{project}: in the baseline but not scored (fixture removed?)")
    return res


def _strip(report: dict) -> dict:
    return {p: {d: {k: v for k, v in s.items() if k in ("precision", "recall", "f1")} for d, s in dims.items()}
            for p, dims in report.items()}


def offline_gate(*, sealed: bool = False, update: bool = False) -> GateResult:
    total = GateResult()
    plans = [("fixtures", score_tree(FIXTURES), FIXTURE_BASELINE, True),
             ("hard:dev", score_tree(HARD, only="dev"), HARD_BASELINES["dev"], True)]
    if sealed:
        plans.append(("hard:sealed", score_tree(HARD, only="sealed"), HARD_BASELINES["sealed"], False))
    for label, current, path, detail in plans:
        baseline = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if update:
            path.write_text(json.dumps(_strip(current), indent=2) + "\n", encoding="utf-8")
            total.warnings.append(f"{label}: baseline written to {path.relative_to(ROOT)} — add a justification "
                                  f"line to docs/roadmap/PROGRESS.md")
            baseline = _strip(current)
        res = compare(current, baseline, label=label, detail=detail)
        total.failures += res.failures
        total.improvements += res.improvements
        total.warnings += res.warnings
        if detail:
            total.report[label] = _strip(current)
        else:   # sealed: aggregate only
            total.report[label] = {"cases": len(current), "regressions": len(res.failures),
                                   "improvements": len(res.improvements)}
    total.report["split"] = {"seed": SPLIT_SEED,
                             "dev": sorted(gt["project"] for _, gt in _projects(HARD) if split_of(gt["project"]) == "dev"),
                             "sealed_count": sum(1 for _, gt in _projects(HARD) if split_of(gt["project"]) == "sealed")}
    return total


# ── external: graded precision + per-repo recall ──────────────────────────

def load_sheet(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if (row.get("verdict") or "").strip()]


def _edge_key(e: dict) -> tuple:
    """Identify an edge by where its caller and target are defined (file:line), so renamed qualified
    names (e.g. C# namespaces fixed in M6.2) still match the graded sheet; names only as a fallback."""
    if e.get("caller_file"):
        target = (e["resolved_file"], str(e.get("resolved_line"))) if e.get("resolved_file") else \
            (e.get("resolved_to") or "",)   # synthesized targets (`external::…`) have no file
        return (e["caller_file"], str(e.get("caller_line")), *target)
    return (e["caller"], e["resolved_to"])


def precision_check(graded: list[dict], emitted: list[dict],
                    recorded_by_origin: Optional[dict] = None) -> tuple[dict, list[str], list[str]]:
    """Per stratum (language × origin): graded precision at sampling time vs now.

    The sheet sampled edges that were all emitted when it was graded, so the baseline
    precision already includes the edges graded wrong. *Now* = the graded edges still
    emitted. A regression is current precision below the baseline's Wilson 95% lower
    bound. Wrong edges still emitted are listed (a to-fix list), not failed; correct
    edges that disappeared are counted (recall is judged per repo by key edges).
    *Drift*: when an origin's emitted-edge count moved more than ``RESAMPLE_SHARE``
    from the recorded run, the sample no longer represents the stratum — re-sample.

    Returns (strata report, failures, warnings).
    """
    from .precision.score import wilson

    now = {_edge_key(e) for e in emitted if e.get("resolved_to")}
    empty = {"base_c": 0, "base_n": 0, "cur_c": 0, "cur_n": 0, "wrong_still": [], "correct_gone": 0}
    strata: dict[tuple[str, str], dict] = defaultdict(lambda: {**empty, "wrong_still": []})
    for row in graded:
        verdict = row["verdict"].strip().lower()
        if verdict not in ("correct", "wrong"):
            continue
        s = strata[(row["language"], row["origin"])]
        edge = _edge_key(row)
        s["base_n"] += 1
        s["base_c"] += verdict == "correct"
        if edge in now:
            s["cur_n"] += 1
            s["cur_c"] += verdict == "correct"
            if verdict == "wrong":
                s["wrong_still"].append(f"{row['caller']} → {row['resolved_to']}")
        elif verdict == "correct":
            s["correct_gone"] += 1
    report, failures, warnings = {}, [], []
    for key in sorted(strata):
        s = strata[key]
        lo, _ = wilson(s["base_c"], s["base_n"])
        cur_p = round(s["cur_c"] / s["cur_n"], 3) if s["cur_n"] else None
        name = f"{key[0]}/{key[1]}"
        report[name] = {"baseline_precision": round(s["base_c"] / s["base_n"], 3), "baseline_ci_low": lo,
                        "graded": s["base_n"], "current_precision": cur_p, "graded_still_emitted": s["cur_n"],
                        "correct_edges_lost": s["correct_gone"], "known_wrong_still_emitted": s["wrong_still"]}
        if cur_p is not None and cur_p < lo:
            failures.append(f"{name}: precision {cur_p} below the baseline 95% lower bound {lo}")
        if s["correct_gone"]:
            warnings.append(f"{name}: {s['correct_gone']} edge(s) graded correct are no longer emitted")
    if recorded_by_origin:
        now_by_origin: dict[str, int] = defaultdict(int)
        for e in emitted:
            if e.get("resolved_to"):
                now_by_origin[e["origin"]] += 1
        drift = {}
        for origin in sorted(set(recorded_by_origin) | set(now_by_origin)):
            before, after = int(recorded_by_origin.get(origin, 0)), now_by_origin.get(origin, 0)
            drift[origin] = {"recorded": before, "now": after}
            if before and abs(after - before) / before > RESAMPLE_SHARE:
                warnings.append(f"origin {origin}: {before} → {after} edges since the graded sample — re-sample "
                                f"(`python -m bench sample`) before trusting its precision")
        report["_drift"] = drift
    return report, failures, warnings


def recall_check(repo_id: str, current: dict, recorded: Optional[dict]) -> tuple[dict, list[str]]:
    cur = (current.get("ground_truth") or {})
    rec = ((recorded or {}).get("ground_truth") or {})
    out = {"current": cur.get("call_edge_recall"), "baseline": rec.get("call_edge_recall"),
           "status": cur.get("status"),
           "unresolved_now": (current.get("unresolved") or {}).get("unresolved_calls_rows"),
           "unresolved_before": ((recorded or {}).get("unresolved") or {}).get("unresolved_calls_rows")}
    failures = []
    if cur.get("status") == "ok" and rec.get("status") == "ok" and out["current"] is not None and \
            out["baseline"] is not None and out["current"] < out["baseline"] - TOLERANCE:
        rose = (out["unresolved_now"] or 0) > (out["unresolved_before"] or 0)
        if not rose:
            failures.append(f"{repo_id}: key-edge recall {out['baseline']} → {out['current']} without a matching "
                            f"rise in unresolved calls ({out['unresolved_before']} → {out['unresolved_now']})")
    return out, failures


def current_sheets(path: Path = CURRENT) -> dict[str, tuple[Path, Path]]:
    """Repo id → (graded sheet, recorded run) named by ``CURRENT.json``; a missing file is an error."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for repo_id, entry in sorted(data.items()):
        if repo_id.startswith("_"):
            continue
        sheet, run = SHEETS / entry["sheet"], RAW / entry["run"]
        for f in (sheet, run):
            if not f.exists():
                raise FileNotFoundError(f"{path.name}: {repo_id} names {f.name}, which does not exist")
        out[repo_id] = (sheet, run)
    return out


def external_gate(*, fetch: bool = False, repo_ids: Optional[list[str]] = None) -> GateResult:
    from .precision.sampler import emitted_edges
    from .repos import fetch as fetch_repo, load_repos, locate
    from .run_ai_discovery import run

    res = GateResult()
    repos = load_repos()
    for repo_id, (sheet, recorded_path) in current_sheets().items():
        if repo_ids and repo_id not in repo_ids:
            continue
        repo = repos.get(repo_id)
        if repo is None:
            res.warnings.append(f"{sheet.name}: no pinned repo {repo_id!r} in bench/repos.toml")
            continue
        src = locate(repo)
        if src.path is None and fetch:
            fetch_repo(repo)
            src = locate(repo)
        if src.path is None:
            res.warnings.append(f"{repo_id}: source not available at {repo.sha[:12]} — skipped (use --fetch)")
            res.report[repo_id] = {"status": "skipped"}
            continue
        state = Path(tempfile.mkdtemp(prefix=f"bench-gate-{repo_id}-"))
        try:
            current = run(repo, src, keep_state=state)
            if not current.get("ok"):
                res.failures.append(f"{repo_id}: scan failed: {current.get('error')}")
                continue
            if current.get("void"):
                res.failures.append(f"{repo_id}: scan indexed 0 files (void arm — protocol rule 6)")
                continue
            emitted = emitted_edges(state / "discovery.db", 1, src.path)
        finally:
            shutil.rmtree(state, ignore_errors=True)
        recorded = json.loads(recorded_path.read_text()) if recorded_path.exists() else None
        strata, p_fail, p_warn = precision_check(load_sheet(sheet), emitted,
                                                 (recorded or {}).get("edges_by_resolved_by"))
        recall, r_fail = recall_check(repo_id, current, recorded)
        res.failures += [f"{repo_id}/{f}" for f in p_fail] + r_fail
        res.warnings += [f"{repo_id}/{w}" for w in p_warn]
        res.report[repo_id] = {"status": "scored", "precision": strata, "recall": recall,
                               "edges_total": current.get("edges_total")}
    return res
