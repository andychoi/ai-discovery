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


class RollupTotalFailureError(RuntimeError):
    """Raised when every attempted Tier-3 doc rollup fails (0 succeeded).

    Total failure is almost always a misconfiguration (e.g. the tier3 model id
    is not enabled in this Bedrock account/region — 'model identifier is
    invalid'), not a per-document problem. Without this, generate_all_docs would
    swallow each failure and return an empty list, the pipeline would record
    phase 14 complete, and the scan would exit 0 with missing ASIS/ASD/ASSC
    docs. Raising makes the failure loud and produces a non-zero exit.
    """


# Doc types generated per domain (Phase 0: Discovery)
DOC_TYPES = ("as-is", "as-is-detail", "as-is-schema")

# Confidence for a doc with nothing scorable (no AST rows, no extractable
# claims). Such a doc is unverifiable — not certain — so it scores low. See
# blend_confidence (HIGH-1 fix).
UNVERIFIABLE_CONFIDENCE = 0.3

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


def _build_verified_api_table(domain: Domain) -> tuple[str, int] | None:
    """Build a deterministic markdown table of HTTP endpoints from parser-extracted
    framework_hints. Returns (markdown, row_count) or None when the domain
    exposes no endpoints.

    Track 1 (verified-facts injection): the parser already extracts
    `route` + `method` accurately on endpoint nodes; we surface them as a
    non-overridable table so the LLM cannot rewrite paths from REST priors.

    The row count flows into Track 4's confidence blending — every row here
    counts as verified (confidence 1.0) when computing doc-level confidence.
    """
    rows: list[tuple[str, str, str, str]] = []
    for node in sorted(domain.nodes, key=lambda n: n.qualified_name):
        if node.node_type != "endpoint":
            continue
        hints = node.framework_hints or {}
        method = hints.get("method") or hints.get("http_method")
        route = hints.get("route") or hints.get("path")
        if method and route:
            src = f"{node.file_path}:{node.line_start}"
            rows.append((node.qualified_name, str(method), str(route), src))
    if not rows:
        return None

    lines = [
        "## API Surface (verified)",
        "",
        "*Extracted verbatim from source AST annotations — `✓ AST` marks provenance "
        "(faithful to source), not independent validation of correctness.*",
        "",
        "| Handler | HTTP | Path | Source | Provenance |",
        "|---|---|---|---|---|",
    ]
    for qn, method, route, src in rows:
        lines.append(f"| `{qn}` | {method} | `{route}` | `{src}` | ✓ AST |")
    lines.append("")
    return "\n".join(lines), len(rows)


