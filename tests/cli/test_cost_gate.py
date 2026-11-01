"""Cost gate + `scan --dry-run` (roadmap item M2.4)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ai_discovery.cli import app
from ai_discovery.cli import cost_gate
from ai_discovery.config import CostGateConfig, DiscoveryConfig
from ai_discovery.core.errors import DiscoveryError
from ai_discovery.llm.estimate import Estimate, ScanShape
from ai_discovery.stages.resolve import _adds_prose
from tests.golden.stub_llm import StubLLM
from tests.golden.test_golden import GOLDEN_REPOS, RECORDED, _build_repo

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = _build_repo(tmp_path / "repo", GOLDEN_REPOS["java-spring-jsp"])
    (r / ".discovery").mkdir()
    # a metered provider so the quote is not $0; the gate asks from $0 here to exercise the prompt paths
    (r / ".discovery" / "config.yaml").write_text("provider: bedrock\ncost_gate:\n  ask_above_usd: 0\n")
    return r


def _cli(repo: Path, *args: str, json_out: bool = True):
    return runner.invoke(app, [*(["--json"] if json_out else []), "-C", str(repo), "scan", *args])


def _out(res) -> dict:
    return json.loads(res.stdout)


def _rows(db: Path, sql: str) -> list:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _stub_llm():
    """The golden harness's seams: a recorded LLM, no embeddings, no self-review persistence."""
    from ai_discovery.ai.self_review import ReviewClaim

    stack = ExitStack()
    stub = StubLLM.from_file(RECORDED)
    for target, kw in (("ai_discovery.ai.llm_client.LLMClient", {"return_value": stub}),
                       ("ai_discovery.rag.embedder.embed_chunks", {"return_value": {"embedded": 0, "dim": 256}}),
                       ("ai_discovery.rag.retriever.search", {"return_value": []}),
                       ("ai_discovery.ai.self_review.review_document",
                        {"return_value": [ReviewClaim(claim_text="stub", status="verified")]}),
                       ("ai_discovery.ai.self_review.persist_claims", {})):
        stack.enter_context(patch(target, **kw))
    return stack, stub


def _quote(usd: float) -> cost_gate.Quote:
    return cost_gate.Quote(Estimate(usd, usd * 0.5, usd * 2), ScanShape(), "index", 1)


# ── decision table ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("usd,expected", [(1.99, "auto"), (2.0, "approved"), (25.0, "approved"), (30.0, "approved")])
def test_cost_gate_thresholds_and_defaults(usd, expected) -> None:
    cfg = DiscoveryConfig()
    asked = []
    got = cost_gate.decide(_quote(usd), cfg, yes=False, can_ask=True,
                           ask=lambda prompt, default: asked.append(default) or True)
    assert got == expected
    assert asked == ([] if usd < 2 else [usd <= 25])          # default Yes up to $25, No above


def test_cost_gate_thresholds_are_configurable() -> None:
    cfg = DiscoveryConfig(cost_gate=CostGateConfig(ask_above_usd=10, default_no_above_usd=50))
    assert cost_gate.verdict(9.99, cfg) == cost_gate.RUN
    assert cost_gate.verdict(50, cfg) == cost_gate.ASK_YES and cost_gate.verdict(50.01, cfg) == cost_gate.ASK_NO


def test_cost_gate_decline_and_no_tty_and_yes() -> None:
    cfg = DiscoveryConfig()
    with pytest.raises(DiscoveryError) as declined:
        cost_gate.decide(_quote(5), cfg, yes=False, can_ask=True, ask=lambda p, d: False)
    assert (declined.value.reason, declined.value.exit_code) == ("cost_gate_declined", 2)
    with pytest.raises(DiscoveryError) as no_tty:
        cost_gate.decide(_quote(5), cfg, yes=False, can_ask=False)
    assert (no_tty.value.reason, no_tty.value.exit_code) == ("cost_gate_no_tty", 2)
    assert "--yes" in no_tty.value.hint and "--no-prose" in no_tty.value.hint
    assert cost_gate.decide(_quote(500), cfg, yes=True, can_ask=False) == "pre_approved"
    assert cost_gate.decide(_quote(1), cfg, yes=False, can_ask=False) == "auto"   # under $2: never asks


# ── --dry-run ────────────────────────────────────────────────────────────────

def test_cost_gate_dry_run_writes_nothing_and_prints_stages(repo: Path) -> None:
    before = sorted(p.relative_to(repo) for p in repo.rglob("*"))
    res = _cli(repo, "--dry-run")
    assert res.exit_code == 0, res.output
    data = _out(res)
    assert data["dry_run"] is True and data["estimate"]["source"] == "structure_scan"
    stages = {s["stage"] for s in data["estimate"]["stages"]}
    assert {"summaries", "scenario_flows", "rollup"} <= stages
    est = data["estimate"]
    assert 0 < est["low_usd"] < est["expected_usd"] < est["high_usd"]
    assert data["estimate"]["shape"]["scenarios"] == 10          # 9 endpoints + 1 @Scheduled job (M6.12)
    assert sorted(p.relative_to(repo) for p in repo.rglob("*")) == before   # no state, no lock file


