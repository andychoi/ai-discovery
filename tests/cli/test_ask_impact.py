"""`discover ask` and `discover impact` (roadmap item M2.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo

runner = CliRunner()


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    r = _build_repo(tmp_path_factory.mktemp("ask") / "repo", GOLDEN_REPOS["java-spring-jsp"])
    assert runner.invoke(app, ["--json", "-C", str(r), "scan"]).exit_code == 0
    return r


def _cli(repo: Path, *args: str, json_out: bool = True):
    return runner.invoke(app, [*(["--json"] if json_out else []), "-C", str(repo), *args])


def test_ask_returns_hits_with_page_ids_and_citations(repo: Path) -> None:
    res = _cli(repo, "ask", "create order", "--limit", "5")
    assert res.exit_code == 0, res.output
    hits = json.loads(res.stdout)["hits"]
    assert 0 < len(hits) <= 5 and all(":" in h["id"] for h in hits)
    cited = [h for h in hits if h["citations"]]
    assert cited and all(c.split(":")[0].startswith("src/") for h in cited for c in h["citations"])
    human = _cli(repo, "ask", "create order", json_out=False)
    assert human.exit_code == 0 and "1. " in human.output


def test_ask_filters_kinds(repo: Path) -> None:
    hits = json.loads(_cli(repo, "ask", "order", "--kinds", "table").stdout)["hits"]
    assert hits and all(h["type"] == "table" for h in hits)


def test_impact_prints_the_m52_answer(repo: Path) -> None:
    res = _cli(repo, "impact", "com.example.service.OrderService.createOrder")
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["status"] == "ok" and out["data"]["items"][0]["node"] == "com.example.controller.OrderController.create"
    human = _cli(repo, "impact", "com.example.service.OrderService.createOrder", json_out=False)
    assert "OrderController.create" in human.output and "Unknowns:" in human.output


def test_impact_unknown_target_is_target_not_found(repo: Path) -> None:
    res = _cli(repo, "impact", "no.such.Thing")
    assert res.exit_code == 5 and json.loads(res.stdout)["error"]["reason"] == "target_not_found"