def _build_verified_schema_table(domain: Domain) -> tuple[str, int] | None:
    """Build a deterministic markdown block of entities + fields from parser
    output. Returns (markdown, total_field_count) or None when the domain
    has no db_model nodes with fields.

    Track 1: same rationale as the API table — entity names, field lists,
    and base-class relationships are AST-extracted; the LLM should not
    rephrase them or invent missing fields. Track 4: each entity counts as
    one verified row in confidence blending (not one per field, since the
    LLM-claim review extracts entity-level claims, not field-level).
    """
    entries: list[tuple[str, str, list[str], list[str], str]] = []
    for node in sorted(domain.db_models, key=lambda n: n.qualified_name):
        if not node.fields:
            continue
        src = f"{node.file_path}:{node.line_start}"
        entries.append((node.qualified_name, node.name, list(node.fields), list(node.bases or []), src))
    if not entries:
        return None

    lines = [
        "## Entity Schema (verified)",
        "",
        "*Extracted verbatim from source AST — `✓ AST` marks provenance (faithful "
        "to source), not independent validation of correctness.*",
        "",
    ]
    for qn, name, fields, bases, src in entries:
        extends = f" — extends `{', '.join(bases)}`" if bases else ""
        lines.append(f"### `{name}` &nbsp;<sub>✓ AST</sub>")
        lines.append(f"Source: `{src}`{extends}")
        lines.append("")
        lines.append("**Fields:**")
        for f in fields:
            lines.append(f"- `{f}`")
        lines.append("")
    return "\n".join(lines), len(entries)


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
    confidence: float  # 0.0-1.0, from LLM self-assessment, decayed by self-review
    tokens_in: int
    tokens_out: int
    model: str
    unverified_claims: int = 0  # set by self-review step before writing markdown
    # Track 4: count of rows injected from AST-verified tables (endpoints,
    # entities). These are deterministic ground truth — counted as verified
    # when blending with prose-claim review verdicts to compute doc confidence.
    verified_row_count: int = 0


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
        # HIGH-8: these are calls leaving this domain — mostly to OTHER internal
        # domains, plus unresolved calls. They are not necessarily external
        # *systems*; labeling them "External Dependencies" overstated the system's
        # outward surface. (First-class external-system nodes are future work.)
        lines.append("## Cross-Domain & Outbound Calls")
        lines.append("")
        lines.append("*Calls that leave this domain — to other internal domains or unresolved targets. Not necessarily external systems.*")
        lines.append("")
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

    # Verified facts (Track 1): AST-extracted endpoints and entities.
    # These are deterministic ground truth — the LLM must not rewrite them.
    api_result = _build_verified_api_table(domain)
    schema_result = _build_verified_schema_table(domain)
    if api_result or schema_result:
        lines.append("## VERIFIED FACTS (do not modify)")
        lines.append(
            "The following are extracted directly from source AST. They will be "
            "injected verbatim into the final document. Do NOT include duplicate "
            "API or entity tables in your output. You may reference these facts in "
            "prose, but always reproduce HTTP verbs, paths, entity names, and "
            "field names exactly as shown — never infer alternatives.\n"
        )
        if api_result:
            lines.append(api_result[0])
        if schema_result:
            lines.append(schema_result[0])
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


def blend_confidence(verified_row_count: int, review_summary: dict) -> float:
    """Track 4: compute doc-level confidence from AST-verified rows + review verdicts.

    AST-verified rows (endpoints, entity field lists) count as confidence 1.0
    each — they're deterministic. Prose-claim review contributes per the
    verified/unverified/contradicted split: verified=1.0, unverified=0.5,
    contradicted=0.0.

    Returns a value in [0.0, 1.0]. If there's nothing to score (no AST rows AND
    no claims extracted), the doc is *unverifiable*, not certain — return a low
    confidence (HIGH-1 fix). Publishing 1.0 here meant a doc whose claims could
    not even be extracted shipped as maximally confident, which is exactly
    backwards for a trustworthiness signal.
    """
    n_ast = max(0, int(verified_row_count or 0))
    verified = int(review_summary.get("verified", 0))
    unverified = int(review_summary.get("unverified", 0))
    contradicted = int(review_summary.get("contradicted", 0))
    n_prose = verified + unverified + contradicted

    total = n_ast + n_prose
    if total == 0:
        return UNVERIFIABLE_CONFIDENCE

    score = (n_ast * 1.0) + (verified * 1.0) + (unverified * 0.5) + (contradicted * 0.0)
    return round(score / total, 2)


# Confidence for a doc whose prose has NOT been claim-verified — self-review was
# skipped (budget) or hasn't run yet. We must never publish the LLM's own
# self-asserted "Confidence: X.XX" (P1-b): a model rating its own output is not a
# trust signal. A doc backed by AST-verified facts but unreviewed prose earns a
# capped, middling score; one with no facts at all is unverifiable.
UNREVIEWED_WITH_FACTS_CONFIDENCE = 0.6


