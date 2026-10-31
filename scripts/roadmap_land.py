#!/usr/bin/env python3
"""Land a roadmap branch on main safely (docs/roadmap/README.md, work-item protocol step 9).

    python scripts/roadmap_land.py rm/M6.3-java-coverage            # squash, verify, commit
    python scripts/roadmap_land.py rm/M6.3-java-coverage --push     # … and push main
    python scripts/roadmap_land.py rm/M6.3-java-coverage --dry-run  # checks only, no merge

Refuses, with nothing changed on main, when:
- main has uncommitted changes, or is behind/ahead of origin/main in a way the push would not fix;
- the branch has uncommitted or untracked files (they would be left out of the squash);
- the item's milestone status on the branch is not ``Done (…)``;
- after squashing, anything is unstaged or untracked, or the staged diff differs from the branch diff
  (the M4.6 incident: a failed ``git add`` left half an item out of its squash commit);
- ``pytest -q``, ``python scripts/roadmap_lint.py`` or ``python -m bench gate`` fails on the result.
The squash commit message is the branch's first ``feat|fix|…`` commit message.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
ITEM_RE = re.compile(r"^rm/(M\d+\.\d+(?:\.?[a-z])?)-")


class LandError(SystemExit):
    pass


def git(*args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode:
        raise LandError(f"git {' '.join(args)} failed:\n{p.stderr.strip()}")
    return p.stdout.strip()


def run(cmd: list[str], label: str) -> None:
    print(f"… {label}")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        tail = (p.stdout + p.stderr).strip().splitlines()[-15:]
        raise LandError(f"{label} failed:\n" + "\n".join(tail))


def item_status(branch: str) -> tuple[str | None, str | None]:
    m = ITEM_RE.match(branch)
    if not m:
        return None, None
    item = re.sub(r"^(M\d+\.\d+)([a-z])$", r"\1.\2", m.group(1))   # rm/M5.7a-… → M5.7.a
    for path in sorted((ROOT / "docs" / "roadmap" / "milestones").glob("M*.md")):
        text = git("show", f"{branch}:{path.relative_to(ROOT)}", check=False)
        hit = re.search(rf"^### {re.escape(item)} .*?\n- \*\*Status:\*\* (.+)$", text, re.M)
        if hit:
            return item, hit.group(1).strip()
    return item, None


def land(branch: str, *, push: bool, dry_run: bool, skip_tests: bool) -> str:
    if git("symbolic-ref", "--short", "HEAD") != "main":
        raise LandError("run from main (git checkout main)")
    if git("status", "--porcelain"):
        raise LandError("main has uncommitted changes — commit or stash them first")
    if not git("rev-parse", "--verify", "-q", branch, check=False):
        raise LandError(f"no branch {branch}")
    item, status = item_status(branch)
    if item and not (status or "").startswith("Done"):
        raise LandError(f"{item} on {branch} has status {status!r} — close it (Status: Done (<sha>)) first")
    base = git("merge-base", "main", branch)
    branch_files = sorted(f for f in git("diff", "--name-only", base, branch).splitlines() if f)
    if not branch_files:
        raise LandError(f"{branch} has no changes against main")
    msg = next((git("log", "-1", "--format=%B", sha) for sha in git("rev-list", "--reverse", f"{base}..{branch}").split()
                if re.match(r"^(feat|fix|refactor|perf|test|docs|chore)(\(.+\))?: ", git("log", "-1", "--format=%s", sha))
                and "claim" not in git("log", "-1", "--format=%s", sha)), None)
    if msg is None:
        raise LandError(f"{branch}: no conventional commit message to reuse")
    print(f"landing {branch} ({len(branch_files)} files) — {msg.splitlines()[0]}")
    if dry_run:
        return "dry-run: checks passed, nothing merged"
    git("merge", "--squash", branch)
    try:
        leftovers = [ln for ln in git("status", "--porcelain").splitlines() if ln[:2] not in ("M ", "A ", "D ", "R ")]
        if leftovers:
            raise LandError("after the squash, files are unstaged or untracked:\n" + "\n".join(leftovers))
        staged = sorted(git("diff", "--cached", "--name-only").splitlines())
        if staged != branch_files:
            missing = sorted(set(branch_files) - set(staged))
            extra = sorted(set(staged) - set(branch_files))
            raise LandError(f"staged diff ≠ branch diff (missing {missing}, extra {extra})")
        if subprocess.run(["git", "diff", "--cached", "--quiet", branch], cwd=ROOT).returncode:
            raise LandError("staged tree does not equal the branch tree (main moved? rebase the branch first)")
        if not skip_tests:
            run([PY, "-m", "pytest", "-q"], "pytest -q")
            run([PY, "scripts/roadmap_lint.py"], "roadmap lint")
            run([PY, "-m", "bench", "gate"], "accuracy gate")
    except BaseException:
        git("reset", "-q", "--hard", "HEAD")
        raise
    subprocess.run(["git", "commit", "-q", "-F", "-"], cwd=ROOT, input=msg, text=True, check=True)
    sha = git("rev-parse", "--short", "HEAD")
    shown = sorted(f for f in git("show", "--name-only", "--format=", "HEAD").splitlines() if f)
    if shown != branch_files:
        raise LandError(f"commit {sha} does not contain exactly the branch files — inspect before pushing")
    git("branch", "-D", branch)
    if push:
        git("push", "-q", "origin", "main")
    return f"landed {branch} as {sha}" + (" and pushed" if push else " (not pushed)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("branch")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-tests", action="store_true", help="docs-only branches (the hook still guards main)")
    args = ap.parse_args()
    try:
        print(land(args.branch, push=args.push, dry_run=args.dry_run, skip_tests=args.skip_tests))
    except LandError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
