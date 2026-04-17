"""Tests for the ingest walker — file discovery, title extraction, domain inference."""

import pytest
from pathlib import Path

from ai_discovery.ingest.walker import extract_title, infer_domain, batch_files, IngestFile
from ai_discovery.ingest.frontmatter import make_doc_id, generate_frontmatter, prepend_frontmatter


class TestExtractTitle:
    def test_from_h1(self):
        assert extract_title("# Payment Service\n\nSome text", "file.md") == "Payment Service"

    def test_from_filename(self):
        assert extract_title("No heading here", "payment-service.md") == "Payment Service"

    def test_from_filename_underscores(self):
        assert extract_title("No heading", "payment_service_v2.md") == "Payment Service V2"


class TestInferDomain:
    def test_subdirectory(self, tmp_path):
        d = tmp_path / "payments"
        d.mkdir()
        f = d / "service.md"
        assert infer_domain(f, tmp_path) == "payments"

    def test_nested_subdirectory(self, tmp_path):
        d = tmp_path / "core" / "auth"
        d.mkdir(parents=True)
        f = d / "login.md"
        assert infer_domain(f, tmp_path) == "core"

    def test_root_level(self, tmp_path):
        f = tmp_path / "overview.md"
        assert infer_domain(f, tmp_path) == "general"


class TestMakeDocId:
    def test_asis(self):
        assert make_doc_id("as-is", 1) == "ASIS-001"

    def test_brd(self):
        assert make_doc_id("brd", 3) == "BRD-003"

    def test_design(self):
        assert make_doc_id("design", 12) == "DES-012"

    def test_spec(self):
        assert make_doc_id("spec", 42) == "SPEC-042"

    def test_interface(self):
        assert make_doc_id("interface", 7) == "IF-007"

    def test_unknown_type(self):
        assert make_doc_id("unknown", 1) == "DOC-001"


class TestFrontmatter:
    def test_generate_phase0(self):
        fm = generate_frontmatter("ASIS-001", "Test Title", "as-is", tags=["payments"])
        assert "doc_id: ASIS-001" in fm
        assert 'title: "Test Title"' in fm
        assert "status: Draft" in fm
        assert "tags: [payments]" in fm
        assert "source_repo:" in fm  # Phase 0 field
        assert "scan_date:" in fm
        assert fm.startswith("---\n")
        assert fm.endswith("---\n")

    def test_generate_phase2(self):
        fm = generate_frontmatter("DES-001", "Design Doc", "design", links_to=["BRD-001"], req_ids=["FR-001"])
        assert "doc_id: DES-001" in fm
        assert "links_to: [BRD-001]" in fm
        assert "req_ids: [FR-001]" in fm
        assert "last_audit_status:" in fm  # Design/spec field
        assert "source_repo:" not in fm  # NOT a Phase 0 field

    def test_generate_deprecated_status(self):
        fm = generate_frontmatter("BRD-001", "Old BRD", "brd", status="Deprecated")
        assert "status: Deprecated" in fm

    def test_prepend_strips_existing(self):
        content = "---\nold: value\n---\n\n# Title\n\nBody"
        result = prepend_frontmatter(content, "---\nnew: value\n---\n")
        assert "old: value" not in result
        assert "new: value" in result
        assert "# Title" in result

    def test_prepend_no_existing(self):
        content = "# Title\n\nBody"
        result = prepend_frontmatter(content, "---\nkey: val\n---\n")
        assert result.startswith("---\nkey: val\n---\n")
        assert "# Title" in result


class TestBatchFiles:
    def test_batch_size(self):
        files = [IngestFile(path=Path(f"f{i}.md"), content="", title="", domain="", doc_type="as-is") for i in range(5)]
        batches = batch_files(files, batch_size=2)
        assert len(batches) == 3
        assert len(batches[0]) == 2
        assert len(batches[2]) == 1
