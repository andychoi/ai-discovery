"""Tests for CRIT-3 screen-spec source verification (Phase-17 pass)."""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from ai_discovery.db import init_db
from ai_discovery.ai import screen_spec_generator as ssg
from ai_discovery.ai.screen_spec_generator import (
    _mapping_row_count, _patch_screen_doc, _screen_narrative, verify_screen_specs,
)
from ai_discovery.ai.self_review import ReviewClaim

_SCREEN_MD = """---
doc_id: proj-screen-orders
discovery_confidence: 0.5
content_provenance: llm-narrative-unverified
---

# Orders

> ⚠ **LLM-authored, not source-verified.** Purpose, rules, and field
> descriptions below are generated narrative.

## Purpose
View orders.
"""


def test_screen_narrative_assembles_prose():
    spec = {"purpose": "View orders", "rules_narrative": "only active",
            "rules": [{"name": "R1", "description": "active only"}],
            "fields_description": [{"name": "id", "description": "order id"}]}
    n = _screen_narrative(spec)
    assert "View orders" in n and "R1: active only" in n and "id: order id" in n


def test_patch_screen_doc_verified_flips_banner(tmp_path):
    p = tmp_path / "orders.md"; p.write_text(_SCREEN_MD)
    _patch_screen_doc(p, 0.83, verified=True)
    out = p.read_text()
    assert "discovery_confidence: 0.83" in out
    assert "content_provenance: llm-narrative-source-verified" in out
    assert "✓ **Source-verified.**" in out
    assert "not source-verified" not in out


def test_patch_screen_doc_unverified_updates_confidence_only(tmp_path):
    """With no real evidence (verified=False) the confidence updates but the
    banner stays honest — it must NOT claim source-verified."""
    p = tmp_path / "orders.md"; p.write_text(_SCREEN_MD)
    _patch_screen_doc(p, 0.5, verified=False)
    out = p.read_text()
    assert "discovery_confidence: 0.5" in out
    assert "content_provenance: llm-narrative-unverified" in out   # unchanged
    assert "⚠ **LLM-authored, not source-verified.**" in out        # banner kept


def _claims():
    """Realistic ReviewClaim mix: one of each verdict."""
    return [
        ReviewClaim(claim_text="Orders are listed", status="verified",
                    evidence="def list_orders(): ...", source_file="src/orders.py",
                    reason="supported"),
        ReviewClaim(claim_text="Orders can be deleted", status="contradicted",
                    evidence="# delete disabled", source_file="src/orders.py",
                    reason="contradicted"),
        ReviewClaim(claim_text="Totals include tax", status="unverified",
                    reason="not_retrieved"),
    ]


def _seed_screen_db(tmp_path, specs):
    """Create a DB with scan 1 and the given {screen_id: spec_dict} specs."""
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (1,'2026-01-01','running')")
    for screen_id, spec in specs.items():
        conn.execute(
            "INSERT INTO screen_specs (scan_id, screen_id, spec_json, confidence, created_at) "
            "VALUES (1, ?, ?, 0.5, '2026-01-01')", (screen_id, json.dumps(spec)))
    conn.commit(); conn.close()
    return db


def test_verify_screen_specs_end_to_end(monkeypatch, tmp_path):
    spec = {"screen_id": "orders", "purpose": "View orders", "rules": [], "fields_description": []}
    db = _seed_screen_db(tmp_path, {"orders": spec})

    screens = tmp_path / "screens"; screens.mkdir()
    (screens / "orders.md").write_text(_SCREEN_MD)

    import ai_discovery.ai.self_review as sr
    import ai_discovery.ai.rollup as rollup
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: _claims()[:1])
    monkeypatch.setattr(rollup, "blend_confidence", lambda n, s: 0.9)

    n = verify_screen_specs(db, 1, MagicMock(), tmp_path)
    assert n == 1
    # DB confidence updated
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT confidence FROM screen_specs WHERE screen_id='orders'").fetchone()[0] == 0.9
    conn.close()
    # markdown patched
    assert "discovery_confidence: 0.9" in (screens / "orders.md").read_text()


def test_screen_review_claims_table_exists(tmp_path):
    """Parity: screen claims need an audit trail like review_claims gives rollups."""
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(screen_review_claims)").fetchall()}
    conn.close()
    assert {"screen_spec_id", "claim_text", "status", "evidence", "source_file"} <= cols


