"""python -m bench <command> — see design/benchmark-protocol.md.

  list                                   pinned repos and where their source is
  verify-repos [--repo ID]               check each pinned SHA is fetchable (network)
  fetch --repo ID                        shallow-fetch the pinned commit into the cache
  run --repo ID [--dry-run] [--fetch] [--save] [--prose --budget USD] [--out FILE]
                                         keyless scan → metrics JSON (stdout; --save → bench/results/)
  sample --repo ID --seed N [--per-stratum 30] [--fetch]
                                         seeded stratified edge sample → CSV for hand grading
  score SHEET.csv [...]                  precision per stratum (Wilson 95% CI) from graded sheets
  gate [--sealed] [--external [--fetch] [--repo ID ...]] [--update] [--json]
                                         accuracy gates (M6.13): exit 1 on any regression
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
from pathlib import Path

from .repos import ROOT, fetch, load_repos, locate, verify_remote

RESULTS = Path(__file__).with_name("results")
SHEETS = Path(__file__).parent / "precision" / "sheets"


def _repo(args):
    repos = load_repos()
    if args.repo not in repos:
        sys.exit(f"unknown repo {args.repo!r}; known: {', '.join(repos)}")
    return repos[args.repo]


def _source(repo, want_fetch: bool):
    src = locate(repo)
    if src.path is None and want_fetch:
        fetch(repo)
        src = locate(repo)
    if src.path is None:
        sys.exit(f"{repo.id}: source not available at {repo.sha[:12]} ({src.note or 'not fetched'}); "
                 f"run `python -m bench fetch --repo {repo.id}` or pass --fetch")
    return src


def cmd_list(_args) -> int:
    for repo in load_repos().values():
        src = locate(repo)
        where = str(src.path.relative_to(ROOT.parent)) if src.path and ROOT.parent in src.path.parents else src.how
        gt = "gt" if repo.ground_truth else "  "
        print(f"{repo.id:24} {repo.sha[:12]} {repo.status:9} {gt} {repo.stack:18} {repo.license:14} {where}")
    return 0


def cmd_verify(args) -> int:
    repos = load_repos()
    targets = [repos[args.repo]] if args.repo else list(repos.values())
    bad = 0
    for repo in targets:
        ok = verify_remote(repo)
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {repo.id} {repo.sha[:12]} {repo.url}")
    return 1 if bad else 0


def cmd_fetch(args) -> int:
    print(fetch(_repo(args)))
    return 0


def cmd_run(args) -> int:
    from .run_ai_discovery import run, scan_command

    repo = _repo(args)
    if args.dry_run:
        src = locate(repo)
        plan = {"repo": repo.id, "sha": repo.sha, "stack": repo.stack, "status": repo.status,
                "source": src.how, "path": str(src.path) if src.path else None, "note": src.note,
                "command": scan_command(src.path or Path(f"<cache>/{repo.id}"), Path("<tmp-state-dir>")),
                "ground_truth": repo.ground_truth, "would_fetch": src.path is None}
        print(json.dumps(plan, indent=2))
        return 0
    if args.source:
        from .repos import Source, _head

        path = Path(args.source).resolve()
        if _head(path) != repo.sha:
            sys.exit(f"--source {path} is not at the pinned {repo.sha[:12]}")
        src = Source(path, "override")
    else:
        src = _source(repo, args.fetch)
    result = run(repo, src, prose=args.prose, budget=args.budget)
    result["date"] = dt.date.today().isoformat()
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.save:
        RESULTS.mkdir(exist_ok=True)
        out = RESULTS / f"{result['date']}-{repo.id}.json"
        out.write_text(text + "\n", encoding="utf-8")
        print(f"saved {out.relative_to(ROOT)}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def cmd_sample(args) -> int:
    from .precision.sampler import emitted_edges, stratified_sample, write_sheet
    from .run_ai_discovery import run

    repo = _repo(args)
    src = _source(repo, args.fetch)
    state = Path(tempfile.mkdtemp(prefix=f"bench-sample-{repo.id}-"))
    try:
        result = run(repo, src, keep_state=state)
        if not result.get("ok"):
            print(json.dumps(result, indent=2), file=sys.stderr)
            return 1
        rows = stratified_sample(emitted_edges(state / "discovery.db", 1, src.path),
                                 seed=args.seed, per_stratum=args.per_stratum)
    finally:
        shutil.rmtree(state, ignore_errors=True)
    out = Path(args.out) if args.out else SHEETS / f"{repo.id}-{repo.sha[:12]}-seed{args.seed}.csv"
    write_sheet(rows, out)
    print(f"{len(rows)} edges → {out}")
    return 0


def cmd_score(args) -> int:
    from .precision.score import score_sheet

    print(json.dumps([score_sheet(Path(p)) for p in args.sheets], indent=2))
    return 0


def cmd_gate(args) -> int:
    from .gate import external_gate, offline_gate

    result = offline_gate(sealed=args.sealed, update=args.update)
    if args.external:
        ext = external_gate(fetch=args.fetch, repo_ids=args.repo or None)
        result.failures += ext.failures
        result.warnings += ext.warnings
        result.report["external"] = ext.report
    if args.json:
        print(json.dumps({"ok": result.ok, "failures": result.failures, "improvements": result.improvements,
                          "warnings": result.warnings, "report": result.report}, indent=2, sort_keys=True))
    else:
        for label in ("fixtures", "hard:dev"):
            projects = result.report.get(label, {})
            print(f"{label}: {len(projects)} project(s) scored")
        if "hard:sealed" in result.report:
            s = result.report["hard:sealed"]
            print(f"hard:sealed: {s['cases']} case(s), {s['regressions']} regression(s), "
                  f"{s['improvements']} improvement(s) — details withheld (sealed split)")
        for repo_id, r in sorted(result.report.get("external", {}).items()):
            if r.get("status") != "scored":
                print(f"external/{repo_id}: {r.get('status')}")
                continue
            rc = r["recall"]
            print(f"external/{repo_id}: recall {rc['baseline']} → {rc['current']}; "
                  + ", ".join(f"{k} {v['current_precision']}" for k, v in sorted(r["precision"].items())
                              if not k.startswith("_") and v["current_precision"] is not None))
        for w in result.warnings:
            print(f"warning: {w}")
        for i in result.improvements:
            print(f"improved: {i} — refresh with `python -m bench gate --update` and justify in PROGRESS.md")
        for f in result.failures:
            print(f"FAIL: {f}")
        print("gate: ok" if result.ok else f"gate: {len(result.failures)} regression(s)")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("verify-repos")
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("fetch")
    p.add_argument("--repo", required=True)
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("run")
    p.add_argument("--repo", required=True)
    p.add_argument("--dry-run", action="store_true", help="print the plan; scan nothing")
    p.add_argument("--fetch", action="store_true", help="fetch the pinned commit if no local copy matches")
    p.add_argument("--save", action="store_true", help="also write bench/results/<date>-<repo>.json")
    p.add_argument("--out", help="also write the JSON to this file")
    p.add_argument("--prose", action="store_true", help="LLM run (spends money; needs a configured provider)")
    p.add_argument("--budget", type=float, help="USD cap for --prose runs")
    p.add_argument("--source", help="scan this checkout instead (must be at the pinned SHA)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("sample")
    p.add_argument("--repo", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--per-stratum", type=int, default=30)
    p.add_argument("--out")
    p.add_argument("--fetch", action="store_true")
    p.set_defaults(fn=cmd_sample)
    p = sub.add_parser("score")
    p.add_argument("sheets", nargs="+")
    p.set_defaults(fn=cmd_score)
    p = sub.add_parser("gate", help="accuracy gates: fixtures + dev hard cases (+ sealed / external)")
    p.add_argument("--sealed", action="store_true", help="also score the sealed hard cases (milestone gates only)")
    p.add_argument("--external", action="store_true", help="also scan pinned repos vs graded sheets (network)")
    p.add_argument("--fetch", action="store_true", help="with --external: fetch pinned commits that are missing")
    p.add_argument("--repo", action="append", help="with --external: only these repo ids")
    p.add_argument("--update", action="store_true", help="rewrite offline baselines from this run (justify!)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_gate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
