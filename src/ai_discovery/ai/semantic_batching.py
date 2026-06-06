"""Louvain semantic batching for Tier-1 summarization (assessment 07, A-1).

Cluster files by call-graph community before Tier-1 LLM work, so each batched
call carries chunks that actually reference each other — the summarizer sees
the callers/callees it is describing instead of one chunk in isolation.

Design ported from Understand-Anything's compute-batches.mjs (Louvain over an
import graph, deterministic fallback, size caps, singleton consolidation),
adapted to ai-discovery's stronger signal: Phase-7 call edges carry resolved,
confidence-weighted qualified-name relationships, which collapse into a
weighted file graph rather than U-A's unweighted import graph.

Failure posture: clustering problems degrade loudly to deterministic
domain/path grouping — Tier-1 never crashes and never drops chunks because of
batching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..graph.models import CallEdge, CodeChunk

logger = logging.getLogger(__name__)

# Caps per batched LLM call. Token cap bounds prompt size (chunk token
# estimates are len(text)//4, individually capped by the chunker at ~1500);
# the chunk cap keeps the structured-output array small enough that one
# malformed member doesn't cost a large re-do.
_MAX_BATCH_CHUNKS = 10
_MAX_BATCH_TOKENS = 12_000

# Batches smaller than this are pooled into shared "misc" batches (U-A found
# 87 singleton communities on a 314-file run — 1-chunk LLM calls would pay the
# full instruction overhead the batching exists to amortize).
_MIN_BATCH_CHUNKS = 3

# Fixed seed: Louvain is stochastic; identical input must yield identical
# batches so resumes and tests are reproducible.
_LOUVAIN_SEED = 42


@dataclass
class SemanticBatch:
    """One Tier-1 work unit: chunks that share a call-graph community."""

    index: int  # 1-based
    chunks: list[CodeChunk]
    algorithm: str  # "louvain" | "fallback"


def _louvain_file_communities(
    files: list[str], weights: dict[tuple[str, str], float], seed: int
) -> list[set[str]]:
    """Partition files into communities via Louvain over the weighted file graph."""
    import networkx as nx

    graph = nx.Graph()
    graph.add_nodes_from(files)
    for (a, b), w in weights.items():
        graph.add_edge(a, b, weight=w)
    return [set(c) for c in nx.community.louvain_communities(graph, weight="weight", seed=seed)]


def _fallback_file_communities(
    files: list[str], chunks_by_file: dict[str, list[CodeChunk]]
) -> list[set[str]]:
    """Deterministic degradation: one community per domain (split downstream by caps)."""

    def _domain(file_path: str) -> str:
        for chunk in chunks_by_file[file_path]:
            if chunk.domain:
                return chunk.domain
        return ""

    groups: dict[str, set[str]] = {}
    for f in sorted(files):
        groups.setdefault(_domain(f), set()).add(f)
    return [groups[d] for d in sorted(groups)]


def _pack(
    chunks: list[CodeChunk], max_chunks: int, max_tokens: int
) -> list[list[CodeChunk]]:
    """Greedy sequential packing under both caps, preserving chunk order
    (callers keep same-file chunks adjacent, so files rarely split)."""
    parts: list[list[CodeChunk]] = []
    cur: list[CodeChunk] = []
    cur_tokens = 0
    for chunk in chunks:
        tokens = chunk.token_estimate or 0
        if cur and (len(cur) >= max_chunks or cur_tokens + tokens > max_tokens):
            parts.append(cur)
            cur, cur_tokens = [], 0
        cur.append(chunk)
        cur_tokens += tokens
    if cur:
        parts.append(cur)
    return parts


def compute_semantic_batches(
    chunks: list[CodeChunk],
    call_edges: list[CallEdge] | None,
    *,
    max_batch_chunks: int = _MAX_BATCH_CHUNKS,
    max_batch_tokens: int = _MAX_BATCH_TOKENS,
    min_batch_chunks: int = _MIN_BATCH_CHUNKS,
    seed: int = _LOUVAIN_SEED,
) -> list[SemanticBatch]:
    """Group chunks into call-graph-community batches for batched Tier-1 calls.

    Every input chunk lands in exactly one batch; chunks of the same file stay
    in the same batch (unless a single file alone exceeds the caps). Returns
    [] for empty input.
    """
    if not chunks:
        return []

    chunks_by_file: dict[str, list[CodeChunk]] = {}
    for chunk in chunks:
        chunks_by_file.setdefault(chunk.file_path, []).append(chunk)
    for file_chunks in chunks_by_file.values():
        file_chunks.sort(key=lambda c: (c.chunk_index, c.qualified_name))

    # Qualified-name → file map. Split-class chunks carry the class node's
    # qualified name in parent_class — call edges reference the class node, so
    # both names must bind to the file.
    qn_to_file: dict[str, str] = {}
    for chunk in chunks:
        qn_to_file.setdefault(chunk.qualified_name, chunk.file_path)
        if chunk.parent_class:
            qn_to_file.setdefault(chunk.parent_class, chunk.file_path)

    # Collapse call edges to an undirected weighted file graph. Confidence is
    # the weight: a 1.0 exact-match edge binds files more strongly than a 0.5
    # unresolved guess.
    weights: dict[tuple[str, str], float] = {}
    for edge in call_edges or []:
        file_a = qn_to_file.get(edge.caller)
        file_b = qn_to_file.get(edge.callee)
        if not file_a or not file_b or file_a == file_b:
            continue
        key = (file_a, file_b) if file_a < file_b else (file_b, file_a)
        weights[key] = weights.get(key, 0.0) + (edge.confidence or 1.0)

    files = sorted(chunks_by_file)
    try:
        communities = _louvain_file_communities(files, weights, seed)
        algorithm = "louvain"
    except Exception as exc:
        logger.warning(
            "Semantic batching: Louvain clustering failed (%s) — falling back "
            "to domain/path grouping; module semantic boundaries lost",
            exc,
        )
        communities = _fallback_file_communities(files, chunks_by_file)
        algorithm = "fallback"

    # Communities → packed chunk lists. Largest communities first, min-path
    # tiebreak — deterministic regardless of set iteration order.
    packed: list[list[CodeChunk]] = []
    small: list[CodeChunk] = []
    for community in sorted(communities, key=lambda c: (-len(c), min(c))):
        community_chunks = [c for f in sorted(community) for c in chunks_by_file[f]]
        for part in _pack(community_chunks, max_batch_chunks, max_batch_tokens):
            if len(part) < min_batch_chunks:
                small.extend(part)
            else:
                packed.append(part)

    # Singleton/undersized consolidation: pool by domain (then path) so misc
    # batches still share business context, and re-pack under the same caps.
    if small:
        by_domain: dict[str, list[CodeChunk]] = {}
        for chunk in sorted(
            small, key=lambda c: (c.domain or "", c.file_path, c.chunk_index, c.qualified_name)
        ):
            by_domain.setdefault(chunk.domain or "", []).append(chunk)
        n_misc = 0
        for domain in sorted(by_domain):
            parts = _pack(by_domain[domain], max_batch_chunks, max_batch_tokens)
            packed.extend(parts)
            n_misc += len(parts)
        logger.info(
            "Semantic batching: pooled %d undersized chunks into %d misc batches",
            len(small), n_misc,
        )

    return [
        SemanticBatch(index=i, chunks=part, algorithm=algorithm)
        for i, part in enumerate(packed, start=1)
    ]
