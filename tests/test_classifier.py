"""Tests for the markdown auto-classifier."""

import pytest

from ai_discovery.ingest.classifier import classify_markdown


class TestPhase0Classification:
    """Phase 0: as-is document types."""

    def test_api_headings(self):
        content = "# Payment Service\n\n## Endpoints\n\nGET /api/payments\n\n## Request Schemas\n\n## Response Schemas\n"
        assert classify_markdown(content) == "as-is-api"

    def test_api_from_filename(self):
        content = "# Overview\n\nSome general text about the API.\n"
        assert classify_markdown(content, filename="payment-api-docs.md") == "as-is-api"

    def test_schema_headings(self):
        content = "# Data Layer\n\n## Entity Definitions\n\n## Table: users\n\n## Relationships\n\n## Constraints\n"
        assert classify_markdown(content) == "as-is-schema"

    def test_asis_spec_headings(self):
        content = "# Auth Module\n\n## Use Cases\n\n## Business Rules\n\n## Processing Logic\n"
        assert classify_markdown(content) == "as-is-spec"

    def test_asis_headings(self):
        content = "# System X\n\n## Current Architecture Overview\n\n## Tech Debt\n\n## System Boundary\n"
        assert classify_markdown(content) == "as-is"


class TestPhase1Classification:
    """Phase 1: requirement doc types."""

    def test_brd_headings(self):
        content = "# Portal Redesign\n\n## Business Requirement\n\n## Stakeholder Analysis\n\n## ROI Estimate\n\n## Budget\n"
        assert classify_markdown(content) == "brd"

    def test_brd_from_filename(self):
        content = "# Requirements\n\nGeneral text.\n"
        assert classify_markdown(content, filename="brd-auth-portal.md") == "brd"

    def test_sla_nfr_headings(self):
        content = "# Quality Targets\n\n## SLA Definitions\n\n## Latency Requirements\n\n## Availability Target\n"
        assert classify_markdown(content) == "sla-nfr"

    def test_sla_from_filename(self):
        content = "# Targets\n\nGeneral text.\n"
        assert classify_markdown(content, filename="nfr-performance.md") == "sla-nfr"

    def test_gap_analysis_headings(self):
        content = "# Requirements Gap\n\n## Gap Analysis\n\n## Change Classification\n\n## Impact Assessment\n"
        assert classify_markdown(content) == "gap-analysis"


class TestPhase2Classification:
    """Phase 2: architecture & design doc types."""

    def test_design_headings(self):
        content = "# Auth Service\n\n## Architecture Diagram\n\n## Component Design\n\n## Data Flow\n\n## Technology Stack\n"
        assert classify_markdown(content) == "design"

    def test_adr_headings(self):
        content = "# ADR-001\n\n## Decision\n\n## Options Considered\n\n## Consequences\n"
        assert classify_markdown(content) == "adr"

    def test_adr_from_filename(self):
        content = "# Database Choice\n\nWe chose PostgreSQL.\n"
        assert classify_markdown(content, filename="adr-003-database.md") == "adr"

    def test_data_model_headings(self):
        content = "# User Data Model\n\n## Entity Relationship Diagram\n\n## Table Definitions\n\n## Index Strategy\n\n## PII Classification\n"
        assert classify_markdown(content) == "data-model"

    def test_security_design_headings(self):
        content = "# Security\n\n## Threat Model\n\n## Encryption Strategy\n\n## OWASP Checklist\n"
        assert classify_markdown(content) == "security-design"

    def test_ux_design_headings(self):
        content = "# Login Flow\n\n## Wireframe\n\n## User Journey\n\n## Accessibility (WCAG)\n"
        assert classify_markdown(content) == "ux-design"


class TestPhase3Classification:
    """Phase 3: technical spec doc types."""

    def test_spec_headings(self):
        content = "# Auth Spec\n\n## Validation Rules\n\n## Error Cases\n\n## Expected Output\n"
        assert classify_markdown(content) == "spec"

    def test_spec_from_filename(self):
        content = "# Overview\n\nTechnical details.\n"
        assert classify_markdown(content, filename="spec-auth-service.md") == "spec"

    def test_interface_headings(self):
        content = "# Payment API\n\n## Endpoint Definition\n\n## Request Schema\n\n## Response Schema\n\n## Rate Limits\n"
        assert classify_markdown(content) == "interface"

    def test_integration_headings(self):
        content = "# CRM Sync\n\n## Field Mapping\n\n## ETL Pipeline\n\n## Message Queue Config\n"
        assert classify_markdown(content) == "integration"


class TestEdgeCases:
    """Edge cases and disambiguation."""

    def test_fallback_to_default(self):
        content = "# Meeting Notes\n\nWe discussed the project.\n"
        assert classify_markdown(content) == "as-is"

    def test_custom_default(self):
        content = "# Random Notes\n\nNothing specific.\n"
        assert classify_markdown(content, default="brd") == "brd"

    def test_empty_content(self):
        assert classify_markdown("") == "as-is"

    def test_minimum_threshold(self):
        """Weak signals should fall back to default."""
        content = "# Something\n\nMentions an endpoint once.\n"
        assert classify_markdown(content) == "as-is"

    def test_case_insensitive(self):
        content = "# Module\n\n## THREAT MODEL\n\n## ENCRYPTION\n\n## OWASP\n"
        assert classify_markdown(content) == "security-design"

    def test_brd_vs_spec_disambiguation(self):
        """BRD has business/stakeholder; spec has technical input/output."""
        brd_content = "# Requirements\n\n## Business Requirement\n\n## Stakeholder Analysis\n\n## Budget\n"
        spec_content = "# Requirements\n\n## Validation Rules\n\n## Expected Output\n\n## Error Cases\n"
        assert classify_markdown(brd_content) == "brd"
        assert classify_markdown(spec_content) == "spec"
