"""Tests for CRIT-3 screen-spec source verification (Phase-17 pass)."""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from ai_discovery.db import init_db
from ai_discovery.ai import screen_spec_generator as ssg
from ai_discovery.ai.screen_spec_generator import (
    _patch_screen_doc, _screen_narrative, verify_screen_specs,
)

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


def test_verify_screen_specs_end_to_end(monkeypatch, tmp_path):
    db = tmp_path / "d.db"; init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO scan_runs (id, started_at, status) VALUES (1,'2026-01-01','running')")
    spec = {"screen_id": "orders", "purpose": "View orders", "rules": [], "fields_description": []}
    conn.execute(
        "INSERT INTO screen_specs (scan_id, screen_id, spec_json, confidence, created_at) "
        "VALUES (1, 'orders', ?, 0.5, '2026-01-01')", (json.dumps(spec),))
    conn.commit(); conn.close()

    screens = tmp_path / "screens"; screens.mkdir()
    (screens / "orders.md").write_text(_SCREEN_MD)

    monkeypatch.setattr(ssg, "review_document", lambda *a, **k: ["c"], raising=False)
    import ai_discovery.ai.self_review as sr
    import ai_discovery.ai.rollup as rollup
    monkeypatch.setattr(sr, "review_document", lambda *a, **k: ["c"])
    monkeypatch.setattr(sr, "get_review_summary", lambda c: {"verified": 1, "unverified": 0, "contradicted": 0, "total": 1})
    monkeypatch.setattr(rollup, "blend_confidence", lambda n, s: 0.9)

    n = verify_screen_specs(db, 1, MagicMock(), tmp_path)
    assert n == 1
    # DB confidence updated
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT confidence FROM screen_specs WHERE screen_id='orders'").fetchone()[0] == 0.9
    conn.close()
    # markdown patched
    assert "discovery_confidence: 0.9" in (screens / "orders.md").read_text()
