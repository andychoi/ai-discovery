"""Verification plan (roadmap item M5.15): build/test detection, targeted tests, plan comparison."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery import api
from ai_discovery.cli import app as cli_app
from ai_discovery.query.verification import BuildRoot, _commands, _convention_tests, _nearest, _walk_build_files
from tests.golden.test_golden import _GIT_ENV

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "projects" / "spring-boot-app"
PLAN_DIR = Path(__file__).resolve().parents[1] / "plugin" / "skills" / "discover-change-plan" / "examples"
R = "src/main/java/com/example"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env={**os.environ, **_GIT_ENV}, check=True)


@pytest.fixture()
def project(tmp_path) -> api.Project:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".gitignore").write_text(".discovery/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "i")
    res = CliRunner().invoke(cli_app, ["-q", "-C", str(repo), "scan"])
    assert res.exit_code == 0, res.output
    plans = repo / ".discovery" / "plans" / "orders-status"
    plans.mkdir(parents=True)
    for f in ("IMPLEMENTATION_PLAN.md", "IMPACT_REPORT.md"):
        shutil.copy(PLAN_DIR / f, plans / f)
    return api.Project.open(repo)


def _edit(repo: Path, rel: str, old: str, new: str) -> None:
    p = repo / rel
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new))


def _apply_planned_change(repo: Path) -> None:
    _edit(repo, f"{R}/repository/OrderRepository.java", "Long> {\n}",
          "Long> {\n    java.util.List<Order> findByStatus(String status);\n}")
    _edit(repo, f"{R}/service/OrderService.java", "return orderRepository.findAll();",
          "return orderRepository.findAll(); // status filter added below")
    _edit(repo, f"{R}/controller/OrderController.java", "public List<Order> list() {\n        return orderService.getOrders();",
          "public List<Order> list(@RequestParam(required = false) String status) {\n        return orderService.getOrders();")


PLAN = ".discovery/plans/orders-status/IMPLEMENTATION_PLAN.md"


def test_planned_change_is_ok(project) -> None:
    repo = project.layout.repo
    _apply_planned_change(repo)
    t = repo / "src/test/java/com/example/controller/OrderControllerTest.java"
    t.parent.mkdir(parents=True)
    t.write_text("class OrderControllerTest {}\n")
    ans = api.verification_plan(project, working_tree=True, plan_path=PLAN)
    assert ans["status"] == "ok", ans
    d = ans["data"]
    assert d["verdict"] == "ok" and d["stop_reasons"] == []
    assert d["build"] == [{"stack": "java", "tool": "maven", "cwd": ".", "cmd": "mvn -q compile", "basis": "detected"}]
    assert d["tests"][0]["cmd"] == "mvn -q test"
    targeted = {t["test"]: t for t in d["targeted_tests"]}
    assert targeted["OrderControllerTest"]["cmd"] == "mvn -q test -Dtest=OrderControllerTest"
    assert targeted["OrderControllerTest"]["basis"] == "convention"
    assert {"kind": "endpoint", "page_id": "endpoint:GET-/api/orders", "method": "GET",
            "path": "/api/orders"} in d["smoke_targets"]
    assert d["plan"]["test_changes"] == ["src/test/java/com/example/controller/OrderControllerTest.java"]
    assert d["plan"]["unplanned_changes"] == [] and d["impacted_tests"]["status"] == "unknown"


def test_unplanned_change_stops(project) -> None:
    repo = project.layout.repo
    _apply_planned_change(repo)
    _edit(repo, f"{R}/service/ProductService.java", "public Product getProduct(Long id) {",
          "public Product getProduct(Long id) { // drive-by")
    d = api.verification_plan(project, working_tree=True, plan_path=PLAN)["data"]
    assert d["verdict"] == "stop"
    assert d["plan"]["unplanned_changes"] == [f"{R}/service/ProductService.java"]
    assert "com.example.controller.ProductController.get" in d["plan"]["unexpected_impact"]
    assert any("not pinned" in r for r in d["stop_reasons"]) and any("not mentioned" in r for r in d["stop_reasons"])


def test_without_plan_and_with_bad_plan_path(project) -> None:
    repo = project.layout.repo
    _apply_planned_change(repo)
    d = api.verification_plan(project, working_tree=True)["data"]
    assert d["plan"] is None and d["verdict"] == "ok"
    outside = repo.parent / "evil.md"
    outside.write_text("## Pinned target files\n- `x.java`\n")
    for bad in (str(outside), "../evil.md", f"{R}/service/OrderService.java"):
        ans = api.verification_plan(project, working_tree=True, plan_path=bad)
        assert ans["status"] == "not_found" and ans["data"]["reason"] == "plan_not_found", bad


def test_passes_through_change_impact_errors(project) -> None:
    assert api.verification_plan(project)["data"]["reason"] == "bad_arguments"
    assert api.verification_plan(project, range="--evil")["data"]["reason"] == "bad_range"


def test_build_detection_nearest_root_and_wrappers(tmp_path) -> None:
    repo = tmp_path
    (repo / "pom.xml").write_text("<project/>")
    (repo / "mvnw").write_text("")
    (repo / "svc-a").mkdir()
    (repo / "svc-a" / "pom.xml").write_text("<project/>")
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (repo / "web" / "tsconfig.json").write_text("{}")
    (repo / "api").mkdir()
    (repo / "api" / "Api.sln").write_text("")
    (repo / "tools").mkdir()
    (repo / "tools" / "pyproject.toml").write_text("")
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "node_modules" / "x" / "package.json").write_text("{}")
    roots = _walk_build_files(repo)
    assert {(r.kind, r.dir) for r in roots} == {("maven", "."), ("maven", "svc-a"), ("npm", "web"),
                                                ("dotnet", "api"), ("python", "tools")}
    assert _nearest(roots, "svc-a/src/main/java/A.java", "java").dir == "svc-a"
    assert _nearest(roots, "src/main/java/B.java", "java").dir == "."
    root = next(r for r in roots if r.dir == ".")
    assert _commands(repo, root) == ("./mvnw -q compile", "./mvnw -q test")
    web = next(r for r in roots if r.kind == "npm")
    assert _commands(repo, web) == ("npx tsc --noEmit", "npm test")
    dotnet = next(r for r in roots if r.kind == "dotnet")
    assert _commands(repo, dotnet) == ("dotnet build Api.sln", "dotnet test Api.sln")
    assert _commands(repo, BuildRoot("python", "python", "tools", "pyproject.toml"))[1] == "pytest -q"


def test_convention_tests() -> None:
    files = ["src/Foo.java", "app/orders.py", "web/cart.ts", "Svc/Billing.cs"]
    all_files = ["test/FooTest.java", "tests/test_orders.py", "web/cart.test.ts", "Svc.Tests/BillingTests.cs",
                 "other/Unrelated.java"]
    assert _convention_tests(files, all_files) == {
        "src/Foo.java": "test/FooTest.java", "app/orders.py": "tests/test_orders.py",
        "web/cart.ts": "web/cart.test.ts", "Svc/Billing.cs": "Svc.Tests/BillingTests.cs"}


def test_mcp_tool_is_opt_in_and_budgeted() -> None:
    from ai_discovery.mcp.registry import discover, select

    spec = discover()["get_verification_plan"]
    assert spec.opt_in and spec.budget.shed
    assert "get_verification_plan" not in [s.name for s in select("default")]
    assert "get_verification_plan" in [s.name for s in select("+get_verification_plan")]
