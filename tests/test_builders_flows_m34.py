"""Flow, domain, onboarding, batch-job and stored-procedure page builders (roadmap item M3.4)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ai_discovery.graph import origins as O
from ai_discovery.pages.model import canonical_json
from ai_discovery.pages.registry import BuildContext, run_builders

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PROC_REPO = {
    "db/procedures.sql": (
        "CREATE PROCEDURE dbo.usp_AuditOrder @OrderId INT AS\nBEGIN\n"
        "  INSERT INTO AuditLog (OrderId) VALUES (@OrderId);\nEND\nGO\n\n"
        "CREATE PROCEDURE dbo.usp_PlaceOrder @CustomerId INT AS\nBEGIN\n"
        "  SELECT Id FROM Customers WHERE Id = @CustomerId;\n"
        "  INSERT INTO Orders (CustomerId) VALUES (@CustomerId);\n"
        "  EXEC dbo.usp_AuditOrder @OrderId = 1;\nEND\nGO\n"),
    "src/Shop/Shop.csproj": "<Project />",
    "src/Shop/OrderRepository.cs": (
        "using System.Data;\nusing System.Data.SqlClient;\nnamespace Shop;\n"
        "public class OrderRepository {\n  public void Place(int customerId) {\n"
        "    using var cmd = new SqlCommand(\"dbo.usp_PlaceOrder\");\n"
        "    cmd.CommandType = CommandType.StoredProcedure;\n    cmd.ExecuteNonQuery();\n  }\n}\n"),
}


def _git_scan(repo: Path) -> tuple[sqlite3.Connection, int]:
    from ai_discovery.config import DiscoveryConfig
    from ai_discovery.core.paths import ProjectLayout
    from ai_discovery.pipeline import run_pipeline
    from tests.golden.test_golden import _GIT_ENV

    env = {**os.environ, **_GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True)
    layout = ProjectLayout.for_repo(repo)
    cfg = DiscoveryConfig(max_concurrent=1)
    cfg.no_llm = True
    run_pipeline(repo=str(repo), branch="main", project_slug=layout.slug, output_dir=layout.state_dir,
                 docs_root=layout.state_dir, config=cfg, layout=layout)
    conn = sqlite3.connect(layout.db_path)
    return conn, conn.execute("SELECT scan_id FROM current_scan").fetchone()[0]


@pytest.fixture(scope="module")
def spring(tmp_path_factory):
    """Spring fixture: 9 endpoints + an @Scheduled job; one external system added on a flow's path."""
    repo = tmp_path_factory.mktemp("flows") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    conn, sid = _git_scan(repo)
    ids = dict(conn.execute("SELECT qualified_name, id FROM code_nodes WHERE scan_id = ?", (sid,)).fetchall())
    conn.execute("INSERT INTO code_nodes (scan_id, file_path, language, node_type, name, qualified_name, line_start, "
                 "line_end, framework_hints) VALUES (?, '', 'external', 'external_system', 'Stripe', "
                 "'external::stripe', 0, 0, '{\"kind\": \"payment\"}')", (sid,))
    stripe = conn.execute("SELECT id FROM code_nodes WHERE qualified_name = 'external::stripe'").fetchone()[0]
    conn.execute("INSERT INTO call_edges (scan_id, caller_id, callee_id, callee_name, edge_type, confidence, "
                 "resolved_by, edge_kind, evidence_kind) VALUES (?, ?, ?, 'charge', 'external_call', 0.9, "
                 "'external_client', 'calls_external', 'framework')",
                 (sid, ids["com.example.service.OrderService.createOrder"], stripe))
    pages = run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))
    return repo, conn, sid, {p.page_id: p for p in pages}


@pytest.fixture(scope="module")
def procs(tmp_path_factory):
    repo = tmp_path_factory.mktemp("procs") / "repo"
    for rel, text in PROC_REPO.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    conn, sid = _git_scan(repo)
    return {p.page_id: p for p in run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))}


def _of(pages, kind):
    return {pid: p for pid, p in pages.items() if p.type == kind}


# ── flows ───────────────────────────────────────────────────────────────────

def test_builders_flows_one_flow_page_per_entry_point(spring) -> None:
    _, _, _, pages = spring
    flows = _of(pages, "flow")
    entries = {p.facts["entry"].rsplit(".", 2)[-2] + "." + p.facts["entry"].rsplit(".", 1)[-1] for p in flows.values()}
    assert entries == {"CustomerController.list", "CustomerController.get", "OrderController.list",
                       "OrderController.get", "OrderController.create", "ProductController.list",
                       "ProductController.get", "ProductController.create", "ProductController.update",
                       "OrderExportJob.exportOrders"}
    for p in flows.values():
        assert [s.heading for s in p.sections][:4] == ["Trigger", "Main path", "Also reached", "Where the trace stops"]
        assert set(p.facts["stop_reasons"].values()) <= O.STOP_REASONS
        assert p.facts["main_path"][0] == p.facts["entry"]
    job = next(p for p in flows.values() if p.facts["entry"].endswith("exportOrders"))
    assert (job.facts["kind"], job.facts["trigger"]) == ("scheduled", "SCHEDULED")