def test_verify_screen_specs_persists_claims(monkeypatch, tmp_path):
    """Each claim verdict lands in screen_review_claims, FK'd to its spec row,
    and re-running verification replaces (not duplicates) the claims."""
    spec = {"screen_id": "orders", "purpose": "View orders"}
    db = _seed_screen_db(tmp_path, {"orders": spec})
    (tmp_path / "screens").mkdir()

    import ai_discovery.ai.self_review as sr
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: _claims())

    verify_screen_specs(db, 1, MagicMock(), tmp_path)
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT c.claim_text, c.status, c.source_file FROM screen_review_claims c "
        "JOIN screen_specs s ON s.id = c.screen_spec_id WHERE s.screen_id='orders' "
        "ORDER BY c.claim_text").fetchall()
    conn.close()
    assert [(r[1]) for r in rows] == ["verified", "contradicted", "unverified"]
    assert rows[1][2] == "src/orders.py"

    # idempotent on re-run (resume): same 3 claims, not 6
    verify_screen_specs(db, 1, MagicMock(), tmp_path)
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM screen_review_claims").fetchone()[0]
    conn.close()
    assert n == 3


def test_verify_screen_specs_annotates_doc(monkeypatch, tmp_path):
    """Contradicted/unverified claims must be reader-visible in the screen doc
    (Self-Review Notes), and re-running must not stack duplicate sections."""
    spec = {"screen_id": "orders", "purpose": "View orders"}
    db = _seed_screen_db(tmp_path, {"orders": spec})
    screens = tmp_path / "screens"; screens.mkdir()
    (screens / "orders.md").write_text(_SCREEN_MD)

    import ai_discovery.ai.self_review as sr
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: _claims())

    verify_screen_specs(db, 1, MagicMock(), tmp_path)
    out = (screens / "orders.md").read_text()
    assert "## Self-Review Notes" in out
    assert "Orders can be deleted" in out      # contradicted claim listed
    assert "Totals include tax" in out          # unverified claim listed
    assert "Orders are listed" not in out       # verified claims pass silently

    verify_screen_specs(db, 1, MagicMock(), tmp_path)
    out = (screens / "orders.md").read_text()
    assert out.count("## Self-Review Notes") == 1


def test_mapping_row_count():
    assert _mapping_row_count({
        "fe_api_calls": [{"m": "GET"}, {"m": "POST"}],
        "be_controllers": ["OrderController"],
        "be_services": None,
        "db_tables": ["orders"],
        "batch_jobs": [],
    }) == 4
    assert _mapping_row_count({}) == 0


def test_verify_screen_specs_blends_mapping_rows(monkeypatch, tmp_path):
    """Deterministic mapping rows count as AST-verified rows in the blend
    (parity with rollup verified_row_count): 4 mapping rows + 1 verified
    claim -> (4*1.0 + 1*1.0) / 5 = 1.0."""
    spec = {"screen_id": "orders", "purpose": "View orders",
            "fe_api_calls": [{"m": "GET"}, {"m": "POST"}],
            "be_controllers": ["OrderController"], "db_tables": ["orders"]}
    db = _seed_screen_db(tmp_path, {"orders": spec})
    (tmp_path / "screens").mkdir()

    import ai_discovery.ai.self_review as sr
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: _claims()[:1])

    verify_screen_specs(db, 1, MagicMock(), tmp_path)
    conn = sqlite3.connect(str(db))
    conf = conn.execute("SELECT confidence FROM screen_specs WHERE screen_id='orders'").fetchone()[0]
    conn.close()
    assert conf == 1.0


def test_verify_screen_specs_parallel_multiple_screens(monkeypatch, tmp_path):
    specs = {f"s{i}": {"screen_id": f"s{i}", "purpose": f"Screen {i}"} for i in range(3)}
    db = _seed_screen_db(tmp_path, specs)
    (tmp_path / "screens").mkdir()

    import ai_discovery.ai.self_review as sr
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: _claims()[:1])

    n = verify_screen_specs(db, 1, MagicMock(), tmp_path, max_workers=3)
    assert n == 3
    conn = sqlite3.connect(str(db))
    n_claims = conn.execute("SELECT COUNT(*) FROM screen_review_claims").fetchone()[0]
    conn.close()
    assert n_claims == 3


def test_verify_screen_specs_does_not_cap_claims_at_15(monkeypatch, tmp_path):
    """HIGH-2 parity: confidence must reflect more than a 15-claim sample."""
    spec = {"screen_id": "orders", "purpose": "View orders"}
    db = _seed_screen_db(tmp_path, {"orders": spec})
    (tmp_path / "screens").mkdir()

    seen = {}
    import ai_discovery.ai.self_review as sr

    def _capture(*a, **k):
        seen.update(k)
        return _claims()[:1]

    monkeypatch.setattr(sr, "review_document", _capture)
    verify_screen_specs(db, 1, MagicMock(), tmp_path)
    assert seen.get("max_claims", 50) >= 50
