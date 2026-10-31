"""Run the agent-loop eval (roadmap item M5.7). Spends money only via ``run_all(..., confirm_spend=True)``.

Order of a run: history-free workspace at the task's parent commit → arm setup (index,
instructions block) → agent with a per-run spend cap and timeout → harness measures:
changed files, files-touched recall/precision, build and tests (run by the harness),
transcript usage, tool calls, ai-discovery ledger. One JSON per run; reruns skip
finished runs, so an interrupted batch resumes in the frozen order.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import arms as arms_mod
from .metrics import file_scores, parse_claude_stream, parse_codex_stream
from .tasks import BUILD, ROOT, RepoSpec, Task, changed_files, load, make_workspace

HERE = Path(__file__).parent
RESULTS = HERE / "results"
PREREG = ROOT / "docs" / "roadmap" / "benchmarks" / "2026-10-31-agent-eval-prereg.md"


@dataclass(frozen=True)
class Config:
    status: str
    model: str
    codex_model: Optional[str]
    reps: int
    seed: int
    arms: tuple[str, ...]
    exploratory_arms: tuple[str, ...]
    budget_per_run_usd: float
    budget_total_usd: float
    timeout_minutes: int
    bootstrap_resamples: int


def load_prereg(path: Path = PREREG) -> Config:
    """Read the machine block ``<!-- prereg … -->`` from the pre-registration page."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"<!--\s*prereg\s*\n(.*?)-->", text, re.S)
    if not m:
        raise SystemExit(f"{path}: no <!-- prereg … --> block")
    kv = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()
    split = lambda v: tuple(x.strip() for x in v.split(",") if x.strip() and x.strip() != "none")  # noqa: E731
    return Config(status=kv["status"], model=kv["model"],
                  codex_model=None if kv.get("codex_model", "none") == "none" else kv["codex_model"],
                  reps=int(kv["reps"]), seed=int(kv["seed"]), arms=split(kv["arms"]),
                  exploratory_arms=split(kv.get("exploratory_arms", "none")),
                  budget_per_run_usd=float(kv["budget_per_run_usd"]),
                  budget_total_usd=float(kv["budget_total_usd"]),
                  timeout_minutes=int(kv.get("timeout_minutes", "30")),
                  bootstrap_resamples=int(kv.get("bootstrap_resamples", "10000")))


def frozen(path: Path = PREREG) -> tuple[bool, str]:
    """Frozen = status Frozen AND the page is committed unchanged (so the commit is the pre-registration)."""
    cfg = load_prereg(path)
    if cfg.status != "Frozen":
        return False, f"pre-registration status is {cfg.status!r}, not 'Frozen'"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(path)], cwd=ROOT, capture_output=True)
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(path)], cwd=ROOT)
    if tracked.returncode or dirty.returncode:
        return False, "pre-registration has uncommitted changes — commit it before running"
    sha = subprocess.run(["git", "log", "-1", "--format=%h", "--", str(path)], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip()
    return True, sha


def plan(cfg: Config, tasks: list[Task], *, include_exploratory: bool = False) -> list[dict]:
    """The full run matrix in the pre-registered random order."""
    names = list(cfg.arms) + (list(cfg.exploratory_arms) if include_exploratory else [])
    cells = [{"task": t.id, "arm": a, "rep": r} for t in tasks for a in names for r in range(1, cfg.reps + 1)]
    random.Random(cfg.seed).shuffle(cells)
    for i, c in enumerate(cells, 1):
        c["order"] = i
        c["run_id"] = f"{c['task']}__{c['arm']}__r{c['rep']}"
    return cells


def _sh(cmd: str, cwd: Path, timeout: int = 1800) -> dict:
    started = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout)
        status = "pass" if p.returncode == 0 else "fail"
        tail = (p.stdout + p.stderr)[-1500:]
        if p.returncode == 127 or "command not found" in tail:
            status = "not_run"
    except subprocess.TimeoutExpired:
        status, tail = "timeout", ""
    return {"cmd": cmd, "status": status, "seconds": round(time.monotonic() - started, 1), "tail": tail}


def _build_cmds(task: Task, ws: Path, changed: list[str]) -> tuple[Optional[str], Optional[str]]:
    build, test = BUILD[task.stack]
    if build and "{changed_js}" in build:
        js = [f for f in changed if f.endswith(".js") and (ws / f).exists()]
        build = " && ".join(f"node --check {json.dumps(f)}" for f in js) or None
    return build, test


