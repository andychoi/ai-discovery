"""Verification plan for a change (roadmap item M5.15).

Answers "how do I check this change?" without running anything: build and test
commands for the stacks the change touches (detected from build files, nearest
project directory per changed file), targeted tests (reached through the call
graph, or found by naming convention), smoke targets (endpoints and screens the
change reaches), and — given an ``IMPLEMENTATION_PLAN.md`` — changes and impact
the plan did not foresee. ai-discovery never executes these commands; the agent
does, with its own tools.

Every command carries ``basis``: ``detected`` (from build files) or, in future,
``configured`` (config v2 ``verify:`` section — not yet available).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from .answer import Answer, not_indexed
from .impact import _TEST_PATH, DEFAULT_DEPTH, change_impact
from .session import Project

_SKIP_DIRS = {".git", ".discovery", "node_modules", "target", "build", "bin", "obj", "dist", ".venv", "venv",
              "__pycache__", ".gradle", ".idea"}
_MAX_DEPTH = 6
_EXT_STACK = {".java": "java", ".kt": "java", ".jsp": "java", ".jspx": "java", ".xml": None,
              ".cs": "dotnet", ".vb": "dotnet", ".aspx": "dotnet", ".ascx": "dotnet", ".cshtml": "dotnet",
              ".razor": "dotnet", ".ts": "node", ".tsx": "node", ".js": "node", ".jsx": "node", ".vue": "node",
              ".py": "python", ".abap": "abap"}
_PATH_IN_MD = re.compile(r"`([^`\s]+\.[A-Za-z0-9]+)`|^\s*(?:\d+\.|[-*])\s+([^\s`]+\.[A-Za-z0-9]+)", re.M)


@dataclass(frozen=True)
class BuildRoot:
    kind: str          # maven | gradle | dotnet | npm | python | abaplint
    stack: str         # java | dotnet | node | python | abap
    dir: str           # repo-relative POSIX dir ("." for the root)
    marker: str        # the build file that identified it


def _walk_build_files(repo: Path) -> list[BuildRoot]:
    roots: list[BuildRoot] = []

    def visit(d: Path, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        names = {e.name for e in entries}
        rel = d.relative_to(repo).as_posix() or "."
        if "pom.xml" in names:
            roots.append(BuildRoot("maven", "java", rel, "pom.xml"))
        if names & {"build.gradle", "build.gradle.kts"}:
            roots.append(BuildRoot("gradle", "java", rel, sorted(names & {"build.gradle", "build.gradle.kts"})[0]))
        sln = sorted(n for n in names if n.endswith(".sln"))
        proj = sorted(n for n in names if n.endswith((".csproj", ".vbproj")))
        if sln or proj:
            roots.append(BuildRoot("dotnet", "dotnet", rel, (sln or proj)[0]))
        if "package.json" in names:
            roots.append(BuildRoot("npm", "node", rel, "package.json"))
        if names & {"pyproject.toml", "setup.py", "setup.cfg"}:
            roots.append(BuildRoot("python", "python", rel, sorted(names & {"pyproject.toml", "setup.py",
                                                                             "setup.cfg"})[0]))
        if ".abapgit.xml" in names:
            roots.append(BuildRoot("abaplint", "abap", rel, ".abapgit.xml"))
        for e in entries:
            if e.is_dir() and not e.is_symlink() and e.name not in _SKIP_DIRS and not e.name.startswith("."):
                visit(e, depth + 1)

    visit(repo, 0)
    return roots


def _nearest(roots: list[BuildRoot], path: str, stack: str) -> Optional[BuildRoot]:
    """Deepest build root of *stack* that contains *path*."""
    best: Optional[BuildRoot] = None
    parts = PurePosixPath(path).parts
    for r in roots:
        if r.stack != stack:
            continue
        rp = () if r.dir == "." else PurePosixPath(r.dir).parts
        if parts[:len(rp)] == rp and (best is None or len(rp) > len(PurePosixPath(best.dir).parts)):
            best = r
    return best


def _wrapper(repo: Path, root: BuildRoot, name: str, fallback: str) -> str:
    return f"./{name}" if (repo / root.dir / name).exists() else fallback


def _npm_scripts(repo: Path, root: BuildRoot) -> dict:
    try:
        return json.loads((repo / root.dir / "package.json").read_text(encoding="utf-8")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _commands(repo: Path, root: BuildRoot) -> tuple[Optional[str], Optional[str]]:
    """(build, test) for one build root."""
    if root.kind == "maven":
        mvn = _wrapper(repo, root, "mvnw", "mvn")
        return f"{mvn} -q compile", f"{mvn} -q test"
    if root.kind == "gradle":
        gradle = _wrapper(repo, root, "gradlew", "gradle")
        return f"{gradle} compileJava", f"{gradle} test"
    if root.kind == "dotnet":
        return f"dotnet build {root.marker}", f"dotnet test {root.marker}"
    if root.kind == "npm":
        scripts = _npm_scripts(repo, root)
        build = "npm run build" if "build" in scripts else (
            "npx tsc --noEmit" if (repo / root.dir / "tsconfig.json").exists() else None)
        test = "npm test" if "test" in scripts else None
        return build, test
    if root.kind == "python":
        return "python -m compileall -q .", "pytest -q"
    if root.kind == "abaplint":
        return "npx @abaplint/cli (Unverified: needs abaplint.json)", None
    return None, None


def _targeted(repo: Path, root: BuildRoot, test_file: str) -> Optional[str]:
    stem = PurePosixPath(test_file).stem
    if root.kind == "maven":
        return f"{_wrapper(repo, root, 'mvnw', 'mvn')} -q test -Dtest={stem}"
    if root.kind == "gradle":
        return f"{_wrapper(repo, root, 'gradlew', 'gradle')} test --tests '*{stem}'"
    if root.kind == "dotnet":
        return f"dotnet test {root.marker} --filter FullyQualifiedName~{stem}"
    if root.kind == "python":
        rel = test_file if root.dir == "." else str(PurePosixPath(test_file).relative_to(root.dir))
        return f"pytest -q {rel}"
    if root.kind == "npm":
        return f"npm test -- {PurePosixPath(test_file).name}"
    return None


def _convention_tests(files: list[str], all_files: list[str]) -> dict[str, str]:
    """Changed file → test file found by naming convention (FooTest/FooTests/FooSpec/test_foo/foo.test)."""
    index: dict[str, list[str]] = {}
    for f in all_files:
        index.setdefault(PurePosixPath(f).name.lower(), []).append(f)
    out: dict[str, str] = {}
    for f in files:
        p = PurePosixPath(f)
        stem, ext = p.stem, p.suffix
        candidates = [f"{stem}Test{ext}", f"{stem}Tests{ext}", f"{stem}Spec{ext}", f"{stem}IT{ext}",
                      f"test_{stem}{ext}", f"{stem}_test{ext}", f"{stem}.test{ext}", f"{stem}.spec{ext}"]
        for c in candidates:
            hits = index.get(c.lower())
            if hits:
                out[f] = sorted(hits, key=len)[0]
                break
    return out


def _repo_files(repo: Path) -> list[str]:
    out: list[str] = []

    def visit(d: Path, depth: int) -> None:
        if depth > 12 or len(out) > 50_000:
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir():
                if e.name not in _SKIP_DIRS and not e.name.startswith(".") and not e.is_symlink():
                    visit(e, depth + 1)
            elif e.suffix in _EXT_STACK:
                out.append(e.relative_to(repo).as_posix())

    visit(repo, 0)
    return out


def _plan(repo: Path, state_dir: Path, plan_path: str) -> tuple[Optional[dict], Optional[str]]:
    """Pinned files + names mentioned in the plan (and a sibling IMPACT_REPORT.md)."""
    p = Path(plan_path)
    p = (repo / p) if not p.is_absolute() else p
    try:
        p = p.resolve()
    except OSError:
        return None, "plan_not_found"
    allowed = [repo.resolve(), state_dir.resolve()]
    if p.suffix.lower() != ".md" or not any(a == p or a in p.parents for a in allowed) or not p.is_file():
        return None, "plan_not_found"
    text = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^##\s+Pinned target files.*?$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    pinned = sorted({(a or b).lstrip("./") for a, b in _PATH_IN_MD.findall(m.group(1))}) if m else []
    mentioned = text
    report = p.parent / "IMPACT_REPORT.md"
    if report.is_file():
        mentioned += "\n" + report.read_text(encoding="utf-8", errors="replace")
    return {"path": str(p), "pinned": pinned, "has_impact_report": report.is_file(), "_text": mentioned}, None


def _mentioned(node: str, text: str) -> bool:
    short = node.split(":", 1)[-1]
    parts = short.split(".")
    keys = {short, ".".join(parts[-2:]) if len(parts) > 1 else short}
    return any(k and k in text for k in keys)


def _matches_pinned(path: str, pinned: Iterable[str]) -> bool:
    return any(path == p or path.endswith("/" + p) or p.endswith("/" + path) for p in pinned)


def verification_plan(project: Project, *, diff: Optional[str] = None, range: Optional[str] = None,
                      working_tree: bool = False, plan_path: Optional[str] = None,
                      depth: int = DEFAULT_DEPTH) -> dict:
    ci = change_impact(project, diff=diff, range=range, working_tree=working_tree, depth=depth, max_items=100)
    if ci["status"] != "ok":
        return ci
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        repo = r.repo
        data = ci["data"]
        ans = Answer(meta=ci["meta"])
        changed = [f["path"] for f in data["changed_files"] if f.get("path")]
        roots = _walk_build_files(repo) if repo else []
        # build + test commands, per nearest build root of each changed file's stack
        used: dict[tuple, BuildRoot] = {}
        unmapped: list[str] = []
        for f in changed:
            stack = _EXT_STACK.get(PurePosixPath(f).suffix.lower())
            root = _nearest(roots, f, stack) if stack else None
            if root is None and stack is None:
                # config/markup: attribute to the deepest root of any stack that contains it
                root = max((x for s in {"java", "dotnet", "node", "python"} if (x := _nearest(roots, f, s))),
                           key=lambda x: len(PurePosixPath(x.dir).parts), default=None)
            if root is None:
                unmapped.append(f)
                continue
            used[(root.kind, root.dir)] = root
        build, tests = [], []
        for root in sorted(used.values(), key=lambda x: (x.dir, x.kind)):
            b, t = _commands(repo, root)
            if b:
                build.append({"stack": root.stack, "tool": root.kind, "cwd": root.dir, "cmd": b, "basis": "detected"})
            if t:
                tests.append({"stack": root.stack, "tool": root.kind, "cwd": root.dir, "cmd": t, "basis": "detected"})
        # targeted tests: graph-reached tests, then naming convention for changed files
        targeted = []
        impact = data["impact"]
        cited = {i["node"]: i.get("citation") for i in impact["items"]}
        for qn in impact.get("tests_reached", []):
            c = cited.get(qn) or {}
            path = c.get("path")
            root = _nearest(roots, path, _EXT_STACK.get(PurePosixPath(path).suffix.lower(), "")) if path else None
            if path and root:
                targeted.append({"test": qn, "path": path, "cwd": root.dir, "cmd": _targeted(repo, root, path),
                                 "basis": "graph"})
        if repo:
            for src, test_file in sorted(_convention_tests(changed, _repo_files(repo)).items()):
                if any(t["path"] == test_file for t in targeted):
                    continue
                stack = _EXT_STACK.get(PurePosixPath(test_file).suffix.lower())
                root = _nearest(roots, test_file, stack) if stack else None
                if root:
                    targeted.append({"test": PurePosixPath(test_file).stem, "path": test_file, "cwd": root.dir,
                                     "cmd": _targeted(repo, root, test_file), "basis": "convention", "for": src})
        # smoke targets: endpoints and screens the change reaches
        smoke = []
        for ep in impact.get("entry_points_reached", []):
            if ep.startswith("endpoint:"):
                row = r.page_row(ep)
                facts = loads_facts(row["facts_json"]) if row else {}
                smoke.append({"kind": "endpoint", "page_id": ep, "method": facts.get("method"),
                              "path": facts.get("path")})
            elif ep.startswith("screen:"):
                row = r.page_row(ep)
                facts = loads_facts(row["facts_json"]) if row else {}
                smoke.append({"kind": "screen", "page_id": ep, "menu_path": facts.get("menu_path") or [],
                              "title": row["title"] if row else None})
            else:
                smoke.append({"kind": ep.split(":", 1)[0], "id": ep.split(":", 1)[-1]})
        plan_out = None
        stop_reasons: list[str] = []
        if plan_path:
            plan, err = _plan(repo, project.layout.state_dir, plan_path) if repo else (None, "plan_not_found")
            if err:
                return Answer(status="not_found", data={"target": plan_path, "reason": err, "did_you_mean": []},
                              meta=ci["meta"], remedy="pass the path of an IMPLEMENTATION_PLAN.md inside the repo or "
                                                      ".discovery/").to_dict()
            outside = [f for f in changed if not _matches_pinned(f, plan["pinned"])]
            test_changes = [f for f in outside if _TEST_PATH.search(f)]   # adding tests is expected, not drift
            unplanned = [f for f in outside if f not in test_changes]
            unexpected = [i["node"] for i in impact["items"] if not _mentioned(i["node"], plan["_text"])]
            plan_out = {"path": plan["path"], "pinned": plan["pinned"], "has_impact_report": plan["has_impact_report"],
                        "unplanned_changes": unplanned, "test_changes": test_changes, "unexpected_impact": ans.cap("unexpected_impact", unexpected,
                                                                                      50)}
            if not plan["pinned"]:
                stop_reasons.append("plan has no '## Pinned target files' section with file paths")
            if unplanned:
                stop_reasons.append(f"{len(unplanned)} changed file(s) are not pinned in the plan")
            if unexpected:
                stop_reasons.append(f"{len(unexpected)} impacted node(s) are not mentioned in the plan or report")
        if unmapped:
            stop_reasons.append(f"no build file found for {len(unmapped)} changed file(s)")
        ans.data = {
            "source": data["source"], "changed_files": changed,
            "changed_symbols": [s["id"] for s in data["changed_symbols"]],
            "build": build, "tests": tests, "targeted_tests": targeted,
            "impacted_tests": data["tests_to_run"], "smoke_targets": smoke,
            "unknowns": impact.get("unknowns", []), "unmapped_files": unmapped,
            "plan": plan_out, "verdict": "stop" if stop_reasons else "ok", "stop_reasons": stop_reasons,
            "note": "ai-discovery does not run these commands; run them and report pass/fail with output.",
        }
        ans.totals.update({"build": len(build), "tests": len(tests), "targeted_tests": len(targeted),
                           "smoke_targets": len(smoke)})
        return ans.to_dict()


def loads_facts(facts_json: Optional[str]) -> dict:
    try:
        return (json.loads(facts_json) if facts_json else {}).get("facts", {}) or {}
    except (TypeError, ValueError):
        return {}