def test_builders_flows_follow_resolved_edges_only(spring) -> None:
    _, conn, sid, pages = spring
    followable = {(a, b) for a, b, origin in conn.execute(
        "SELECT c1.qualified_name, c2.qualified_name, e.resolved_by FROM call_edges e "
        "JOIN code_nodes c1 ON c1.id = e.caller_id JOIN code_nodes c2 ON c2.id = e.callee_id WHERE e.scan_id = ?",
        (sid,)) if origin in O.ORIGINS and O.ORIGINS[origin].confidence >= O.FLOW_MIN_CONFIDENCE
        and O.ORIGINS[origin].edge_kind in O.FLOW_EDGE_KINDS}
    for p in _of(pages, "flow").values():
        path = p.facts["main_path"]
        assert all((a, b) in followable for a, b in zip(path, path[1:])), p.page_id


def test_builders_flows_external_boundary_and_data(spring) -> None:
    _, _, _, pages = spring
    create = next(p for p in _of(pages, "flow").values() if p.facts["entry"].endswith("OrderController.create"))
    assert create.facts["stop_reasons"].get("external::stripe") == "external_boundary"
    assert create.facts["externals"] == ["Stripe"]
    assert any(link.dst == "interface:Stripe" for link in create.links)


def test_builders_flows_links_resolve_and_citations_exist(spring, procs) -> None:
    for repo_pages, repo in ((spring[3], spring[0]), (procs, None)):
        live = set(repo_pages)
        dangling = sorted({(p.page_id, link.dst) for p in repo_pages.values() for link in p.links
                           if link.dst not in live})
        assert dangling == []
        if repo is not None:
            for p in repo_pages.values():
                for c in p.citations:
                    lines = (repo / c.path).read_text().splitlines()
                    assert c.line_end <= len(lines), (p.page_id, str(c))


# ── domains + onboarding ───────────────────────────────────────────────────

def test_builders_flows_domain_and_onboarding_pages(spring) -> None:
    _, conn, sid, pages = spring
    domains = {r[0] for r in conn.execute("SELECT name FROM domains WHERE scan_id = ?", (sid,))}
    assert set(_of(pages, "domain")) == {f"domain:{d}" for d in domains}
    flows = _of(pages, "flow")
    for d in _of(pages, "domain").values():
        for qn in d.facts["entry_points"]:
            assert any(f.facts["entry"] == qn for f in flows.values())
    tours = _of(pages, "onboarding")
    assert tours, "at least one domain is big enough for a tour"
    entry_qns = {f.facts["entry"] for f in flows.values()}
    for t in tours.values():
        assert len(t.facts["steps"]) >= 3 and t.facts["steps"][0] in entry_qns      # entry point first
        assert f"domain:{t.facts['domain']}" in pages


# ── batch jobs + stored procedures: rendered when the data exists, omitted otherwise ─────────────

def test_builders_flows_batch_job_page(spring, procs) -> None:
    jobs = _of(spring[3], "batch_job")
    assert list(jobs) == ["batch_job:com.example.batch.OrderExportJob.exportOrders"]
    job = next(iter(jobs.values()))
    assert job.facts["schedule"] == 'cron = "0 0 2 * * *"' and job.facts["kind"] == "scheduled"
    assert _of(procs, "batch_job") == {}


def test_builders_flows_stored_proc_pages(spring, procs) -> None:
    assert _of(spring[3], "stored_proc") == {}
    sp = _of(procs, "stored_proc")
    assert set(sp) == {"stored_proc:usp_auditorder", "stored_proc:usp_placeorder"}
    place = sp["stored_proc:usp_placeorder"]
    assert place.facts["callers"] == ["Shop.OrderRepository.Place"]
    assert (place.facts["reads"], place.facts["writes"], place.facts["calls"]) == (
        ["Customers"], ["Orders"], ["usp_auditorder"])
    assert any(link.dst == "stored_proc:usp_auditorder" and link.kind == "calls" for link in place.links)
    audit = sp["stored_proc:usp_auditorder"]
    assert audit.facts["writes"] == ["AuditLog"]
    assert any(link.dst == "stored_proc:usp_placeorder" and link.kind == "called_by" for link in audit.links)
    assert [str(c) for c in audit.citations] == ["db/procedures.sql:1-5"]      # the whole CREATE PROCEDURE


def test_builders_flows_are_deterministic(spring) -> None:
    repo, conn, sid, pages = spring
    again = {p.page_id: p for p in run_builders(BuildContext(conn=conn, scan_id=sid, repo=repo))}
    kinds = ("flow", "domain", "onboarding", "batch_job")
    assert {k: canonical_json(p.to_row(sid)) for k, p in again.items() if p.type in kinds} == \
        {k: canonical_json(p.to_row(sid)) for k, p in pages.items() if p.type in kinds}
