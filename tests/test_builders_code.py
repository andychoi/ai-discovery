"""Structural code page builders (roadmap item M3.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_discovery.config import DiscoveryConfig
from ai_discovery.pages.builders.code import FOOTER
from ai_discovery.pages.model import save_pages, load_page
from ai_discovery.pages.registry import BuildContext, discover_builders, run_builders
from tests.golden.test_golden import GOLDEN_REPOS, _build_repo


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    """Keyless scan of the Java/Spring+JSP fixture into the v1 layout."""
    from ai_discovery.core.paths import ProjectLayout
    from ai_discovery.pipeline import run_pipeline

    repo = _build_repo(tmp_path_factory.mktemp("b") / "repo", GOLDEN_REPOS["java-spring-jsp"])
    layout = ProjectLayout.for_repo(repo)
    cfg = DiscoveryConfig(max_concurrent=1)
    cfg.no_llm = True
    run_pipeline(repo=str(repo), branch="main", project_slug=layout.slug, output_dir=layout.state_dir,
                 docs_root=layout.state_dir, config=cfg, layout=layout)
    conn = sqlite3.connect(layout.db_path)
    scan_id = conn.execute("SELECT scan_id FROM current_scan").fetchone()[0]
    return repo, conn, scan_id


def _build(scanned, **extra):
    repo, conn, sid = scanned
    ctx = BuildContext(conn=conn, scan_id=sid, repo=repo, commit="abc123", extra=dict(extra))
    return run_builders(ctx, [b for b in discover_builders() if b.name.startswith("code.")])


def test_page_families(scanned) -> None:
    pages = _build(scanned)
    types = {p.type for p in pages}
    assert types == {"overview", "module", "file", "symbol"}
    assert sum(p.type == "overview" for p in pages) == 1


def test_overview_facts_match_the_db(scanned) -> None:
    _, conn, sid = scanned
    ov = next(p for p in _build(scanned) if p.type == "overview")
    assert ov.page_id == "overview:system"
    assert ov.facts["symbols"] == conn.execute("SELECT COUNT(*) FROM code_nodes WHERE scan_id=?", (sid,)).fetchone()[0]
    assert ov.facts["edges"] == conn.execute("SELECT COUNT(*) FROM call_edges WHERE scan_id=?", (sid,)).fetchone()[0]
    assert ov.facts["languages"].get("java", 0) > 0
    assert ov.facts["screens"] > 0 and ov.facts["entry_points"] > 0
    for heading in ("Summary", "Domains", "Entry points", "External systems", "Modules"):
        assert any(s.heading == heading for s in ov.sections)


def test_every_citation_resolves(scanned) -> None:
    repo = scanned[0]
    pages = _build(scanned)
    cites = [c for p in pages for c in p.citations]
    assert cites, "expected file/symbol pages to cite code"
    for c in cites:
        lines = (repo / c.path).read_text(encoding="utf-8", errors="replace").splitlines()
        assert 1 <= c.line_start <= c.line_end <= len(lines), str(c)


def test_every_page_says_it_is_derived_from_structure(scanned) -> None:
    for p in _build(scanned):
        assert p.sections[-1].heading == "Provenance" and FOOTER in p.sections[-1].body_md


def test_builds_are_byte_identical(scanned) -> None:
    a = [p.to_row(1) for p in _build(scanned)]
    b = [p.to_row(1) for p in _build(scanned)]
    assert a == b


def test_file_page_cap_keeps_most_called_files(scanned) -> None:
    files = [p for p in _build(scanned, max_file_pages=2) if p.type == "file"]
    assert len(files) == 2
    all_files = [p for p in _build(scanned) if p.type == "file"]
    top = sorted(all_files, key=lambda p: (-p.facts["incoming_calls"], p.page_id))[:2]
    assert {p.page_id for p in files} == {p.page_id for p in top}


def test_symbol_pages_cover_entry_points_and_hubs(scanned) -> None:
    symbols = [p for p in _build(scanned, max_hub_symbols=3) if p.type == "symbol"]
    roles = {p.facts["role"] for p in symbols}
    assert "entry point" in roles
    hubs = [p for p in symbols if p.facts["role"] == "hub"]
    assert len(hubs) <= 3 and all(p.facts["called_by"] > 0 for p in hubs)


def test_module_tree_links_are_consistent(scanned) -> None:
    pages = {p.page_id: p for p in _build(scanned)}
    for p in pages.values():
        for link in p.links:
            if link.kind in ("child", "parent", "belongs_to") and link.dst.split(":")[0] in ("module", "file", "symbol"):
                assert link.dst in pages, f"{p.page_id} -> {link.dst} has no page"
    # every non-root module has a parent link
    for p in pages.values():
        if p.type == "module" and p.key != ".":
            assert any(l.kind == "parent" for l in p.links) or "/" not in p.key


def test_pages_round_trip_through_the_store(scanned) -> None:
    repo, conn, sid = scanned
    pages = _build(scanned)
    save_pages(conn, sid, pages)
    for p in pages[:20]:
        assert load_page(conn, sid, p.page_id) == p
    conn.rollback()
