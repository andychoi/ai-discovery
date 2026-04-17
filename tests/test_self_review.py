"""Tests for the self-review claim extraction and verification pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_discovery.ai.llm_client import LLMResponse
from ai_discovery.ai.self_review import (
    ReviewClaim,
    extract_claims,
    get_review_summary,
    persist_claims,
    review_document,
    verify_claim,
)
from ai_discovery.db import get_conn, init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm(text: str, tier: str = "tier1") -> MagicMock:
    client = MagicMock()
    client.invoke.return_value = LLMResponse(
        text=text,
        tokens_in=100,
        tokens_out=50,
        model="test-model",
        tier=tier,
    )
    return client


def _mock_rag_results(results: list[dict]):
    """Return a patcher for rag.retriever.search."""
    return patch(
        "app.rag.retriever.search",
        return_value=results,
    )


# ---------------------------------------------------------------------------
# Tests: extract_claims
# ---------------------------------------------------------------------------


def test_extract_claims_returns_list():
    """Mock LLM returns JSON array of claims, verify parsed correctly."""
    claims_json = json.dumps([
        "The OrderService class handles payment processing",
        "The /api/users endpoint accepts POST requests",
        "The User entity has a foreign key to Organization",
    ])
    client = _mock_llm(claims_json)

    claims = extract_claims("# Some Doc\nContent here.", client)

    assert len(claims) == 3
    assert "OrderService" in claims[0]
    assert "/api/users" in claims[1]
    assert "foreign key" in claims[2]
    client.invoke.assert_called_once()
    assert client.invoke.call_args[0][0] == "tier1"


def test_extract_claims_handles_non_json():
    """Graceful fallback when LLM returns non-JSON text."""
    client = _mock_llm("Here are some claims I found in the document...")

    claims = extract_claims("# Doc\nContent", client)

    assert claims == []


def test_extract_claims_extracts_from_wrapped_json():
    """LLM wraps JSON in explanation text, parser still extracts it."""
    text = 'Here are the claims:\n["claim one", "claim two"]\nDone.'
    client = _mock_llm(text)

    claims = extract_claims("# Doc", client)

    assert len(claims) == 2
    assert claims[0] == "claim one"
    assert claims[1] == "claim two"


# ---------------------------------------------------------------------------
# Tests: verify_claim
# ---------------------------------------------------------------------------


def test_verify_claim_verified():
    """Mock RAG returns matching code, mock LLM says 'verified'."""
    rag_results = [
        {
            "qualified_name": "orders.OrderService",
            "chunk_text": "class OrderService:\n    def process_payment(self):\n        ...",
            "domain": "orders",
            "file_path": "src/orders/service.py",
            "distance": 0.1,
            "chunk_type": "class",
        },
    ]

    # First call is for extract, but verify_claim calls invoke directly
    client = _mock_llm("verified")

    with _mock_rag_results(rag_results):
        result = verify_claim("The OrderService handles payment processing", Path("/tmp/test.db"), client)

    assert result.status == "verified"
    assert result.claim_text == "The OrderService handles payment processing"
    assert "OrderService" in result.evidence
    assert result.source_file == "src/orders/service.py"


def test_verify_claim_unverified_no_results():
    """Mock RAG returns empty -> status='unverified'."""
    client = _mock_llm("unverified")

    with _mock_rag_results([]):
        result = verify_claim("The FooService does something", Path("/tmp/test.db"), client)

    assert result.status == "unverified"
    assert result.evidence == ""
    assert result.source_file == ""
    # LLM should NOT be called when RAG returns nothing
    client.invoke.assert_not_called()


def test_verify_claim_contradicted():
    """Mock RAG returns code that contradicts the claim."""
    rag_results = [
        {
            "qualified_name": "users.UserService",
            "chunk_text": "class UserService:\n    def delete_user(self, user_id: int):\n        ...",
            "domain": "users",
            "file_path": "src/users/service.py",
            "distance": 0.2,
            "chunk_type": "class",
        },
    ]

    client = _mock_llm("contradicted")

    with _mock_rag_results(rag_results):
        result = verify_claim("The UserService creates new users", Path("/tmp/test.db"), client)

    assert result.status == "contradicted"
    assert result.source_file == "src/users/service.py"


# ---------------------------------------------------------------------------
# Tests: review_document
# ---------------------------------------------------------------------------


def test_review_document_full_pipeline():
    """Mock both LLM and RAG, verify end-to-end pipeline."""
    claims_json = json.dumps(["Claim A is true", "Claim B is true"])

    client = MagicMock()
    # First call: extract_claims -> returns JSON array
    # Subsequent calls: verify_claim -> returns "verified"
    client.invoke.side_effect = [
        LLMResponse(text=claims_json, tokens_in=100, tokens_out=50, model="m", tier="tier1"),
        LLMResponse(text="verified", tokens_in=50, tokens_out=10, model="m", tier="tier1"),
        LLMResponse(text="unverified", tokens_in=50, tokens_out=10, model="m", tier="tier1"),
    ]

    rag_results = [
        {
            "qualified_name": "mod.Foo",
            "chunk_text": "class Foo: pass",
            "domain": "core",
            "file_path": "src/foo.py",
            "distance": 0.1,
            "chunk_type": "class",
        },
    ]

    with _mock_rag_results(rag_results):
        results = review_document("# Doc\nSome content", Path("/tmp/test.db"), client)

    assert len(results) == 2
    assert results[0].status == "verified"
    assert results[1].status == "unverified"


def test_review_document_caps_claims():
    """Content with many claims, max_claims=3, verify only 3 processed."""
    many_claims = json.dumps([f"Claim {i}" for i in range(10)])

    client = MagicMock()
    # First call: extract returns 10 claims
    responses = [
        LLMResponse(text=many_claims, tokens_in=100, tokens_out=50, model="m", tier="tier1"),
    ]
    # Next 3 calls: verify (only 3 due to cap)
    for _ in range(3):
        responses.append(
            LLMResponse(text="verified", tokens_in=50, tokens_out=10, model="m", tier="tier1"),
        )
    client.invoke.side_effect = responses

    rag_results = [
        {
            "qualified_name": "mod.Bar",
            "chunk_text": "class Bar: pass",
            "domain": "core",
            "file_path": "src/bar.py",
            "distance": 0.1,
            "chunk_type": "class",
        },
    ]

    with _mock_rag_results(rag_results):
        results = review_document("# Doc", Path("/tmp/test.db"), client, max_claims=3)

    assert len(results) == 3
    # extract (1) + verify (3) = 4 total LLM calls
    assert client.invoke.call_count == 4


# ---------------------------------------------------------------------------
# Tests: get_review_summary
# ---------------------------------------------------------------------------


def test_get_review_summary():
    """2 verified + 1 unverified -> confidence=0.67."""
    claims = [
        ReviewClaim(claim_text="A", status="verified"),
        ReviewClaim(claim_text="B", status="verified"),
        ReviewClaim(claim_text="C", status="unverified"),
    ]

    summary = get_review_summary(claims)

    assert summary["verified"] == 2
    assert summary["unverified"] == 1
    assert summary["contradicted"] == 0
    assert summary["total"] == 3
    assert summary["confidence"] == 0.67


def test_get_review_summary_empty():
    """No claims -> confidence 1.0."""
    summary = get_review_summary([])

    assert summary["total"] == 0
    assert summary["confidence"] == 1.0


def test_get_review_summary_all_contradicted():
    """All contradicted -> confidence=0.0."""
    claims = [
        ReviewClaim(claim_text="X", status="contradicted"),
        ReviewClaim(claim_text="Y", status="contradicted"),
    ]

    summary = get_review_summary(claims)

    assert summary["contradicted"] == 2
    assert summary["confidence"] == 0.0


# ---------------------------------------------------------------------------
# Tests: persist_claims
# ---------------------------------------------------------------------------


def test_persist_claims_writes_to_db(tmp_path: Path):
    """Create test DB, persist claims, verify rows in review_claims table."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    # Create a scan_run and generated_doc so FK is satisfied
    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO scan_runs (repo_url, started_at, status) VALUES (?, ?, ?)",
        ("https://example.com/repo", "2026-01-01T00:00:00Z", "running"),
    )
    conn.commit()
    scan_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO generated_docs (scan_id, domain, doc_type, title, content_md, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (scan_id, "orders", "as-is", "Test Doc", "# Content", 0.8, "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    claims = [
        ReviewClaim(
            claim_text="The OrderService handles payments",
            status="verified",
            evidence="class OrderService:\n    def process_payment(self):",
            source_file="src/orders/service.py",
        ),
        ReviewClaim(
            claim_text="The UserService creates reports",
            status="unverified",
            evidence="",
            source_file="",
        ),
        ReviewClaim(
            claim_text="The DB uses MongoDB",
            status="contradicted",
            evidence="DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}",
            source_file="settings.py",
        ),
    ]

    persist_claims(claims, doc_id, db_path)

    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT claim_text, status, evidence, source_file FROM review_claims WHERE doc_id = ? ORDER BY id",
        (doc_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 3

    assert rows[0]["claim_text"] == "The OrderService handles payments"
    assert rows[0]["status"] == "verified"
    assert "OrderService" in rows[0]["evidence"]
    assert rows[0]["source_file"] == "src/orders/service.py"

    assert rows[1]["status"] == "unverified"
    assert rows[1]["evidence"] == ""

    assert rows[2]["status"] == "contradicted"
    assert "postgresql" in rows[2]["evidence"]
