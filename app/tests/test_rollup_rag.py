"""Tests for RAG-augmented Tier 3 rollup."""
import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


@pytest.fixture
def mock_llm_client():
    client = MagicMock()
    client.get_embedding.return_value = [0.1, 0.2, 0.3, 0.4]
    return client


def test_retrieve_tier3_context_returns_code_snippets(tmp_path, mock_llm_client):
    """RAG retrieval returns relevant code chunks for a doc_type query."""
    from app.ai.rollup import _retrieve_tier3_context

    db_path = tmp_path / "test.db"

    try:
        import pysqlite3 as sqlite3
    except ImportError:
        import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    conn.execute("CREATE VIRTUAL TABLE discovery_vectors USING vec0(embedding float[4])")
    conn.execute("""CREATE TABLE discovery_chunk_meta (
        rowid INTEGER PRIMARY KEY, qualified_name TEXT, chunk_text TEXT,
        domain TEXT, file_path TEXT, chunk_type TEXT
    )""")
    vec = _serialize_f32([0.1, 0.2, 0.3, 0.4])
    conn.execute("INSERT INTO discovery_vectors (embedding) VALUES (?)", (vec,))
    conn.execute(
        "INSERT INTO discovery_chunk_meta (rowid, qualified_name, chunk_text, domain, file_path, chunk_type) "
        "VALUES (1, 'AuthController.login', 'def login(request): ...', 'auth', 'auth/controller.py', 'method')"
    )
    conn.commit()
    conn.close()

    result = _retrieve_tier3_context("auth", "as-is-api", db_path, mock_llm_client, top_k=3)

    assert isinstance(result, str)
    assert "AuthController.login" in result
    assert "def login(request)" in result


def _make_domain(name="auth"):
    from app.graph.models import Domain, CodeNode

    node = CodeNode(
        file_path="auth/controller.py",
        language="python",
        node_type="class",
        name="AuthController",
        qualified_name="auth.AuthController",
        line_start=1, line_end=50,
        source_code="class AuthController: ...",
    )
    node.domain = name
    return Domain(name=name, nodes=[node], entry_points=[node])


def test_build_rollup_prompt_includes_rag_section():
    """When rag_context is provided, it appears in the prompt."""
    from app.ai.rollup import _build_rollup_prompt

    domain = _make_domain()
    rag_context = "### auth.login (auth/handler.py)\n```\ndef login(): pass\n```"

    prompt = _build_rollup_prompt(domain, "as-is-api", {}, [], rag_context=rag_context)

    assert "Source Code Context" in prompt
    assert "auth.login" in prompt
    assert "def login(): pass" in prompt


def test_build_rollup_prompt_no_rag_context():
    """When rag_context is empty, no RAG section is added."""
    from app.ai.rollup import _build_rollup_prompt

    domain = _make_domain()
    prompt = _build_rollup_prompt(domain, "as-is", {}, [], rag_context="")

    assert "Source Code Context" not in prompt


def test_build_rollup_prompt_includes_external_summaries():
    """External edges include callee summaries when available."""
    from app.ai.rollup import _build_rollup_prompt
    from app.graph.models import CallEdge

    domain = _make_domain("orders")
    ext_edge = CallEdge(
        caller="orders.OrderService.checkout",
        callee="payments.PaymentGateway.charge",
        edge_type="calls",
        confidence=0.9,
    )
    domain.external_edges = [ext_edge]

    summaries = {
        "payments.PaymentGateway.charge": {
            "purpose": "Processes credit card payments via Stripe API",
            "io_summary": "Input: amount, card_token. Output: transaction_id. Side effect: Stripe API call.",
        }
    }

    # Pass all_summaries to enable cross-domain resolution
    prompt = _build_rollup_prompt(domain, "as-is", summaries, [])

    assert "Stripe API" in prompt or "Processes credit card" in prompt


def test_retrieve_tier3_context_empty_db(tmp_path, mock_llm_client):
    """Returns empty string when no vectors exist."""
    from app.ai.rollup import _retrieve_tier3_context

    db_path = tmp_path / "empty.db"

    try:
        import pysqlite3 as sqlite3
    except ImportError:
        import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.close()

    result = _retrieve_tier3_context("auth", "as-is", db_path, mock_llm_client)
    assert result == ""
