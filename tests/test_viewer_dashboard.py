"""Tests for viewer.dashboard — pure functions, no HTTP."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.db import init_db
from ai_discovery.viewer.dashboard import (
    HIGH_CONF_THRESHOLD,
    LOW_CONF_THRESHOLD,
    MAX_LOW_CONF_RATIO,
    build_summary,
    confidence_histogram,
    discover_artifacts,
    doc_tree,
    quality_status,
    weakest_docs,
)


@pytest.fixture()
def scan_out(tmp_path: Path) -> tuple[Path, Path, str]:
    """Build a minimal scan output: DB with known edges/docs + docs_root markdown."""
    slug = "demo"
    output_dir = tmp_path / "discovery-output"
    docs_root = tmp_path / "docs"
    slug_dir = output_dir / slug
    slug_dir.mkdir(parents=True)

    db = slug_dir / f"discovery-{slug}.db"
    init_db(db)

    # Two scans? No — just one. Insert a scan_run to satisfy FK.
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO scan_runs (repo_path, project_slug, started_at, status) "
        "VALUES ('/tmp', ?, '2026-04-21T00:00:00', 'done')",
        (slug,),
    )
    scan_id = conn.execute("SELECT id FROM scan_runs").fetchone()[0]

    # A single caller node so call_edges FK is satisfied.
    conn.execute(
        """INSERT INTO code_nodes
           (scan_id, file_path, language, node_type, name, qualified_name)
           VALUES (?, 'a.py', 'python', 'function', 'f', 'a.f')""",
        (scan_id,),
    )
    caller_id = conn.execute("SELECT id FROM code_nodes").fetchone()[0]

    # Confidence distribution chosen so buckets are predictable:
    # 10 at 0.95, 5 at 0.75, 5 at 0.55  →  total 20
    #   resolved (>=0.85) = 10  →  50% (below 85% target → FAIL)
    #   low_conf (<0.65)  = 5   →  25% (above 15% target → FAIL)
    edge_confidences = [0.95] * 10 + [0.75] * 5 + [0.55] * 5
    for c in edge_confidences:
        conn.execute(
            "INSERT INTO call_edges (scan_id, caller_id, callee_name, confidence) "
            "VALUES (?, ?, 'x', ?)",
            (scan_id, caller_id, c),
        )

    # Three docs: worst has 7 unverified claims, then 3, then 0.
    docs = [
        ("demo-orders-as-is", "Orders — As-Is", "as-is", "orders", 0.6, 7),
        ("demo-payments-as-is", "Payments — As-Is", "as-is", "payments", 0.8, 3),
        ("demo-orders-spec", "Orders — Spec", "spec", "orders", 0.9, 0),
    ]
    for doc_id, title, doc_type, domain, conf, unv in docs:
        conn.execute(
            """INSERT INTO generated_docs
               (scan_id, domain, doc_type, doc_id, title, content_md, confidence,
                unverified_claims, created_at)
               VALUES (?, ?, ?, ?, ?, '', ?, ?, '2026-04-21T00:00:00')""",
            (scan_id, domain, doc_type, doc_id, title, conf, unv),
        )
    conn.commit()
    conn.close()

    # Canonical JSON artifacts — touch a couple so presence checks have signal.
    (slug_dir / "entity_state_machines.json").write_text("[]")
    (slug_dir / "entity_backbone.mmd").write_text("graph LR\nA-->B\n")
    (slug_dir / "bpmn").mkdir()
    (slug_dir / "bpmn" / "scenario1.bpmn").write_text("<?xml?>")

    # Rendered markdown under docs_root.
    asis = docs_root / slug / "ASIS"
    asis.mkdir(parents=True)
    (asis / "demo-orders-as-is.md").write_text(
        '---\ntitle: "Orders — As-Is"\ndiscovery_confidence: 0.6\n'
        'unverified_claims: 7\n---\n\n# body\n'
    )
    (asis / "demo-no-frontmatter.md").write_text("# just a body\n")

    return output_dir, docs_root, slug


def test_confidence_histogram_sums_to_total(scan_out):
    output_dir, _, slug = scan_out
    db = output_dir / slug / f"discovery-{slug}.db"
    buckets = confidence_histogram(db)
    assert len(buckets) == 10
    assert sum(b.count for b in buckets) == 20
    # All 10 of our 0.95 edges land in the last bucket (0.9–1.0 inclusive).
    assert buckets[-1].count == 10
    # 5 at 0.75 → bucket [0.7, 0.8).
    assert [b for b in buckets if b.lo == 0.7][0].count == 5
    # 5 at 0.55 → bucket [0.5, 0.6).
    assert [b for b in buckets if b.lo == 0.5][0].count == 5


def test_quality_status_flags_both_failures(scan_out):
    output_dir, _, slug = scan_out
    db = output_dir / slug / f"discovery-{slug}.db"
    q = quality_status(db)
    assert q is not None
    assert q.total_edges == 20
    assert q.resolved_edges == 10
    assert q.low_conf_edges == 5
    assert q.resolution_ratio == pytest.approx(0.5)
    assert q.low_conf_ratio == pytest.approx(0.25)
    assert q.meets_resolution_target is False
    assert q.meets_low_conf_target is False


def test_quality_status_none_when_no_edges(tmp_path: Path):
    db = tmp_path / "empty.db"
    init_db(db)
    assert quality_status(db) is None


def test_weakest_docs_ranks_by_unverified_then_confidence(scan_out):
    output_dir, _, slug = scan_out
    db = output_dir / slug / f"discovery-{slug}.db"
    docs = weakest_docs(db, limit=10)
    assert [d.doc_id for d in docs] == [
        "demo-orders-as-is",    # 7 unverified
        "demo-payments-as-is",  # 3 unverified
        "demo-orders-spec",     # 0 unverified, highest confidence → last
    ]
    assert docs[0].bucket == "ASIS"    # doc_type="as-is"
    assert docs[2].bucket == "SPEC"    # doc_type="spec" (canonical prefix from doc_generator)


def test_discover_artifacts_reports_presence_and_dir_counts(scan_out):
    output_dir, _, slug = scan_out
    arts = {a.key: a for a in discover_artifacts(output_dir, slug)}
    assert arts["db"].exists
    assert arts["fsms"].exists
    assert arts["cross_links"].exists is False          # never written in fixture
    assert arts["backbone_mmd"].exists
    assert arts["bpmn"].exists
    assert arts["bpmn"].file_count == 1
    assert arts["dmn"].exists is False


def test_doc_tree_extracts_frontmatter_confidence(scan_out):
    _, docs_root, slug = scan_out
    tree = doc_tree(docs_root, slug)
    assert "ASIS" in tree
    names = {e.rel_path for e in tree["ASIS"]}
    assert "ASIS/demo-orders-as-is.md" in names
    assert "ASIS/demo-no-frontmatter.md" in names
    with_fm = next(
        e for e in tree["ASIS"] if e.rel_path == "ASIS/demo-orders-as-is.md"
    )
    assert with_fm.confidence == 0.6
    assert with_fm.unverified_claims == 7
    without = next(
        e for e in tree["ASIS"] if e.rel_path == "ASIS/demo-no-frontmatter.md"
    )
    assert without.confidence is None
    assert without.unverified_claims is None


def test_build_summary_combines_all_sections(scan_out):
    output_dir, docs_root, slug = scan_out
    data = build_summary(output_dir, docs_root, slug)
    assert data.slug == slug
    assert data.db_path is not None
    assert len(data.histogram) == 10
    assert data.quality is not None
    assert data.quality.total_edges == 20
    assert len(data.weakest_docs) == 3
    assert "ASIS" in data.doc_tree


def test_build_summary_handles_missing_db(tmp_path: Path):
    """If DB doesn't exist (scan never started), return a degraded-but-valid summary."""
    data = build_summary(tmp_path / "nope", tmp_path / "docs", "ghost")
    assert data.db_path is None
    assert data.histogram == []
    assert data.quality is None
    assert data.weakest_docs == []


def test_thresholds_match_claude_md_targets():
    """Guard against accidental drift from the documented targets."""
    assert HIGH_CONF_THRESHOLD == 0.85
    assert LOW_CONF_THRESHOLD == 0.65
    assert MAX_LOW_CONF_RATIO == 0.15
