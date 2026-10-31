"""Mine candidate change tasks from git history (roadmap item M5.7).

A candidate is a non-merge commit whose diff against its parent:
- touches 2–8 source files of the repo's stack (tests counted separately),
- changes ≤ 400 lines in total,
- has a message that reads like a behaviour change (not a bump, format, docs, CI,
  rename, revert or release commit).

The filters are fixed here so the selection can be pre-registered; a human then
writes each task's prompt from the commit message and diff (without naming files).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SOURCE_EXT = {"java": {".java", ".jsp", ".html", ".xml", ".properties", ".sql"},
              "dotnet": {".cs", ".razor", ".cshtml", ".csproj", ".json"},
              "node": {".js", ".ts", ".json"},
              "python": {".py", ".html"}}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(Test|Tests|Spec|IT)\.[a-z]+$|(\.test|\.spec)\.[jt]s$|"
                       r"(^|/)test_[^/]+\.py$")
SKIP_MSG = re.compile(r"\b(bump|upgrade|update (?:to|dependenc)|dependabot|renovate|format|lint|typo|readme|docs?\b|"
                      r"license|\bci\b|workflow|github action|version|release|merge|revert|rename|refactor|cleanup|"
                      r"clean up|polish|whitespace|copyright|checkstyle|spotless|chore)", re.I)
IGNORE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|\.github/|docs?/|README|\.mvn/|mvnw|gradlew|"
                    r"global\.json|Directory\.Packages\.props)")


@dataclass
class Candidate:
    sha: str
    parent: str
    subject: str
    source_files: list[str]
    test_files: list[str]
    lines: int


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def candidates(repo: Path, stack: str, *, limit: int = 400) -> list[Candidate]:
    out = []
    log = _git(repo, "log", "--no-merges", f"-{limit}", "--format=%H%x09%P%x09%s")
    exts = SOURCE_EXT[stack]
    for line in log.splitlines():
        sha, parents, subject = (line.split("\t", 2) + ["", ""])[:3]
        if not parents or " " in parents or SKIP_MSG.search(subject):
            continue
        numstat = _git(repo, "diff", "--numstat", parents, sha)
        files, tests, lines = [], [], 0
        for row in numstat.splitlines():
            added, deleted, path = row.split("\t", 2)
            if IGNORE.search(path) or Path(path).suffix not in exts:
                continue
            lines += int(added) if added.isdigit() else 0
            lines += int(deleted) if deleted.isdigit() else 0
            (tests if TEST_PATH.search(path) else files).append(path)
        if 2 <= len(files) <= 8 and lines <= 400:
            out.append(Candidate(sha, parents, subject, files, tests, lines))
    return out


if __name__ == "__main__":
    import sys

    repo, stack = Path(sys.argv[1]), sys.argv[2]
    for c in candidates(repo, stack):
        print(f"{c.sha[:10]} {c.lines:4} {len(c.source_files)}+{len(c.test_files)}t  {c.subject[:90]}")
