"""Tests for AST-aware code chunker."""

from __future__ import annotations

import pytest

from ai_discovery.ai.chunker import chunk_code_nodes, chunk_for_rag
from ai_discovery.graph.models import CodeNode, CodeChunk


def test_small_class_single_chunk():
    """Class that fits in max_chars -> single chunk."""
    node = CodeNode(
        file_path="svc.py",
        language="python",
        node_type="class",
        name="SmallService",
        qualified_name="svc.SmallService",
        source_code="class SmallService:\n    def do(self): pass",
        line_start=1,
        line_end=2,
    )
    chunks = chunk_code_nodes([node], max_chars=6000)
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "class"
    assert chunks[0].qualified_name == "svc.SmallService"


def test_large_class_split_by_method():
    """Class exceeding max_chars splits into method-level chunks with class context."""
    methods_src = "\n".join(
        f"    def method_{i}(self):\n        " + "x = 1\n        " * 20
        for i in range(20)
    )
    source = f"class BigService:\n{methods_src}"
    node = CodeNode(
        file_path="svc.py",
        language="python",
        node_type="class",
        name="BigService",
        qualified_name="svc.BigService",
        source_code=source,
        line_start=1,
        line_end=100,
    )
    chunks = chunk_code_nodes([node], max_chars=500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert "BigService" in chunk.text  # class context preserved


def test_endpoint_preserves_metadata():
    node = CodeNode(
        file_path="api.py",
        language="python",
        node_type="endpoint",
        name="create_payment",
        qualified_name="api.create_payment",
        source_code='@router.post("/payments")\ndef create_payment(): pass',
        line_start=1,
        line_end=2,
        framework_hints={"method": "POST", "route": "/payments"},
        annotations=["router.post"],
    )
    chunks = chunk_code_nodes([node], max_chars=6000)
    assert chunks[0].framework_hints["route"] == "/payments"
    assert chunks[0].node_type == "endpoint"


def test_token_estimate():
    node = CodeNode(
        file_path="x.py",
        language="python",
        node_type="function",
        name="f",
        qualified_name="x.f",
        source_code="x" * 400,
        line_start=1,
        line_end=1,
    )
    chunks = chunk_code_nodes([node])
    assert chunks[0].token_estimate == 100  # 400 // 4


def test_chunk_for_rag_splits_large():
    big_chunk = CodeChunk(
        text="line\n" * 500,
        chunk_index=0,
        chunk_type="class",
        file_path="x.py",
        language="python",
        qualified_name="x.Big",
    )
    rag_chunks = chunk_for_rag([big_chunk], chunk_size=200, overlap=50)
    assert len(rag_chunks) > 1
    # Check overlap: end of chunk N should overlap with start of chunk N+1
    for i in range(len(rag_chunks) - 1):
        current_lines = rag_chunks[i].text.split("\n")
        next_lines = rag_chunks[i + 1].text.split("\n")
        # There should be some shared lines between consecutive chunks
        current_tail = set(current_lines[-10:])
        next_head = set(next_lines[:10])
        assert current_tail & next_head, (
            f"No overlap found between chunk {i} and {i+1}"
        )


def test_chunk_for_rag_small_passthrough():
    """Chunks already under chunk_size pass through unchanged."""
    small = CodeChunk(
        text="small code",
        chunk_index=0,
        chunk_type="function",
        file_path="f.py",
        language="python",
        qualified_name="f.small",
    )
    result = chunk_for_rag([small], chunk_size=1500)
    assert len(result) == 1
    assert result[0].text == "small code"


def test_function_single_chunk():
    """Functions always produce a single chunk regardless of size."""
    node = CodeNode(
        file_path="util.py",
        language="python",
        node_type="function",
        name="big_func",
        qualified_name="util.big_func",
        source_code="def big_func():\n" + "    x = 1\n" * 200,
        line_start=1,
        line_end=201,
    )
    chunks = chunk_code_nodes([node], max_chars=500)
    # Functions don't get split (only classes do)
    assert len(chunks) == 1
    assert chunks[0].chunk_type == "function"


def test_sequential_chunk_indices():
    """Chunk indices are sequential across multiple nodes."""
    nodes = [
        CodeNode(
            file_path="a.py", language="python", node_type="function",
            name=f"fn_{i}", qualified_name=f"a.fn_{i}",
            source_code=f"def fn_{i}(): pass", line_start=i, line_end=i,
        )
        for i in range(5)
    ]
    chunks = chunk_code_nodes(nodes)
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3, 4]
