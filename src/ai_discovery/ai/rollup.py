"""Tier 3 doc rollup — uses best LLM (Opus) to generate full SDLC document sections per domain."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..ai.llm_client import LLMClient, AdvisorContext
from ..ai.flow_analyzer import BusinessFlow
from ..db import get_conn, now_iso
from ..graph.models import Domain

logger = logging.getLogger(__name__)

# Doc types generated per domain (Phase 0: Discovery)
DOC_TYPES = ("as-is", "as-is-detail", "as-is-schema")

# Human-readable labels for doc types
_DOC_TYPE_LABELS = {
    "as-is":        "As-Is Assessment",
    "as-is-detail": "As-Is Detail",
    "as-is-schema": "As-Is Data Schema",
}

# Doc-type-specific instructions for the LLM
_DOC_INSTRUCTIONS = {
    "as-is": (
        "Generate a Current-State (As-Is) Assessment document for this domain.\n"
        "Include the following sections:\n"
        "- **Current Architecture Overview**: How components are structured and interact\n"
        "- **Existing APIs & Integrations**: Key external dependencies and protocols\n"
        "- **Current Data Model Summary**: Core entities and data flows\n"
        "- **Known Technical Debt**: Issues, code smells, improvement opportunities\n"
        "- **Pain Points & Limitations**: What the system cannot do or does poorly\n"
        "- **System Boundaries**: In-scope and out-of-scope components\n"
        "- **Linked Discovery Artifacts**: Reference the as-is-detail docs for this domain"
    ),
    "as-is-detail": (
        "Generate a combined As-Is Detail document for this domain.\n"
        "Document what the code currently does across functional spec and API contracts.\n"
        "Include the following sections:\n"
        "## Functional Spec\n"
        "- **Purpose**: What business problem this domain currently solves\n"
        "- **Use Cases**: Key user/system use cases as currently implemented\n"
        "- **Business Rules**: Validation, workflow, and processing rules found in code\n"
        "## API Contracts\n"
        "- **Endpoints / APIs**: All public endpoints with HTTP methods and paths\n"
        "- **Request Schemas**: Input parameters, types, validation rules as implemented\n"
        "- **Response Schemas**: Output structures and status codes as currently returned\n"
        "- **Authentication / Authorization**: Access control as currently enforced"
    ),
    "as-is-schema": (
        "Generate an As-Is Data Schema document for this domain.\n"
        "Document the data model as currently implemented in the codebase.\n"
        "Include the following sections:\n"
        "- **Entity Definitions**: All entities/tables/models with field names, types, and descriptions\n"
        "- **Relationships**: Foreign keys, associations, cardinality as implemented\n"
        "- **Constraints**: Unique keys, not-null, check constraints, indexes, and triggers\n"
        "- **Data Flow**: How data moves through the domain (input → processing → storage → output)\n"
        "- **Migration Notes**: Known schema evolution history, deprecated fields, pending changes\n"
        "\nUse DBML or table notation where it aids clarity. This document is the baseline for\n"
        "data-migration planning — be precise about column types, nullability, and defaults."
    ),
}

# Doc-type-specific search queries for RAG context retrieval
_DOC_TYPE_QUERIES = {
    "as-is": "{domain} architecture components interactions dependencies",
    "as-is-detail": "{domain} business rules validation use cases workflow endpoint API handler request response",
    "as-is-schema": "{domain} database model entity table schema migration column relationship foreign key constraint index",
}


def _retrieve_tier3_context(
    domain_name: str,
    doc_type: str,
    db_path: Path,
    llm_client: LLMClient,
    top_k: int = 5,
) -> str:
    """Retrieve relevant source code via RAG to ground Tier 3 rollup.

    Builds a doc-type-specific query and searches sqlite-vec for matching
    code chunks. Returns formatted context string or empty string on failure.
    """
    try:
        from ..rag.retriever import search
    except ImportError:
        return ""

    query_template = _DOC_TYPE_QUERIES.get(doc_type, "{domain} code")
    query = query_template.format(domain=domain_name)

    try:
        results = search(query, db_path, llm_client, top_k=top_k)
    except Exception:
        logger.debug("RAG retrieval failed for %s/%s", domain_name, doc_type)
        return ""

    if not results:
        return ""

    snippets = []
    for r in results:
        snippets.append(
            f"### {r['qualified_name']} ({r['file_path']})\n"
            f"```\n{r['chunk_text'][:800]}\n```"
        )
    return "\n\n".join(snippets)


@dataclass
class RollupResult:
    domain: str
    doc_type: str
    title: str
    content_md: str
    confidence: float  # 0.0-1.0, from LLM self-assessment
    tokens_in: int
    tokens_out: int
    model: str
    unverified_claims: int = 0  # set by self-review step before writing markdown


def _build_rollup_prompt(
    domain: Domain,
    doc_type: str,
    summaries: dict[str, dict],
    flows: list[BusinessFlow],
    rag_context: str = "",
) -> str:
    """Build Tier 3 prompt for generating a specific doc_type for a domain.

    Include:
    - Domain overview (name, node count, tech stack)
    - Tier 1 summaries of key nodes (entry points, classes, methods)
    - Tier 2 business flows
    - DB models
    - Doc-type-specific instructions
    - Ask for confidence score (0.0-1.0)
    - Return markdown content
    """
    lines: list[str] = []

    # Domain overview
    lines.append(f"# Domain: {domain.name}")
    lines.append(f"Total nodes: {len(domain.nodes)}")
    if domain.tech_stack:
        lines.append(f"Tech stack: {json.dumps(domain.tech_stack)}")
    lines.append("")

    # Entry points
    lines.append("## Entry Points")
    if domain.entry_points:
        for ep in domain.entry_points:
            lines.append(f"- {ep.qualified_name} (type={ep.node_type})")
            if ep.qualified_name in summaries:
                s = summaries[ep.qualified_name]
                if "purpose" in s:
                    lines.append(f"  Purpose: {s['purpose']}")
                if "business_rules" in s:
                    lines.append(f"  Business rules: {s['business_rules']}")
    else:
        lines.append("- (none identified)")
    lines.append("")

    # Node summaries — cap at top 40 to keep prompt within ~30k tokens.
    # Priority: nodes with summaries first, then entry points, then higher degree centrality.
    _ep_names = {ep.qualified_name for ep in domain.entry_points}
    _NODE_LIMIT = 40

    # Compute degree (in + out) for each node from domain edges
    degree: dict[str, int] = {}
    for edge in domain.internal_edges:
        degree[edge.caller] = degree.get(edge.caller, 0) + 1
        degree[edge.callee] = degree.get(edge.callee, 0) + 1

    ranked = sorted(
        domain.nodes,
        key=lambda n: (
            0 if n.qualified_name in summaries else 1,       # has summary
            0 if n.qualified_name in _ep_names else 1,        # is entry point
            -(degree.get(n.qualified_name, 0)),               # higher degree = earlier (negate for desc)
        ),
    )
    shown_nodes = ranked[:_NODE_LIMIT]
    omitted = len(domain.nodes) - len(shown_nodes)

    lines.append("## Key Nodes (with Tier 1 summaries)")
    for node in shown_nodes:
        summary_info = ""
        if node.qualified_name in summaries:
            s = summaries[node.qualified_name]
            parts = []
            if "purpose" in s:
                parts.append(f"Purpose: {s['purpose']}")
            if "io_summary" in s:
                parts.append(f"I/O: {s['io_summary']}")
            if "tech_debt_signals" in s:
                parts.append(f"Tech debt: {s['tech_debt_signals']}")
            if parts:
                summary_info = " | " + " | ".join(parts)
        lines.append(f"- {node.qualified_name} ({node.node_type}){summary_info}")
    if omitted:
        lines.append(f"- ... ({omitted} additional nodes omitted for brevity)")
    lines.append("")

    # DB models
    lines.append("## DB Models")
    if domain.db_models:
        for model in domain.db_models:
            lines.append(f"- {model.qualified_name}")
    else:
        lines.append("- (none identified)")
    lines.append("")

    # Business flows (Tier 2)
    lines.append("## Business Flows (from Tier 2 analysis)")
    if flows:
        for flow in flows:
            lines.append(f"### {flow.name} ({flow.flow_type})")
            lines.append(f"{flow.description}")
            if flow.involved_nodes:
                lines.append(f"Involved nodes: {', '.join(flow.involved_nodes)}")
            lines.append("")
    else:
        lines.append("- (no flows identified)")
    lines.append("")

    # Call graph edges — cap at 50 each to avoid prompt bloat, highest confidence first
    _EDGE_LIMIT = 50
    sorted_internal = sorted(domain.internal_edges, key=lambda e: getattr(e, 'confidence', 0.0), reverse=True)
    lines.append("## Internal Call Graph")
    for edge in sorted_internal[:_EDGE_LIMIT]:
        lines.append(f"- {edge.caller} -> {edge.callee} ({edge.edge_type})")
    if len(domain.internal_edges) > _EDGE_LIMIT:
        lines.append(f"- ... ({len(domain.internal_edges) - _EDGE_LIMIT} more edges omitted)")
    lines.append("")

    if domain.external_edges:
        sorted_external = sorted(domain.external_edges, key=lambda e: getattr(e, 'confidence', 0.0), reverse=True)
        lines.append("## External Dependencies")
        for edge in sorted_external[:_EDGE_LIMIT]:
            callee_info = ""
            if edge.callee in summaries:
                s = summaries[edge.callee]
                parts = []
                if "purpose" in s and s["purpose"]:
                    parts.append(s["purpose"])
                if "io_summary" in s and s["io_summary"]:
                    parts.append(f"I/O: {s['io_summary']}")
                if parts:
                    callee_info = " — " + " | ".join(parts)
            lines.append(f"- {edge.caller} -> {edge.callee} ({edge.edge_type}){callee_info}")
        if len(domain.external_edges) > _EDGE_LIMIT:
            lines.append(f"- ... ({len(domain.external_edges) - _EDGE_LIMIT} more omitted)")
        lines.append("")

    # RAG-retrieved source code (actual code for grounding)
    if rag_context:
        lines.append("## Source Code Context (from RAG retrieval)")
        lines.append("Use the following actual source code to ground your documentation.")
        lines.append("Prefer specifics from this code over inferences from summaries.\n")
        lines.append(rag_context)
        lines.append("")

    # Doc-type-specific instructions
    lines.append("## Task")
    lines.append(_DOC_INSTRUCTIONS[doc_type])
    lines.append("")
    lines.append(
        "Format the output as well-structured Markdown suitable for an SDLC document.\n"
        "At the very end of your response, on its own line, include a confidence assessment:\n"
        "Confidence: X.XX\n"
        "where X.XX is a float between 0.0 and 1.0 indicating how confident you are "
        "in the completeness and accuracy of this document based on the available evidence."
    )

    return "\n".join(lines)


def _parse_rollup(text: str, domain_name: str, doc_type: str) -> tuple[str, float]:
    """Parse LLM rollup response.
    Extract markdown content and confidence score.
    If LLM included a confidence line (e.g., "Confidence: 0.8"), extract it.
    Default confidence: 0.7
    Returns (content_md, confidence).
    """
    confidence = 0.7
    content = text.strip()

    # Look for confidence line at the end
    match = re.search(r"[Cc]onfidence:\s*([\d.]+)\s*$", content, re.MULTILINE)
    if match:
        try:
            parsed = float(match.group(1))
            if 0.0 <= parsed <= 1.0:
                confidence = parsed
        except ValueError:
            pass
        # Remove the confidence line from content
        content = content[: match.start()].rstrip()

    return content, confidence


def _generate_single_doc(
    domain: Domain,
    doc_type: str,
    summaries: dict[str, dict],
    flows: list[BusinessFlow],
    llm_client: LLMClient,
    db_path: Path | None = None,
) -> RollupResult:
    """Generate one doc type for a domain. Called concurrently."""
    rag_context = ""
    if db_path is not None:
        rag_context = _retrieve_tier3_context(
            domain.name, doc_type, db_path, llm_client, top_k=5
        )
    prompt = _build_rollup_prompt(domain, doc_type, summaries, flows, rag_context=rag_context)
    response = llm_client.invoke_with_advisor(
        "tier3", prompt, max_tokens=4096,
        context=AdvisorContext(
            complexity="high",
            domain="doc_generation",
            max_advisor_cost_pct=0.5  # willing to spend up to 50% on advisor for doc quality
        )
    )
    content_md, confidence = _parse_rollup(response.text, domain.name, doc_type)
    label = _DOC_TYPE_LABELS.get(doc_type, doc_type)
    title = f"{domain.name} \u2014 {label}"
    logger.info(
        "Generated %s for domain '%s' (confidence=%.2f, model=%s, rag_chunks=%d)",
        doc_type, domain.name, confidence, response.model, rag_context.count("###"),
    )
    return RollupResult(
        domain=domain.name,
        doc_type=doc_type,
        title=title,
        content_md=content_md,
        confidence=confidence,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        model=response.model,
    )


def generate_domain_docs(
    domain: Domain,
    summaries: dict[str, dict],
    flows: list[BusinessFlow],
    llm_client: LLMClient,
    doc_types: tuple[str, ...] = DOC_TYPES,
    db_path: Path | None = None,
) -> list[RollupResult]:
    """Generate all doc types for a single domain using Tier 3 LLM.
    Doc types are generated concurrently (independent prompts, same context).
    Returns list of RollupResult (one per doc_type), ordered as DOC_TYPES.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[RollupResult | None] = [None] * len(doc_types)

    with ThreadPoolExecutor(max_workers=len(doc_types)) as executor:
        futures = {
            executor.submit(
                _generate_single_doc, domain, doc_type, summaries, flows, llm_client, db_path
            ): i
            for i, doc_type in enumerate(doc_types)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                doc_type = doc_types[idx]
                logger.error("Rollup failed for %s/%s: %s", domain.name, doc_type, exc)

    return [r for r in results if r is not None]


def _load_rollups_from_db(scan_id: int, db_path: Path) -> list[RollupResult]:
    """Load existing RollupResult objects from generated_docs for a scan."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT domain, doc_type, title, content_md, confidence, "
            "unverified_claims FROM generated_docs WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
        return [
            RollupResult(
                domain=r["domain"],
                doc_type=r["doc_type"],
                title=r["title"],
                content_md=r["content_md"] or "",
                confidence=r["confidence"] or 0.7,
                tokens_in=0,
                tokens_out=0,
                model="(resumed)",
                unverified_claims=r["unverified_claims"] or 0,
            )
            for r in rows
        ]
    finally:
        conn.close()


def generate_all_docs(
    domains: list[Domain],
    summaries: dict[str, dict],
    flows_by_domain: dict[str, list[BusinessFlow]],
    llm_client: LLMClient,
    on_progress: Callable | None = None,
    max_workers: int = 16,
    db_path: Path | None = None,
    scan_id: int | None = None,
) -> list[RollupResult]:
    """Generate docs for all domains. All (domain, doc_type) pairs run concurrently.
    Calls on_progress(completed, total) where total = len(domains) * len(DOC_TYPES).
    Returns flat list of RollupResult.

    If db_path and scan_id are provided, (domain, doc_type) pairs already present
    in generated_docs are skipped (resume support) and loaded from DB instead.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Resume guard: find already-generated (domain, doc_type) pairs
    done_pairs: set[tuple[str, str]] = set()
    resumed: list[RollupResult] = []
    if db_path is not None and scan_id is not None:
        try:
            conn = get_conn(db_path)
            try:
                rows = conn.execute(
                    "SELECT domain, doc_type FROM generated_docs WHERE scan_id = ?",
                    (scan_id,),
                ).fetchall()
                done_pairs = {(r["domain"], r["doc_type"]) for r in rows}
            finally:
                conn.close()
            if done_pairs:
                resumed = _load_rollups_from_db(scan_id, db_path)
                logger.info("Tier 3 resume: %d docs already generated, skipping", len(done_pairs))
        except Exception:
            pass

    tasks = [
        (domain, doc_type)
        for domain in domains
        for doc_type in DOC_TYPES
        if (domain.name, doc_type) not in done_pairs
    ]
    total = len(tasks) + len(resumed)
    results: list[RollupResult] = list(resumed)
    completed = len(resumed)

    # Report already-done items to progress callback
    if on_progress is not None and completed > 0:
        on_progress(completed, total)

    if not tasks:
        return results

    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(
                _generate_single_doc,
                domain,
                doc_type,
                summaries,
                flows_by_domain.get(domain.name, []),
                llm_client,
                db_path,
            ): (domain, doc_type)
            for domain, doc_type in tasks
        }
        for future in as_completed(futures):
            domain, doc_type = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.error("Rollup failed for %s/%s: %s", domain.name, doc_type, exc)
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

    return results


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug


def persist_rollups(
    rollups: list[RollupResult],
    scan_id: int,
    db_path: Path,
    project_slug: str,
) -> None:
    """Write rollup results to generated_docs table.
    Generate doc_id as: f"{project_slug}-{domain}-{doc_type}" (slugified).
    Generate title as: f"{domain} -- {doc_type_label}" (human readable).
    """
    conn = get_conn(db_path)
    try:
        for rollup in rollups:
            doc_id = _slugify(f"{project_slug}-{rollup.domain}-{rollup.doc_type}")
            conn.execute(
                """INSERT OR REPLACE INTO generated_docs
                   (scan_id, domain, doc_type, doc_id, title, content_md,
                    confidence, unverified_claims, push_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    rollup.domain,
                    rollup.doc_type,
                    doc_id,
                    rollup.title,
                    rollup.content_md,
                    rollup.confidence,
                    0,
                    "local",
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()
