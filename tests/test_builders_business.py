"""Structural business page builders (roadmap item M3.3)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.pages.registry import BuildContext, discover_builders, run_builders

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    """Keyless scan of the Spring fixture (menu.json screens, JPA entities, endpoints)."""
    import subprocess

    from ai_discovery.core.paths import ProjectLayout
    from ai_discovery.pipeline import run_pipeline
    from tests.golden.test_golden import _GIT_ENV

    import os
    repo = tmp_path_factory.mktemp("biz") / "repo"
    shutil.copytree(FIXTURES / "projects" / "spring-boot-app", repo)
    env = {**os.environ, **_GIT_ENV}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "i"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True)
    layout = ProjectLayout.for_repo(repo)
    cfg = DiscoveryConfig(max_concurrent=1)
    cfg.no_llm = True
    run_pipeline(repo=str(repo), branch="main", project_slug=layout.slug, output_dir=layout.state_dir,
                 docs_root=layout.state_dir, config=cfg, layout=layout)
    conn = sqlite3.connect(layout.db_path)
    sid = conn.execute("SELECT scan_id FROM current_scan").fetchone()[0]
    _add_synthetic_fsm_and_interface(conn, sid, repo)
    return repo, conn, sid


def _add_synthetic_fsm_and_interface(conn, sid: int, repo: Path) -> None:
    """The fixture has no state machine or external system; add one of each."""
    conn.execute(
        "INSERT INTO entity_state_machines (scan_id, entity, entity_id, states_json, fields_json, "
        "source_files_json, confidence, metadata_json) VALUES (?, 'orders', 'jpa::orders', ?, ?, ?, 0.9, ?)",
        (sid, json.dumps(["NEW", "PAID", "CANCELLED"]), json.dumps(["status"]),
         json.dumps([str(repo / "src/main/java/com/example/entity/Order.java")]), json.dumps({"entity_kind": "lifecycle"})))
    for frm, to, trig, guard in [("NEW", "PAID", "OrderService.pay", "amount > 0"),
                                 ("NEW", "CANCELLED", "OrderService.cancel", None)]:
        conn.execute(
            "INSERT INTO state_transitions (scan_id, entity, entity_id, field, from_state, to_state, "
            "trigger_function, guard_expr, confidence) VALUES (?, 'orders', 'jpa::orders', 'status', ?, ?, ?, ?, 0.9)",
            (sid, frm, to, trig, guard))
    conn.execute(
        "INSERT INTO code_nodes (scan_id, file_path, language, node_type, name, qualified_name, line_start, "
        "line_end, framework_hints) VALUES (?, ?, 'java', 'external_system', 'payment-gateway', "
        "'external:payment-gateway', 1, 1, ?)",
        (sid, str(repo / "src/main/java/com/example/service/OrderService.java"), json.dumps({"kind": "http"})))
    conn.commit()


def _pages(scanned, prefix: str | None = None):
    repo, conn, sid = scanned
    ctx = BuildContext(conn=conn, scan_id=sid, repo=repo, commit="abc123")
    builders = [b for b in discover_builders() if prefix is None or b.name.startswith(prefix)]
    return run_builders(ctx, builders)


def _by_id(scanned) -> dict:
    return {p.page_id: p for p in _pages(scanned)}


def test_business_page_families(scanned) -> None:
    types = {p.type for p in _pages(scanned, "business.")}
    assert types == {"screen", "endpoint", "entity", "table", "interface"}


def test_screen_page_links_menu_and_tables(scanned) -> None:
    pages = _by_id(scanned)
    screen = pages["screen:products-list"]
    assert screen.title == "Products" and screen.facts["menu_path"] == ["Catalog Management", "Products"]
    assert "table:products" in {link.dst for link in screen.links}
    assert any(s.heading == "Data" for s in screen.sections)


def test_table_page_columns_relationships_and_users(scanned) -> None:
    orders = _by_id(scanned)["table:orders"]
    assert {"id", "status", "customer"} <= set(orders.facts["columns"])
    assert set(orders.facts["references"]) == {"customers", "order_items"}
    assert orders.facts["screens"], "orders is used by screens in menu.json"
    assert orders.citations, "JPA relationship lines are cited"
    # reverse side
    assert "orders" in _by_id(scanned)["table:customers"].facts["referenced_by"]


def test_endpoint_pages(scanned) -> None:
    endpoints = [p for p in _pages(scanned, "business.endpoints")]
    keys = {p.title for p in endpoints}
    assert "GET /api/customers" in keys and "POST /api/orders" in keys
    assert all(p.citations for p in endpoints)
    assert all(any(link.kind == "implements" for link in p.links) for p in endpoints)  # handler symbol pages exist


def test_entity_page_has_fsm_dmn_and_ears(scanned) -> None:
    entity = _by_id(scanned)["entity:jpa::orders"]
    body = entity.body_md()
    assert "```mermaid" in body and "stateDiagram-v2" in body and "s_NEW --> s_PAID : pay" in body
    assert "amount > 0" in body                       # DMN row for the guarded transition
    assert "Requirements (EARS)" in body and "PAID" in body
    assert entity.facts["transitions"] == 2 and entity.confidence == 0.9
    assert "table:orders" in {link.dst for link in entity.links}


def test_interface_page(scanned) -> None:
    iface = _by_id(scanned)["interface:payment-gateway"]
    assert iface.facts["kinds"] == ["http"] and iface.citations


def test_all_links_and_citations_resolve(scanned) -> None:
    repo = scanned[0]
    pages = _by_id(scanned)
    for p in pages.values():
        for link in p.links:
            assert link.dst in pages, f"{p.page_id} -> {link.dst} has no page"
        for c in p.citations:
            lines = (repo / c.path).read_text(encoding="utf-8", errors="replace").splitlines()
            assert c.line_end <= len(lines), str(c)


def test_business_pages_are_deterministic(scanned) -> None:
    assert [p.to_row(1) for p in _pages(scanned, "business.")] == [p.to_row(1) for p in _pages(scanned, "business.")]
