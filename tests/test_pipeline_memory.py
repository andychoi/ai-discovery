"""Tests for P0-3 in-memory source release after chunking."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ai_discovery.ai.chunker import chunk_code_nodes
from ai_discovery.graph.models import CodeNode
from ai_discovery.pipeline import _release_node_source, _budget_exhausted_fn


def _node(qn: str, src: str) -> CodeNode:
    return CodeNode(
        file_path="svc.py", language="python", node_type="class",
        name=qn.split(".")[-1], qualified_name=qn,
        source_code=src, line_start=1, line_end=2,
    )


def test_release_node_source_empties_and_counts():
    nodes = [_node("svc.A", "class A: pass"), _node("svc.B", "class B: pass")]
    freed = _release_node_source(nodes)
    assert freed == len("class A: pass") + len("class B: pass")
    assert all(n.source_code == "" for n in nodes)


def test_release_is_idempotent():
    nodes = [_node("svc.A", "class A: pass")]
    _release_node_source(nodes)
    assert _release_node_source(nodes) == 0  # nothing left to free


def test_chunks_capture_text_before_release():
    """Invariant that makes the release safe: chunking happens first, so chunks
    retain the source text even after node.source_code is released."""
    nodes = [_node("svc.SmallService", "class SmallService:\n    def do(self): pass")]
    chunks = chunk_code_nodes(nodes)          # phase 9 runs first
    captured = chunks[0].text
    _release_node_source(nodes)               # then we release
    assert captured                            # chunk still holds the text
    assert nodes[0].source_code == ""          # node no longer does


# ---------------------------------------------------------------------------
# P1-e: in-phase budget predicate
# ---------------------------------------------------------------------------

def test_budget_exhausted_fn_none_for_local_provider():
    cfg = SimpleNamespace(provider="ollama", budget_limit_usd=50.0)
    assert _budget_exhausted_fn(MagicMock(), cfg) is None


def test_budget_exhausted_fn_none_when_no_limit():
    cfg = SimpleNamespace(provider="bedrock", budget_limit_usd=0.0)
    assert _budget_exhausted_fn(MagicMock(), cfg) is None


def test_budget_exhausted_fn_flips_at_limit():
    cfg = SimpleNamespace(provider="bedrock", budget_limit_usd=10.0)
    client = MagicMock()
    client.total_cost_usd.return_value = 5.0
    fn = _budget_exhausted_fn(client, cfg)
    assert fn() is False
    client.total_cost_usd.return_value = 10.0
    assert fn() is True