def test_cost_gate_dry_run_reads_the_stored_scan_read_only(repo: Path) -> None:
    assert _cli(repo, "--no-prose").exit_code == 0
    db = repo / ".discovery" / "discovery.db"
    runs = _rows(db, "SELECT COUNT(*) FROM scan_runs")
    res = _cli(repo, "--dry-run")
    assert res.exit_code == 0 and _out(res)["estimate"]["source"] == "index"
    assert _rows(db, "SELECT COUNT(*) FROM scan_runs") == runs


def test_cost_gate_dry_run_human_table(repo: Path) -> None:
    res = _cli(repo, "--dry-run", json_out=False)
    assert res.exit_code == 0
    assert "Prose cost estimate" in res.stdout and "scenario_flows" in res.stdout and "cost gate:" in res.stdout


def test_cost_gate_dry_run_rejects_resume_and_only(repo: Path) -> None:
    res = _cli(repo, "--dry-run", "--resume")
    assert res.exit_code == 2 and _out(res)["error"]["reason"] == "contradictory_flags"


# ── gated scans ──────────────────────────────────────────────────────────────

def test_cost_gate_no_tty_exits_2_after_keeping_the_structure_pass(repo: Path) -> None:
    res = _cli(repo, "--prose")                        # CliRunner is not a TTY; no --yes
    assert res.exit_code == 2
    err = _out(res)["error"]
    assert err["reason"] == "cost_gate_no_tty" and "--yes" in err["hint"] and "--no-prose" in err["hint"]
    db = repo / ".discovery" / "discovery.db"
    (cfg_json,) = _rows(db, "SELECT config_json FROM scan_runs")[0]
    assert json.loads(cfg_json)["no_llm"] is True     # only the structure pass ran — nothing was spent
    assert _rows(db, "SELECT COUNT(*) FROM llm_costs")[0][0] == 0


def test_cost_gate_declined_is_recorded(repo: Path) -> None:
    assert _cli(repo, "--no-prose").exit_code == 0
    with patch.object(cost_gate, "interactive", return_value=True), \
            patch.object(cost_gate, "_confirm", return_value=False):
        res = _cli(repo, "--prose", json_out=False)
    assert res.exit_code == 2
    rows = _rows(repo / ".discovery" / "discovery.db", "SELECT decision, source, actual_usd FROM cost_estimates")
    assert rows == [("declined", "index", None)]


def test_cost_gate_structure_then_prose_runs_and_records_estimate_vs_actual(repo: Path) -> None:
    assert _cli(repo, "--no-prose").exit_code == 0
    stack, stub = _stub_llm()
    with stack:
        res = _cli(repo, "--prose", "--yes")
    assert res.exit_code == 0, res.output
    out = _out(res)
    # the guard no longer reports a structure-only scan of the same commit as "already scanned"
    assert out["status"] == "completed" and out["prose"] is True and stub.calls
    assert out["cost_gate"]["decision"] == "pre_approved" and out["cost_gate"]["source"] == "index"
    db = repo / ".discovery" / "discovery.db"
    (scan_id, source, decision, expected, actual, actual_json), = _rows(
        db, "SELECT scan_id, source, decision, expected_usd, actual_usd, actual_json FROM cost_estimates")
    assert (source, decision) == ("index", "pre_approved") and expected > 0
    assert scan_id == _rows(db, "SELECT MAX(id) FROM scan_runs")[0][0]
    assert actual == pytest.approx(_rows(db, f"SELECT COALESCE(SUM(est_usd), 0) FROM llm_costs "
                                             f"WHERE scan_id = {scan_id}")[0][0])
    assert isinstance(json.loads(actual_json), list)
    # HEAD now has its prose: the next prose run does not ask (it reports unchanged and spends nothing)
    assert _cli(repo, "--prose").exit_code == 0


def test_cost_gate_yes_without_a_stored_scan_quotes_after_the_run(repo: Path) -> None:
    stack, _ = _stub_llm()
    with stack:
        res = _cli(repo, "--prose", "--yes")
    assert res.exit_code == 0, res.output
    rows = _rows(repo / ".discovery" / "discovery.db", "SELECT source, decision FROM cost_estimates")
    assert rows == [("post_run", "pre_approved")]


def test_cost_gate_adds_prose_only_over_a_structure_only_run() -> None:
    structure = {"config_json": json.dumps({"no_llm": True})}
    prose = {"config_json": json.dumps({"no_llm": False})}
    assert _adds_prose(structure, SimpleNamespace(no_llm=False))
    assert not _adds_prose(structure, SimpleNamespace(no_llm=True))
    assert not _adds_prose(prose, SimpleNamespace(no_llm=False))