def unreviewed_confidence(verified_row_count: int) -> float:
    """Deterministic confidence for an as-yet-unreviewed doc (P1-b)."""
    return (
        UNREVIEWED_WITH_FACTS_CONFIDENCE
        if (verified_row_count or 0) > 0
        else UNVERIFIABLE_CONFIDENCE
    )


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
    # _parse_rollup also strips the LLM's trailing "Confidence: X.XX" line from
    # the content; we keep that but discard the self-asserted score (P1-b) — the
    # published confidence is derived deterministically below.
    content_md, _llm_self_confidence = _parse_rollup(response.text, domain.name, doc_type)

    # Track 1: prepend AST-verified facts to LLM output. Doc-type aware so each
    # doc gets the table that matches its purpose; `as-is` gets both as a quick
    # reference block at the top.
    # Track 4: count verified rows so confidence can be blended later.
    verified_blocks: list[str] = []
    verified_row_count = 0
    if doc_type in ("as-is", "as-is-detail"):
        api_result = _build_verified_api_table(domain)
        if api_result:
            verified_blocks.append(api_result[0])
            verified_row_count += api_result[1]
    if doc_type in ("as-is", "as-is-schema"):
        schema_result = _build_verified_schema_table(domain)
        if schema_result:
            verified_blocks.append(schema_result[0])
            verified_row_count += schema_result[1]
    if verified_blocks:
        content_md = "\n\n".join(verified_blocks) + "\n\n" + content_md

    # P1-b: publish a deterministic confidence, not the LLM's self-rating. When
    # self-review (phase 17) runs it overwrites this with the full blended score;
    # if it's skipped, this deterministic value stands instead of a fabricated 0.7.
    confidence = unreviewed_confidence(verified_row_count)

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
        verified_row_count=verified_row_count,
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
            "unverified_claims, verified_row_count FROM generated_docs WHERE scan_id = ?",
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
                verified_row_count=r["verified_row_count"] or 0,
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
    budget_exhausted: Callable[[], bool] | None = None,
) -> list[RollupResult]:
    """Generate docs for all domains. All (domain, doc_type) pairs run concurrently.
    Calls on_progress(completed, total) where total = len(domains) * len(DOC_TYPES).
    Returns flat list of RollupResult.

    If db_path and scan_id are provided, (domain, doc_type) pairs already present
    in generated_docs are skipped (resume support) and loaded from DB instead.

    If budget_exhausted() is provided and returns True after a doc completes, stop
    submitting/awaiting further docs (P1-e) — Tier-3 is the most expensive tier and
    a large domain count could otherwise blow past the budget within this phase.
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
        new_success = 0
        failures: list[str] = []
        last_exc: Exception | None = None
        budget_stopped = False
        for future in as_completed(futures):
            domain, doc_type = futures[future]
            try:
                results.append(future.result())
                new_success += 1
            except Exception as exc:
                last_exc = exc
                failures.append(f"{domain.name}/{doc_type}: {exc}")
                logger.error("Rollup failed for %s/%s: %s", domain.name, doc_type, exc)
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)
            # P1-e: stop mid-phase once the budget is spent; cancel not-yet-started docs.
            if budget_exhausted is not None and budget_exhausted():
                budget_stopped = True
                cancelled = sum(1 for f in futures if f.cancel())
                logger.warning(
                    "Tier-3 budget limit reached after %d/%d docs; skipping %d remaining",
                    completed, total, cancelled,
                )
                break

    # Fatal guard: tasks were attempted but produced zero docs (no new success
    # and nothing resumed from a prior run). This is the "tier3 model id invalid"
    # signature. Returning [] here would let the pipeline record phase 14
    # complete and exit 0 with no docs (see RollupTotalFailureError). A partial
    # failure (some succeeded, or resumed docs exist) is tolerated; a budget stop
    # (P1-e) is an intentional halt, not a failure, so it does not raise.
    if tasks and not results and not budget_stopped:
        detail = failures[0] if failures else "unknown error"
        raise RollupTotalFailureError(
            f"All {len(tasks)} Tier-3 doc rollups failed (0 succeeded). "
            f"First error: {detail}. Common cause: the tier3 model id is not "
            "enabled in this Bedrock account/region — check the scan log for "
            "'model identifier is invalid' and set bedrock.tier3p/tier3d in "
            "discovery.yaml to a model you can invoke."
        ) from last_exc

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
                    confidence, unverified_claims, verified_row_count,
                    push_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    rollup.domain,
                    rollup.doc_type,
                    doc_id,
                    rollup.title,
                    rollup.content_md,
                    rollup.confidence,
                    0,
                    rollup.verified_row_count,
                    "local",
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()
