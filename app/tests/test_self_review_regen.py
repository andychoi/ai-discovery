"""Tests for iterative self-review with focused re-generation."""
from unittest.mock import MagicMock, patch

import pytest

from discovery.ai.self_review import ReviewClaim


def test_regenerate_section_replaces_unverified_content():
    """Focused re-gen replaces a section that had unverified claims."""
    from discovery.ai.self_review import regenerate_sections

    original_md = (
        "## Authentication\n"
        "The AuthService uses OAuth2 for token management.\n\n"
        "## Data Model\n"
        "Users are stored in the users table with a foreign key to roles.\n"
    )

    claims = [
        ReviewClaim(
            claim_text="AuthService uses OAuth2 for token management",
            status="unverified",
            source_file="",
        ),
    ]

    mock_client = MagicMock()
    mock_client.invoke.return_value = MagicMock(
        text="The AuthService handles login via session cookies stored in Redis.",
        tokens_in=100, tokens_out=50, model="test",
    )

    result = regenerate_sections(original_md, claims, mock_client, db_path=None)

    # The Data Model section should be unchanged
    assert "Users are stored in the users table" in result
    # The LLM was called to re-generate the Authentication section
    assert mock_client.invoke.called


def test_regenerate_sections_no_unverified_claims():
    """When all claims are verified, returns original unchanged."""
    from discovery.ai.self_review import regenerate_sections

    original_md = "## Auth\nEverything is fine.\n"
    claims = [
        ReviewClaim(claim_text="Everything is fine", status="verified"),
    ]
    mock_client = MagicMock()

    result = regenerate_sections(original_md, claims, mock_client, db_path=None)

    assert result == original_md
    assert not mock_client.invoke.called
