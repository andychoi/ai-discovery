"""Tour/onboarding generator (assessment 07 A-7).

ONBOARD/{domain}.md: a pedagogical learning path through a domain — entry
points first, then BFS down the high-confidence call chain, each step carrying
its file:line and Tier-1 purpose. Ported from Understand-Anything's
tour-builder idea, with strictly better inputs: a real resolved call graph
with confidence, instead of LLM-guessed topology.

Facts/prose separation (same rule as rollups): the step table is deterministic
verified structure; ONE Tier-2 call per domain writes the narrative around it.
A narrative failure never blocks the table — facts don't depend on prose.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from ..ai.rollup import unreviewed_confidence
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


def _build_narrative_prompt(domain_name: str, steps: list[TourStep]) -> str:
    step_lines = "\n".join(
        f"{s.order}. {s.qualified_name} ({s.node_type}, {s.file_path}) — {s.purpose or 'no summary'}"
        for s in steps
    )
    return (
        f"You are writing the orientation narrative for a developer-onboarding "
        f"guide to the `{domain_name}` domain of a codebase.\n\n"
        f"The learning path below was derived from the real call graph (entry "
        f"points first, then the call chain). Do NOT restate it as a list — it "
        f"is rendered separately as a table.\n\n"
        f"Learning path:\n{step_lines}\n\n"
        f"Write 2-3 short paragraphs: what this domain does, why the path is "
        f"ordered this way, and what a newcomer should pay attention to while "
        f"reading the listed code. Plain markdown prose, no headings, no code "
        f"fences. Ground every statement in the path above — do not invent "
        f"functionality."
    )


def render_onboarding_md(
    domain_name: str,
    steps: list[TourStep],
    narrative: str,
    project_slug: str,
    confidence: float,
) -> str:
    """Render the onboarding doc: honest frontmatter, LLM narrative, and the
    deterministic Learning Path table with file:line citations."""
    doc_id = f"{project_slug}-onboard-{domain_name}"
    rows = "\n".join(
        f"| {s.order} | `{s.qualified_name}` | {s.node_type} | "
        f"`{s.file_path}:{s.line_start}` | {s.purpose or '—'} |"
        for s in steps
    )
    return f"""---
doc_id: {doc_id}
doc_type: onboard
domain: {domain_name}
discovery_confidence: {confidence}
content_provenance: llm-narrative-unverified
---

# Onboarding: {domain_name}

> ⚠ **LLM-authored narrative, not source-verified.** The Learning Path table
> below is deterministic (derived from the resolved call graph and Tier-1
> summaries); only the prose narrative is generated.

{narrative.strip()}

## Learning Path

*Ordered from entry points down the call chain — read in this order.*

| # | Symbol | Kind | Source | Purpose |
|---|--------|------|--------|---------|
{rows}
"""


def generate_onboarding_docs(
    domains: list[Domain],
    edges: list[CallEdge],
    summaries: dict[str, dict],
    llm_client,
    docs_dir: Path,
    project_slug: str,
    on_progress=None,
) -> list[dict]:
    """Generate ONBOARD/{slug}-onboard-{domain}.md per tourable domain.

    One Tier-2 call per domain (narrative only). Returns doc dicts ready for
    generated_docs persistence: doc_id, doc_type, domain, title, content_md,
    confidence, verified_row_count.
    """
    out_dir = Path(docs_dir) / "ONBOARD"
    docs: list[dict] = []

    for i, domain in enumerate(domains, start=1):
        steps = build_tour_steps(domain, edges, summaries)
        if len(steps) < _MIN_STEPS:
            logger.info(
                "Onboarding: domain %s has %d tourable steps (< %d) — skipped",
                domain.name, len(steps), _MIN_STEPS,
            )
            continue

        try:
            response = llm_client.invoke(
                "tier2", _build_narrative_prompt(domain.name, steps), max_tokens=1024
            )
            narrative = response.text.strip()
        except Exception as exc:
            logger.warning(
                "Onboarding narrative failed for %s (%s) — shipping the "
                "deterministic table without prose",
                domain.name, exc,
            )
            narrative = (
                "*Narrative generation failed — follow the Learning Path "
                "table below; it is derived directly from the call graph.*"
            )

        confidence = unreviewed_confidence(len(steps))
        content = render_onboarding_md(
            domain.name, steps, narrative, project_slug, confidence
        )
        doc_id = f"{project_slug}-onboard-{domain.name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{doc_id}.md").write_text(content, encoding="utf-8")

        docs.append({
            "doc_id": doc_id,
            "doc_type": "onboard",
            "domain": domain.name,
            "title": f"Onboarding: {domain.name}",
            "content_md": content,
            "confidence": confidence,
            "verified_row_count": len(steps),
        })
        if on_progress:
            on_progress(i, len(domains))

    return docs
