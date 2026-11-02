"""Task set and history-free workspaces for the agent-loop eval (roadmap item M5.7)."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
TASKS_FILE = HERE / "tasks.toml"
REPO_CACHE = HERE / ".repos"
HARNESS_FILES = (".discovery/", ".mcp.json", "CLAUDE.md", "AGENTS.md", ".claude/")   # never counted as edits

BUILD = {   # (build, test) per stack, run by the harness after the agent finishes — never by the agent's word
    "java": ("./mvnw -q -DskipTests compile", "./mvnw -q test"),
    # {dotnet_slnf}: the solution minus Aspire host projects (see `dotnet_filter`)
    # NuGetAudit off: advisories published after a task's commit would fail a warnings-as-errors build it passed
    # then. LibraryRestore off: LibMan fetches browser assets (bootstrap, …) from a CDN at build time; they are not
    # compiled, and the fetch is not reproducible.
    "dotnet": ("dotnet build {dotnet_slnf} --nologo -v q -p:NuGetAudit=false -p:LibraryRestore=false",
               "dotnet test {dotnet_slnf} --nologo -v q -p:NuGetAudit=false -p:LibraryRestore=false"),
    "node": ("node --check {changed_js}", None),
}


DOTNET_FILTER = ".git/bench-build.slnf"     # inside .git/: invisible to `git status`, so never an agent edit
# projects that need an SDK workload: Aspire hosts (incl. functional tests that are hosts), MAUI / mobile targets
_NEEDS_WORKLOAD = re.compile(
    r"<IsAspireHost>\s*true\s*</IsAspireHost>|Sdk=\"Aspire\.AppHost\.Sdk|<UseMaui>\s*true\s*</UseMaui>"
    r"|<TargetFrameworks?>[^<]*-(?:android|ios|maccatalyst|tizen|windows)", re.I)
_PROJECT_REF = re.compile(r'<ProjectReference\s+Include="([^"]+\.csproj)"', re.I)
_SLN_PROJECT = re.compile(r'^Project\("\{[^}]+\}"\)\s*=\s*"[^"]*",\s*"([^"]+\.csproj)"', re.M)


def dotnet_filter(ws: Path) -> tuple[Optional[str], list[str]]:
    """Write a solution filter of the workspace's solution without projects that need an SDK workload, nor those
    referencing one; return its path (relative to *ws*) and the projects left out.

    Workload projects (Aspire hosts and the functional tests that are hosts, which also start containers; MAUI
    apps) need the workloads of their day, which the run environment does not install. The rule is the same for
    every arm and the excluded projects are recorded per run (pre-registration §3).
    """
    slns = sorted(ws.glob("*.sln"))
    if not slns or not (ws / ".git").is_dir():
        return None, []
    projects = [p.replace("\\", "/") for p in _SLN_PROJECT.findall(slns[0].read_text(encoding="utf-8", errors="replace"))]
    text = {p: (ws / p).read_text(encoding="utf-8", errors="replace") if (ws / p).exists() else "" for p in projects}
    refs = {p: {str((Path(p).parent / r.replace("\\", "/")).as_posix()) for r in _PROJECT_REF.findall(text[p])}
            for p in projects}
    refs = {p: {_normpath(r) for r in rs} for p, rs in refs.items()}
    excluded = {p for p in projects if _NEEDS_WORKLOAD.search(text[p])}
    grew = True
    while grew:                                   # anything referencing an excluded project cannot build either
        more = {p for p in projects if p not in excluded and refs[p] & excluded}
        grew = bool(more)
        excluded |= more
    keep = [p.replace("/", "\\") for p in projects if p not in excluded]
    (ws / DOTNET_FILTER).write_text(json.dumps({"solution": {"path": f"../{slns[0].name}", "projects": keep}},
                                               indent=2) + "\n", encoding="utf-8")
    return DOTNET_FILTER, sorted(excluded)


def _normpath(p: str) -> str:
    parts: list[str] = []
    for seg in p.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in ("", "."):
            parts.append(seg)
    return "/".join(parts)


@dataclass(frozen=True)
class Task:
    id: str
    repo: str
    stack: str
    base: str
    gold: str
    prompt: str
    commit_subject: str
    gold_files: tuple[str, ...]
    gold_test_files: tuple[str, ...]


@dataclass(frozen=True)
class RepoSpec:
    id: str
    stack: str
    url: str
    local: str


def load(path: Path = TASKS_FILE) -> tuple[dict[str, RepoSpec], list[Task]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    repos = {rid: RepoSpec(rid, r["stack"], r["url"], r["local"]) for rid, r in data["repos"].items()}
    tasks = [Task(t["id"], t["repo"], repos[t["repo"]].stack, t["base"], t["gold"], t["prompt"], t["commit_subject"],
                  tuple(t["gold_files"]), tuple(t.get("gold_test_files", ()))) for t in data["task"]]
    return repos, tasks


def _git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check).stdout.strip()


def source_repo(spec: RepoSpec, *, clone: bool = False) -> Optional[Path]:
    """A full-history checkout for *spec*: the configured local path, else a clone in ``.repos`` (with clone=True)."""
    for cand in (ROOT / spec.local, REPO_CACHE / Path(spec.url).stem):
        if (cand / ".git").exists() and _git(cand, "rev-parse", "--is-shallow-repository") == "false":
            return cand
    if not clone:
        return None
    REPO_CACHE.mkdir(parents=True, exist_ok=True)
    dest = REPO_CACHE / Path(spec.url).stem
    subprocess.run(["git", "clone", "-q", spec.url, str(dest)], check=True)
    return dest


def validate(repos: dict[str, RepoSpec], tasks: list[Task]) -> list[str]:
    """Problems with the task set (empty = valid): ids, stacks, parent links, gold files vs the real diff."""
    from .build_tasks import leaks
    from .mine import IGNORE, SOURCE_EXT, TEST_PATH

    problems = []
    if len({t.id for t in tasks}) != len(tasks):
        problems.append("duplicate task ids")
    if len(tasks) < 15 or len({t.stack for t in tasks}) < 3:
        problems.append(f"need ≥ 15 tasks across ≥ 3 stacks (have {len(tasks)} / {len({t.stack for t in tasks})})")
    for t in tasks:
        src = source_repo(repos[t.repo])
        if src is None:
            problems.append(f"{t.id}: no full-history checkout of {t.repo} (run `python -m bench.agent fetch`)")
            continue
        if _git(src, "rev-parse", f"{t.gold}^", check=False) != t.base:
            problems.append(f"{t.id}: base is not the parent of gold")
            continue
        names = [p for p in _git(src, "diff", "--name-only", t.base, t.gold).splitlines()
                 if not IGNORE.search(p) and Path(p).suffix in SOURCE_EXT[t.stack]]
        if tuple(p for p in names if not TEST_PATH.search(p)) != t.gold_files:
            problems.append(f"{t.id}: gold_files do not match the commit diff")
        if any(leaks(t.prompt, p) for p in names):
            problems.append(f"{t.id}: prompt names a gold file")
    return problems


def make_workspace(spec: RepoSpec, task: Task, dest: Path) -> Path:
    """Files of ``task.base`` in a fresh one-commit repo: no history, so the answer cannot be looked up."""
    src = source_repo(spec, clone=True)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "--format=tar", task.base], cwd=src, capture_output=True,
                             check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    env_git = ["-c", "user.name=bench", "-c", "user.email=bench@localhost", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True)
    (dest / ".git" / "info" / "exclude").write_text("\n".join(HARNESS_FILES) + "\n")
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", *env_git, "commit", "-q", "-m", "base"], cwd=dest, check=True)
    return dest


def changed_files(ws: Path) -> list[str]:
    """Files the agent added, modified or deleted (harness files excluded).

    NUL-separated porcelain, parsed unstripped: a modified file's line starts with a space (` M path`), and
    stripping the output used to cut the first character off the first path (found in the M5.7.b dev runs).
    """
    out = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=ws,
                         capture_output=True, text=True, check=True).stdout
    entries = out.split("\0")
    files, i = [], 0
    while i < len(entries):
        entry, i = entries[i], i + 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            i += 1                    # -z: a rename/copy is followed by its source path
        if not any(path.startswith(h.rstrip("/")) for h in HARNESS_FILES):
            files.append(path)
    return sorted(set(files))