def preflight(tasks: list[Task], repos: dict[str, RepoSpec], work: Path) -> dict[str, dict]:
    """Build each task's *unmodified* base once: a base that does not build makes build/test unscoreable there."""
    out = {}
    for t in tasks:
        ws = make_workspace(repos[t.repo], t, work / f"preflight-{t.id}")
        build, test = _build_cmds(t, ws, list(t.gold_files))
        out[t.id] = {"build": _sh(build, ws) if build else {"status": "not_applicable"},
                     "test": _sh(test, ws) if test else {"status": "not_applicable"}}
        shutil.rmtree(ws, ignore_errors=True)
    return out


def run_one(cell: dict, task: Task, repo: RepoSpec, cfg: Config, out_dir: Path) -> dict:
    arm = arms_mod.ARMS[cell["arm"]]
    run_dir = out_dir / "work" / cell["run_id"]
    ws = make_workspace(repo, task, run_dir / "ws")
    scratch = run_dir / "harness"
    scratch.mkdir(parents=True, exist_ok=True)
    record = {**cell, "stack": task.stack, "model": arms_mod.model_for(arm, cfg.model, cfg.codex_model),
              "role": arm.role}
    record["setup"] = arms_mod.prepare(arm, ws, run=lambda argv: subprocess.run(argv, check=True,
                                                                                  capture_output=True))
    argv, env = arms_mod.command(arm, prompt=task.prompt, ws=ws, model=record["model"],
                                 budget_usd=cfg.budget_per_run_usd, scratch=scratch)
    transcript = out_dir / "transcripts" / f"{cell['run_id']}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with transcript.open("w", encoding="utf-8") as fh:
        try:
            proc = subprocess.run(argv, cwd=ws, stdout=fh, stderr=subprocess.STDOUT, text=True,
                                  timeout=cfg.timeout_minutes * 60, env={**os.environ, **env})
            record["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            record["exit_code"] = "timeout"
    record["wall_seconds"] = round(time.monotonic() - started, 1)
    lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    record["agent"] = parse_claude_stream(lines) if arm.agent == "claude" else parse_codex_stream(lines)
    changed = changed_files(ws)
    record["changed_files"] = changed
    record["files"] = file_scores(changed, task.gold_files, task.stack)
    build, test = _build_cmds(task, ws, changed)
    record["build"] = _sh(build, ws) if build else {"status": "not_applicable"}
    record["test"] = _sh(test, ws) if test else {"status": "not_applicable"}
    ledger = ws / ".discovery" / "logs" / "mcp_calls.jsonl"
    if ledger.exists():
        from ai_discovery.mcp.ledger import summarize

        record["ledger"] = {k: v for k, v in summarize(ledger).items() if k in ("calls", "tools", "rates")}
    subprocess.run(["git", "diff", "--output", str(out_dir / "transcripts" / f"{cell['run_id']}.diff")], cwd=ws)
    shutil.rmtree(run_dir, ignore_errors=True)
    return record


def run_all(cfg: Config, *, confirm_spend: bool, out_dir: Path = RESULTS, only_tasks: Optional[set] = None,
            only_arms: Optional[set] = None, include_exploratory: bool = False, limit: Optional[int] = None) -> int:
    ok, why = frozen()
    if not ok:
        raise SystemExit(f"refusing to run: {why}")
    if not confirm_spend:
        raise SystemExit("refusing to run without --confirm-spend (see `python -m bench.agent plan` for the ceiling)")
    repos, tasks = load()
    by_id = {t.id: t for t in tasks}
    cells = [c for c in plan(cfg, tasks, include_exploratory=include_exploratory)
             if (not only_tasks or c["task"] in only_tasks) and (not only_arms or c["arm"] in only_arms)]
    for name in {c["arm"] for c in cells}:
        avail, why = arms_mod.ARMS[name].available()
        if not avail:
            raise SystemExit(f"arm {name} unavailable: {why}")
    runs = out_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    spent = sum((json.loads(p.read_text()).get("agent") or {}).get("cost_usd") or 0 for p in runs.glob("*.json"))
    done = 0
    for cell in cells:
        if limit is not None and done >= limit:
            break
        dest = runs / f"{cell['run_id']}.json"
        if dest.exists():
            continue
        if spent + cfg.budget_per_run_usd > cfg.budget_total_usd:
            print(f"stopping: next run could exceed the total ceiling ${cfg.budget_total_usd:.2f} (spent ${spent:.2f})")
            break
        task = by_id[cell["task"]]
        record = run_one(cell, task, repos[task.repo], cfg, out_dir)
        dest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        spent += (record.get("agent") or {}).get("cost_usd") or 0
        done += 1
        print(f"[{cell['order']}/{len(cells)}] {cell['run_id']}: recall {record['files']['recall']} "
              f"precision {record['files']['precision']} build {record['build']['status']} ${spent:.2f}")
    return done


__all__ = ["Config", "load_prereg", "frozen", "plan", "preflight", "run_all", "run_one"]
