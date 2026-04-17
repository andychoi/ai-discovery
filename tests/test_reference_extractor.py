"""Tests for reference extraction and hierarchy link inference."""

import pytest
from pathlib import Path

from ai_discovery.ingest.reference_extractor import (
    extract_doc_references,
    extract_req_ids,
    infer_links_from_hierarchy,
    enrich_references,
)
from ai_discovery.ingest.walker import IngestFile


class TestExtractDocReferences:
    def test_basic_refs(self):
        content = "This design references BRD-001 and SPEC-003."
        refs = extract_doc_references(content)
        assert "BRD-001" in refs
        assert "SPEC-003" in refs

    def test_multiple_prefixes(self):
        content = "See DES-012, ADR-001, and IF-007 for details."
        refs = extract_doc_references(content)
        assert set(refs) == {"DES-012", "ADR-001", "IF-007"}

    def test_dedup(self):
        content = "BRD-001 is referenced here. See also BRD-001."
        refs = extract_doc_references(content)
        assert refs.count("BRD-001") == 1

    def test_ignores_unknown_prefix(self):
        content = "ISO-9001 is not a doc ref. But BRD-001 is."
        refs = extract_doc_references(content)
        assert "ISO-9001" not in refs
        assert "BRD-001" in refs

    def test_empty_content(self):
        assert extract_doc_references("") == []


class TestExtractReqIds:
    def test_basic_req_ids(self):
        content = "Implements REQ-FR-001 and REQ-NFR-002."
        ids = extract_req_ids(content)
        assert "REQ-FR-001" in ids
        assert "REQ-NFR-002" in ids

    def test_short_form(self):
        content = "See FR-001, NFR-003, BR-005."
        ids = extract_req_ids(content)
        assert "FR-001" in ids
        assert "NFR-003" in ids
        assert "BR-005" in ids

    def test_dedup(self):
        content = "FR-001 and FR-001 again."
        ids = extract_req_ids(content)
        assert ids.count("FR-001") == 1

    def test_empty(self):
        assert extract_req_ids("No requirements here.") == []


class TestInferLinksFromHierarchy:
    def _make_file(self, doc_type, domain, doc_id):
        return IngestFile(
            path=Path(f"{doc_id}.md"), content="", title="",
            domain=domain, doc_type=doc_type, doc_id=doc_id,
        )

    def test_spec_links_to_design_and_brd(self):
        files = [
            self._make_file("brd", "payments", "BRD-001"),
            self._make_file("design", "payments", "DES-001"),
            self._make_file("spec", "payments", "SPEC-001"),
        ]
        infer_links_from_hierarchy(files)
        spec = files[2]
        assert "DES-001" in spec.links_to
        assert "BRD-001" in spec.links_to

    def test_interface_links_to_spec(self):
        files = [
            self._make_file("spec", "auth", "SPEC-001"),
            self._make_file("interface", "auth", "IF-001"),
        ]
        infer_links_from_hierarchy(files)
        assert "SPEC-001" in files[1].links_to

    def test_no_cross_domain_links(self):
        files = [
            self._make_file("brd", "payments", "BRD-001"),
            self._make_file("design", "auth", "DES-001"),  # different domain
        ]
        infer_links_from_hierarchy(files)
        assert files[1].links_to == []  # no BRD in auth domain

    def test_gap_analysis_links_to_brd(self):
        files = [
            self._make_file("brd", "core", "BRD-001"),
            self._make_file("gap-analysis", "core", "GAP-001"),
        ]
        infer_links_from_hierarchy(files)
        assert "BRD-001" in files[1].links_to


class TestEnrichReferences:
    def test_combined(self):
        f = IngestFile(
            path=Path("spec.md"),
            content="This spec implements BRD-001 and requirement FR-001.",
            title="Spec", domain="payments", doc_type="spec", doc_id="SPEC-001",
        )
        enrich_references([f])
        assert "BRD-001" in f.links_to
        assert "FR-001" in f.req_ids

    def test_self_reference_excluded(self):
        f = IngestFile(
            path=Path("brd.md"),
            content="Document BRD-001 defines the requirements.",
            title="BRD", domain="core", doc_type="brd", doc_id="BRD-001",
        )
        enrich_references([f])
        assert "BRD-001" not in f.links_to  # self-reference excluded
