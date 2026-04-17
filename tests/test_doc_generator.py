"""Tests for app.output.doc_generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.ai.rollup import RollupResult
from ai_discovery.output.doc_generator import _slugify, render_doc, write_docs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rollup(
    domain: str = "Auth Service",
    doc_type: str = "as-is",
    title: str = "Auth Service — As-Is Assessment",
    content_md: str = "## Overview\nThis is the auth service.",
    confidence: float = 0.85,
) -> RollupResult:
    return RollupResult(
        domain=domain,
        doc_type=doc_type,
        title=title,
        content_md=content_md,
        confidence=confidence,
        tokens_in=100,
        tokens_out=200,
        model="test-model",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_underscores(self):
        assert _slugify("auth_service") == "auth-service"

    def test_special_chars(self):
        assert _slugify("Auth Service (v2)") == "auth-service-v2"

    def test_strips_leading_trailing(self):
        assert _slugify("--hello--") == "hello"

    def test_empty(self):
        assert _slugify("") == ""


class TestRenderDoc:
    def test_has_frontmatter(self):
        rollup = _make_rollup()
        doc_id, md = render_doc(rollup, "myproj")
        assert md.startswith("---\n")
        assert "---" in md[4:]  # closing frontmatter delimiter

    def test_frontmatter_fields(self):
        rollup = _make_rollup()
        _, md = render_doc(
            rollup,
            "myproj",
            repo_url="https://github.com/example/repo",
            repo_commit="abc123",
            unverified_claims=3,
        )
        assert "doc_id: myproj-auth-service-as-is" in md
        assert 'title: "Auth Service' in md
        assert "status: Draft" in md
        assert "tags: [discovery-scan]" in md
        assert "source: discovery_scan" in md
        assert "scan_date:" in md
        assert "repo_url: https://github.com/example/repo" in md
        assert "repo_commit: abc123" in md
        assert "discovery_confidence: 0.85" in md
        assert "unverified_claims: 3" in md

    def test_doc_id_format(self):
        rollup = _make_rollup(domain="Payment Gateway", doc_type="spec")
        doc_id, _ = render_doc(rollup, "myproj")
        assert doc_id == "myproj-payment-gateway-spec"

    def test_includes_content(self):
        rollup = _make_rollup(content_md="## Architecture\nMicroservice-based auth.")
        _, md = render_doc(rollup, "myproj")
        assert "## Architecture" in md
        assert "Microservice-based auth." in md


class TestWriteDocs:
    def test_creates_files(self, tmp_path: Path):
        rollups = [_make_rollup()]
        results = write_docs(rollups, tmp_path, "myproj")
        assert len(results) == 1
        file_path = Path(results[0]["file_path"])
        assert file_path.exists()
        # Folder uses the DocHub type prefix (ASIS), not the doc_type string.
        assert file_path.parent.name == "ASIS"
        assert file_path.name == "myproj-auth-service-as-is.md"

    def test_all_doc_types(self, tmp_path: Path):
        rollups = [
            _make_rollup(doc_type="as-is", title="D — As-Is"),
            _make_rollup(doc_type="spec", title="D — Spec"),
            _make_rollup(doc_type="interface", title="D — Interface"),
            _make_rollup(doc_type="data-model", title="D — Data Model"),
        ]
        results = write_docs(rollups, tmp_path, "myproj")
        assert len(results) == 4
        created_types = {r["doc_type"] for r in results}
        assert created_types == {"as-is", "spec", "interface", "data-model"}
        for r in results:
            assert Path(r["file_path"]).exists()

    def test_result_dict_keys(self, tmp_path: Path):
        rollups = [_make_rollup()]
        results = write_docs(rollups, tmp_path, "myproj")
        r = results[0]
        assert set(r.keys()) == {"doc_id", "doc_type", "domain", "file_path", "confidence"}
        assert r["doc_id"] == "myproj-auth-service-as-is"
        assert r["doc_type"] == "as-is"
        assert r["domain"] == "Auth Service"
        assert r["confidence"] == 0.85
