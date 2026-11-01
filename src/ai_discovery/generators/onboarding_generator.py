"""Onboarding tour steps (assessment 07 A-7; pages since M3.4).

A pedagogical learning path through a domain — entry points first, then BFS down the high-confidence call
chain, each step carrying its file:line. ``pages/builders/flows.py`` renders it as the domain's onboarding
page; the narrative around it is the page prose layer (M3.7.a). The ONBOARD/ markdown docs and their own
LLM narrative were removed with the legacy SDLC tree (M3.7.b).
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from ..graph.models import CallEdge, CodeNode, Domain

logger = logging.getLogger(__name__)

# Tour shape (U-A: 5–15 pedagogical steps). Domains that can't fill the
# minimum aren't worth a Tier-2 call — a 2-step "tour" is just a file listing.
_MIN_STEPS = 3
_MAX_STEPS = 15

# Edges below this confidence don't steer the tour — a 0.6 fan-out guess is
# not a teaching path.
_MIN_EDGE_CONFIDENCE = 0.8


@dataclass
class TourStep:
    """One stop on the learning path — deterministic, AST/graph-backed."""

    order: int
    qualified_name: str
    file_path: str
    line_start: int
    node_type: str
    purpose: str  # Tier-1 summary purpose ("" when unsummarized)
    fan_in: int
    fan_out: int


def build_tour_steps(
    domain: Domain,
    edges: list[CallEdge],
    summaries: dict[str, dict],
    max_steps: int = _MAX_STEPS,
) -> list[TourStep]:
    """BFS-order a learning path through *domain*.

    Seeds: the domain's entry points (endpoints/jobs/commands), highest
    fan-out first — the call-sites a newcomer actually hits. Fallback when a
    domain has no entry points: highest fan-in node (the most-referenced hub).
    Traversal follows ≥ _MIN_EDGE_CONFIDENCE edges between domain nodes,
    higher confidence first; deduped; capped at *max_steps*.
    """
    by_qname = {n.qualified_name: n for n in domain.nodes}

    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    out_edges: dict[str, list[CallEdge]] = defaultdict(list)
    for edge in edges:
        if edge.confidence < _MIN_EDGE_CONFIDENCE:
            continue
        if edge.caller in by_qname and edge.callee in by_qname:
            fan_out[edge.caller] += 1
            fan_in[edge.callee] += 1
            out_edges[edge.caller].append(edge)

    seeds = sorted(
        domain.entry_points,
        key=lambda n: (-fan_out[n.qualified_name], n.qualified_name),
    )
    if not seeds:
        seeds = sorted(
            domain.nodes,
            key=lambda n: (-fan_in[n.qualified_name], n.qualified_name),
        )[:1]

    ordered: list[CodeNode] = []
    seen: set[str] = set()
    queue: deque[str] = deque()
    for seed in seeds:
        if seed.qualified_name not in seen:
            seen.add(seed.qualified_name)
            queue.append(seed.qualified_name)

    while queue and len(ordered) < max_steps:
        qname = queue.popleft()
        ordered.append(by_qname[qname])
        for edge in sorted(out_edges[qname], key=lambda e: (-e.confidence, e.callee)):
            if edge.callee not in seen:
                seen.add(edge.callee)
                queue.append(edge.callee)

    return [
        TourStep(
            order=i,
            qualified_name=node.qualified_name,
            file_path=node.file_path,
            line_start=node.line_start,
            node_type=node.node_type,
            purpose=str((summaries.get(node.qualified_name) or {}).get("purpose", "")),
            fan_in=fan_in[node.qualified_name],
            fan_out=fan_out[node.qualified_name],
        )
        for i, node in enumerate(ordered, start=1)
    ]
