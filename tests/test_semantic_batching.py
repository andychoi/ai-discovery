"""Tests for Louvain semantic batching (assessment 07 A-1).

Cluster chunks by call-graph community before Tier-1 LLM work so each batched
call shares semantic context. Reference design: Understand-Anything's
compute-batches.mjs (Louvain + fallback + size caps + singleton merge),
ported onto ai-discovery's richer confidence-weighted call graph.
"""

from ai_discovery.ai.semantic_batching import SemanticBatch, compute_semantic_batches
from ai_discovery.graph.models import CallEdge, CodeChunk


def _chunk(qname: str, file_path: str, domain: str = "core", tokens: int = 100) -> CodeChunk:
    return CodeChunk(
        text=f"def {qname.rsplit('.', 1)[-1]}(): pass",
        chunk_index=0,
        chunk_type="function",
        file_path=file_path,
        language="python",
        qualified_name=qname,
        domain=domain,
        node_type="function",
        token_estimate=tokens,
    )


def _edge(caller: str, callee: str, confidence: float = 1.0) -> CallEdge:
    return CallEdge(caller=caller, callee=callee, edge_type="direct_call", confidence=confidence)


def _two_cliques():
    """Two densely connected file groups (orders, billing) with no cross edges."""
    chunks = [
        _chunk("orders.svc.create", "src/orders/svc.py"),
        _chunk("orders.repo.save", "src/orders/repo.py"),
        _chunk("orders.api.post", "src/orders/api.py"),
        _chunk("billing.svc.charge", "src/billing/svc.py"),
        _chunk("billing.repo.put", "src/billing/repo.py"),
        _chunk("billing.api.pay", "src/billing/api.py"),
    ]
    edges = [
        _edge("orders.api.post", "orders.svc.create"),
        _edge("orders.svc.create", "orders.repo.save"),
        _edge("orders.api.post", "orders.repo.save"),
        _edge("billing.api.pay", "billing.svc.charge"),
        _edge("billing.svc.charge", "billing.repo.put"),
        _edge("billing.api.pay", "billing.repo.put"),
    ]
    return chunks, edges


def test_two_cliques_become_two_batches():
    chunks, edges = _two_cliques()
    batches = compute_semantic_batches(chunks, edges)
    assert len(batches) == 2
    assert all(isinstance(b, SemanticBatch) for b in batches)
    assert all(b.algorithm == "louvain" for b in batches)
    groups = [{c.file_path.split("/")[1] for c in b.chunks} for b in batches]
    assert {"orders"} in groups and {"billing"} in groups
    # every input chunk lands in exactly one batch
    placed = [c.qualified_name for b in batches for c in b.chunks]
    assert sorted(placed) == sorted(c.qualified_name for c in chunks)


def test_deterministic_across_runs():
    chunks, edges = _two_cliques()
    a = compute_semantic_batches(chunks, edges)
    b = compute_semantic_batches(chunks, edges)
    assert [[c.qualified_name for c in x.chunks] for x in a] == \
           [[c.qualified_name for c in x.chunks] for x in b]


def test_same_file_chunks_stay_together():
    chunks = [
        _chunk("orders.svc.create", "src/orders/svc.py"),
        _chunk("orders.svc.cancel", "src/orders/svc.py"),
        _chunk("billing.svc.charge", "src/billing/svc.py"),
        _chunk("billing.svc.refund", "src/billing/svc.py"),
    ]
    edges = [_edge("orders.svc.create", "orders.svc.cancel"),
             _edge("billing.svc.charge", "billing.svc.refund")]
    batches = compute_semantic_batches(chunks, edges)
    by_file = {}
    for b in batches:
        for c in b.chunks:
            by_file.setdefault(c.file_path, set()).add(b.index)
    assert all(len(ixs) == 1 for ixs in by_file.values())


def test_oversize_community_splits_on_chunk_cap():
    # one clique of 30 single-chunk files, cap at 10 chunks per batch
    chunks = [_chunk(f"m.f{i}", f"src/m/f{i:02d}.py") for i in range(30)]
    edges = [_edge(f"m.f{i}", f"m.f{(i + 1) % 30}") for i in range(30)]
    batches = compute_semantic_batches(chunks, edges, max_batch_chunks=10)
    assert all(len(b.chunks) <= 10 for b in batches)
    placed = [c.qualified_name for b in batches for c in b.chunks]
    assert sorted(placed) == sorted(c.qualified_name for c in chunks)


def test_oversize_community_splits_on_token_cap():
    chunks = [_chunk(f"m.f{i}", f"src/m/f{i}.py", tokens=900) for i in range(6)]
    edges = [_edge(f"m.f{i}", f"m.f{(i + 1) % 6}") for i in range(6)]
    batches = compute_semantic_batches(chunks, edges, max_batch_tokens=2000)
    assert all(sum(c.token_estimate for c in b.chunks) <= 2000 for b in batches)


def test_singletons_merge_into_misc_batches():
    """Files with no call edges form singleton communities; they must be pooled
    into shared batches (U-A: 87 singletons -> 4 misc batches), not 1-chunk calls."""
    chunks = [_chunk(f"iso.f{i}", f"src/iso/f{i:02d}.py") for i in range(8)]
    batches = compute_semantic_batches(chunks, [], max_batch_chunks=10)
    assert len(batches) == 1
    assert len(batches[0].chunks) == 8


def test_singleton_merge_groups_by_domain_first():
    chunks = [
        _chunk("a.f1", "src/a/f1.py", domain="alpha"),
        _chunk("a.f2", "src/a/f2.py", domain="alpha"),
        _chunk("b.f1", "src/b/f1.py", domain="beta"),
        _chunk("b.f2", "src/b/f2.py", domain="beta"),
    ]
    batches = compute_semantic_batches(chunks, [], max_batch_chunks=2)
    domains = [sorted({c.domain for c in b.chunks}) for b in batches]
    assert ["alpha"] in domains and ["beta"] in domains


def test_fallback_when_clustering_unavailable(monkeypatch):
    """If Louvain fails the batcher must degrade to deterministic grouping —
    never crash Tier-1 and never silently drop chunks."""
    import ai_discovery.ai.semantic_batching as sb
    monkeypatch.setattr(sb, "_louvain_file_communities",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    chunks, edges = _two_cliques()
    batches = compute_semantic_batches(chunks, edges)
    assert batches and all(b.algorithm == "fallback" for b in batches)
    placed = [c.qualified_name for b in batches for c in b.chunks]
    assert sorted(placed) == sorted(c.qualified_name for c in chunks)


def test_empty_input():
    assert compute_semantic_batches([], []) == []


def test_split_class_chunks_map_via_parent_class():
    """Chunks split from a large class carry parent_class; call edges that
    reference the class node's qualified name must still bind to the file."""
    method = _chunk("orders.OrderService.process", "src/orders/svc.py")
    method.parent_class = "orders.OrderService"
    other = _chunk("orders.repo.save", "src/orders/repo.py")
    lonely = _chunk("misc.util", "src/misc/util.py")
    edges = [_edge("orders.OrderService", "orders.repo.save")]
    batches = compute_semantic_batches([method, other, lonely], edges)
    # svc.py and repo.py are linked through the class-level edge
    linked = next(b for b in batches
                  if any(c.file_path == "src/orders/svc.py" for c in b.chunks))
    assert any(c.file_path == "src/orders/repo.py" for c in linked.chunks)
