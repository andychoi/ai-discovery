"""python -m bench.agent <command> — agent-loop eval (roadmap item M5.7, design/benchmark-protocol.md §6).

  tasks                      list the task set (stack, gold file counts, prompt)
  validate                   check tasks against git history (parents, gold files, no file names in prompts)
  fetch                      clone repos that have no full-history checkout yet (network)
  plan [--exploratory]       run matrix in the pre-registered order + spend ceiling; spends nothing
  preflight                  build/test each task's unmodified base (toolchains), no model calls
  run --confirm-spend [--task ID ...] [--arm NAME ...] [--limit N] [--exploratory]
                             run the eval — refuses unless the pre-registration is Frozen and committed
  report [--write]           score results against the pre-registered bands (works with zero runs)
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import arms as arms_mod
from . import runner, score, tasks as tasks_mod


def cmd_tasks(_args) -> int:
    _, tasks = tasks_mod.load()
    for t in tasks:
        print(f"{t.id:22} {t.stack:7} gold {len(t.gold_files)} (+{len(t.gold_test_files)} tests)  {t.prompt[:80]}…")
    print(f"{len(tasks)} tasks, {len({t.stack for t in tasks})} stacks")
    return 0


def cmd_validate(_args) -> int:
    repos, tasks = tasks_mod.load()
    problems = tasks_mod.validate(repos, tasks)
    for p in problems:
        print(f"problem: {p}")
    print("tasks: ok" if not problems else f"tasks: {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_fetch(_args) -> int:
    repos, _ = tasks_mod.load()
    for spec in repos.values():
        print(f"{spec.id}: {tasks_mod.source_repo(spec, clone=True)}")
    return 0


def cmd_plan(args) -> int:
    cfg = runner.load_prereg()
    _, tasks = tasks_mod.load()
    cells = runner.plan(cfg, tasks, include_exploratory=args.exploratory)
    arms = sorted({c["arm"] for c in cells})
    print(f"pre-registration: {cfg.status} · model {cfg.model} · reps {cfg.reps} · seed {cfg.seed}")
    print(f"runs: {len(cells)} = {len(tasks)} tasks × {len(arms)} arms × {cfg.reps} reps")
    print(f"spend ceiling: ${cfg.budget_per_run_usd:.2f}/run (enforced by --max-budget-usd), "
          f"${cfg.budget_total_usd:.2f} total (enforced by the runner); worst case "
          f"${len(cells) * cfg.budget_per_run_usd:.2f} before the total cap")
    for a in arms:
        ok, why = arms_mod.ARMS[a].available()
        print(f"  arm {a:15} {arms_mod.ARMS[a].role:11} {'available' if ok else 'UNAVAILABLE: ' + why}")
    print("first runs in order:", ", ".join(c["run_id"] for c in cells[:5]), "…")
    frozen, why = runner.frozen()
    print("ready to run" if frozen else f"not runnable yet: {why}")
    return 0


def cmd_preflight(_args) -> int:
    repos, tasks = tasks_mod.load()
    work = Path(tempfile.mkdtemp(prefix="bench-agent-preflight-"))
    result = runner.preflight(tasks, repos, work)
    out = runner.RESULTS / "preflight.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for tid, r in result.items():
        print(f"{tid:22} build {r['build']['status']:14} test {r['test']['status']}")
    return 0


def cmd_run(args) -> int:
    cfg = runner.load_prereg()
    done = runner.run_all(cfg, confirm_spend=args.confirm_spend, only_tasks=set(args.task or []) or None,
                          only_arms=set(args.arm or []) or None, include_exploratory=args.exploratory,
                          limit=args.limit)
    print(f"{done} run(s) completed")
    return 0


def cmd_report(args) -> int:
    cfg = runner.load_prereg()
    runs = score.load_runs(runner.RESULTS / "runs")
    res = score.verdict(runs, resamples=cfg.bootstrap_resamples, seed=cfg.seed)
    frozen, sha = runner.frozen()
    text = score.report_md(res, prereg_commit=sha if frozen else None)
    print(text)
    if not runs:
        _, tasks = tasks_mod.load()
        print(f"plan: {len(runner.plan(cfg, tasks))} runs pending · pre-registration {cfg.status}")
    if args.write:
        out = runner.RESULTS / "report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bench.agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("tasks", cmd_tasks), ("validate", cmd_validate), ("fetch", cmd_fetch),
                     ("preflight", cmd_preflight)):
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("plan")
    p.add_argument("--exploratory", action="store_true")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("run")
    p.add_argument("--confirm-spend", action="store_true")
    p.add_argument("--task", action="append")
    p.add_argument("--arm", action="append")
    p.add_argument("--limit", type=int)
    p.add_argument("--exploratory", action="store_true")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report")
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
