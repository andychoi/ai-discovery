"""Tracks 1 + 4 — verified-facts injection and per-claim confidence blending.

Track 1: the Java parser extracts route + method on endpoint nodes; rollup
must surface them as a deterministic table in both the LLM prompt and the
final markdown, so the LLM cannot rewrite paths from REST priors.

Track 4: doc-level confidence blends AST-verified row count with the
self-review verdict ratio rather than trusting the LLM's self-asserted score.
"""

from __future__ import annotations

from ai_discovery.ai.rollup import (
    _build_rollup_prompt,
    _build_verified_api_table,
    _build_verified_schema_table,
    blend_confidence,
)
from ai_discovery.graph.models import CodeNode, Domain


def _endpoint(qualified_name: str, route: str, method: str, file_path: str = "C.java", line: int = 10) -> CodeNode:
    return CodeNode(
        file_path=file_path,
        language="java",
        node_type="endpoint",
        name=qualified_name.split(".")[-1],
        qualified_name=qualified_name,
        source_code="",
        line_start=line,
        line_end=line + 5,
        framework_hints={"route": route, "method": method},
    )


def _entity(qualified_name: str, fields: list[str], bases: list[str] | None = None) -> CodeNode:
    return CodeNode(
        file_path="Order.java",
        language="java",
        node_type="db_model",
        name=qualified_name.split(".")[-1],
        qualified_name=qualified_name,
        source_code="",
        line_start=20,
        line_end=40,
        fields=fields,
        bases=bases or [],
    )


def _domain(nodes: list[CodeNode], db_models: list[CodeNode] | None = None) -> Domain:
    return Domain(
        name="orders",
        nodes=nodes,
        internal_edges=[],
        external_edges=[],
        entry_points=[],
        db_models=db_models or [n for n in nodes if n.node_type == "db_model"],
    )


# ---------------------------------------------------------------------------
# API table
# ---------------------------------------------------------------------------

def test_verified_api_table_renders_extracted_routes():
    """Routes from framework_hints land in the table verbatim — no REST inference."""
    nodes = [
        _endpoint("UserController.signIn", "/users/signin", "POST"),
        _endpoint("UserController.register", "/users/signup", "POST"),
        _endpoint("ProductController.updateProduct", "/product/update/{id}", "POST"),
    ]
    result = _build_verified_api_table(_domain(nodes))
    assert result is not None
    table, count = result
    assert count == 3
    assert "/users/signup" in table
    assert "/product/update/{id}" in table
    # The hallucinated REST-convention paths must NOT appear:
    assert "/user/register" not in table
    assert "PUT" not in table  # ProductController.updateProduct is POST, not PUT
    # Track 4: every verified row carries an explicit confidence marker.
    assert "✓ AST" in table


def test_verified_api_table_includes_source_citation():
    nodes = [_endpoint("U.signIn", "/users/signin", "POST", file_path="UserController.java", line=42)]
    table, _ = _build_verified_api_table(_domain(nodes))
    assert "UserController.java:42" in table


def test_verified_api_table_returns_none_when_no_endpoints():
    """Domains without endpoints (e.g. pure data layer) emit no table."""
    nodes = [_entity("Order", ["id", "total"])]
    assert _build_verified_api_table(_domain(nodes)) is None


def test_verified_api_table_skips_endpoints_missing_hints():
    """An endpoint node missing route/method hints is silently skipped, not guessed."""
    bare = CodeNode(
        file_path="X.java", language="java", node_type="endpoint",
        name="orphan", qualified_name="X.orphan", source_code="",
        line_start=1, line_end=2,
    )
    nodes = [bare, _endpoint("U.signIn", "/users/signin", "POST")]
    result = _build_verified_api_table(_domain(nodes))
    assert result is not None
    table, count = result
    assert count == 1  # only the hinted endpoint counts
    assert "/users/signin" in table
    assert "X.orphan" not in table


# ---------------------------------------------------------------------------
# Schema table
# ---------------------------------------------------------------------------

def test_verified_schema_table_lists_entity_fields():
    entities = [_entity("orders.Order", ["id", "totalPrice", "createdDate"], bases=["BaseEntity"])]
    result = _build_verified_schema_table(_domain([], db_models=entities))
    assert result is not None
    table, count = result
    assert count == 1
    assert "Order" in table
    assert "totalPrice" in table
    assert "extends `BaseEntity`" in table
    # Track 4: entity heading carries a confidence marker
    assert "✓ AST" in table


def test_verified_schema_table_returns_none_when_no_entities_with_fields():
    fieldless = _entity("X", fields=[])
    assert _build_verified_schema_table(_domain([], db_models=[fieldless])) is None


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------

def test_prompt_includes_verified_facts_block_with_non_overridable_marker():
    nodes = [_endpoint("U.signIn", "/users/signin", "POST")]
    entities = [_entity("Order", ["id"])]
    domain = _domain(nodes, db_models=entities)
    prompt = _build_rollup_prompt(domain, "as-is-detail", summaries={}, flows=[])
    assert "VERIFIED FACTS" in prompt
    assert "do not modify" in prompt.lower()
    assert "/users/signin" in prompt
    # The instruction must tell the LLM not to duplicate the table:
    assert "Do NOT include duplicate" in prompt


# ---------------------------------------------------------------------------
# Track 4 — confidence blending
# ---------------------------------------------------------------------------

def test_blend_confidence_no_data_is_unverifiable_not_certain():
    """Empty review + zero AST rows -> LOW confidence (HIGH-1): a doc with
    nothing verifiable is unverifiable, not maximally confident."""
    from ai_discovery.ai.rollup import UNVERIFIABLE_CONFIDENCE
    summary = {"verified": 0, "unverified": 0, "contradicted": 0, "total": 0}
    assert blend_confidence(0, summary) == UNVERIFIABLE_CONFIDENCE
    assert blend_confidence(0, summary) < 0.5


def test_blend_confidence_pure_ast_no_review():
    """All-AST docs (no prose claims) score 1.0 — every row is verified."""
    summary = {"verified": 0, "unverified": 0, "contradicted": 0, "total": 0}
    assert blend_confidence(20, summary) == 1.0


def test_blend_confidence_pure_prose_review_ratio():
    """No AST rows -> score equals the review verified-ratio with unverified at 0.5."""
    summary = {"verified": 5, "unverified": 2, "contradicted": 1, "total": 8}
    # 5*1.0 + 2*0.5 + 1*0.0 = 6.0 / 8 = 0.75
    assert blend_confidence(0, summary) == 0.75


def test_blend_confidence_blended_ast_and_prose():
    """AST rows pull confidence up because they're deterministically verified."""
    summary = {"verified": 1, "unverified": 0, "contradicted": 1, "total": 2}
    # 20*1.0 + 1*1.0 + 1*0.0 = 21 / 22 ≈ 0.95
    score = blend_confidence(20, summary)
    assert 0.94 <= score <= 0.96


def test_blend_confidence_contradicted_drops_score_meaningfully():
    """A doc with 3 contradicted prose claims and few AST rows scores low."""
    summary = {"verified": 0, "unverified": 0, "contradicted": 3, "total": 3}
    # 1*1.0 + 3*0.0 = 1 / 4 = 0.25
    assert blend_confidence(1, summary) == 0.25
