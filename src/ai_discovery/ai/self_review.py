"""Self-review -- extract claims from generated docs and verify against source code via RAG."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import TimeoutError as _FuturesTimeout
from dataclasses import dataclass
from pathlib import Path

from ..ai.llm_client import LLMClient, AdvisorContext
from ..db import get_conn, now_iso

logger = logging.getLogger(__name__)


@dataclass
class ReviewClaim:
    claim_text: str
    status: str  # "verified", "unverified", "contradicted"
    evidence: str = ""  # matching source code snippet
    source_file: str = ""


def extract_claims(content_md: str, llm_client: LLMClient) -> list[str]:
    """Use Tier 1 LLM to extract factual/technical claims from generated markdown.

    Ask LLM to identify specific, verifiable claims like:
    - "The OrderService class handles payment processing"
    - "The /api/users endpoint accepts POST requests"
    - "The User entity has a foreign key to Organization"

    Return list of claim strings.
    """
    prompt = (
        "You are a technical reviewer. Extract all specific, verifiable factual claims "
        "from the following generated documentation. Each claim should be a concrete "
        "statement about the codebase that can be checked against source code.\n\n"
        "Return ONLY a JSON array of claim strings. No other text.\n\n"
        "Examples of good claims:\n"
        '- "The OrderService class handles payment processing"\n'
        '- "The /api/users endpoint accepts POST requests"\n'
        '- "The User entity has a foreign key to Organization"\n\n'
        "Document:\n"
        f"{content_md}\n\n"
        "JSON array of claims:"
    )

    response = llm_client.invoke_with_advisor(
        "tier1", prompt, max_tokens=2048,
        context=AdvisorContext(
            complexity="medium",
            domain="claim_extraction",
            max_advisor_cost_pct=0.1  # strict: keep advisor cost <10%
        )
    )
    text = response.text.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    import re as _re
    text = _re.sub(r"^```[a-z]*\n?", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"```$", "", text, flags=_re.MULTILINE)
    text = text.strip()

    # Try to parse JSON array directly
    try:
        claims = json.loads(text)
        if isinstance(claims, list):
            return [str(c) for c in claims if c]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: extract the [ ... ] block
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            claims = json.loads(text[start : end + 1])
            if isinstance(claims, list):
                return [str(c) for c in claims if c]
        except (json.JSONDecodeError, TypeError):
            pass

    # Last resort: truncated response — close the array and parse what we have.
    # LLMs sometimes cut off mid-item; drop the incomplete last element.
    if start != -1:
        fragment = text[start:]
        last_comma = fragment.rfind(",")
        if last_comma != -1:
            try:
                claims = json.loads(fragment[: last_comma] + "]")
                if isinstance(claims, list):
                    logger.warning("Partial claims parse (%d items) — response was truncated", len(claims))
                    return [str(c) for c in claims if c]
            except (json.JSONDecodeError, TypeError):
                pass

    logger.warning("Failed to parse claims from LLM response: %s", text[:200])
    return []


def verify_claim(
    claim: str,
    db_path: Path,
    llm_client: LLMClient,
    top_k: int = 3,
    query_vec: list[float] | None = None,
) -> ReviewClaim:
    """Verify a single claim against RAG-indexed source code.

    1. Search RAG for code related to the claim (uses pre-computed query_vec if provided)
    2. If no results -> status="unverified"
    3. If results found -> use Tier 1 LLM to check if code supports the claim
       - "verified" if code clearly supports it
       - "contradicted" if code contradicts it
       - "unverified" if code is ambiguous/unrelated
    4. Store the best matching source snippet as evidence
    """
    from ..rag.retriever import search

    results = search(claim, db_path, llm_client, top_k=top_k, query_vec=query_vec)

    if not results:
        return ReviewClaim(claim_text=claim, status="unverified")

    # Build context from RAG results
    code_snippets = []
    best_file = results[0].get("file_path", "")
    for r in results:
        snippet = f"File: {r.get('file_path', 'unknown')}\n"
        snippet += f"Symbol: {r.get('qualified_name', 'unknown')}\n"
        snippet += f"Code:\n{r.get('chunk_text', '')}\n"
        code_snippets.append(snippet)

    context = "\n---\n".join(code_snippets)

    prompt = (
        "You are verifying a factual claim from generated documentation against actual source code.\n\n"
        f"Claim: {claim}\n\n"
        f"Source code evidence:\n{context}\n\n"
        "Based on the source code, does the code support this claim?\n"
        "Respond with EXACTLY one word: verified, contradicted, or unverified\n"
        "- verified: the code clearly supports the claim\n"
        "- contradicted: the code clearly contradicts the claim\n"
        "- unverified: the code is ambiguous or unrelated to the claim"
    )

    response = llm_client.invoke_with_advisor(
        "tier1", prompt, max_tokens=64,
        context=AdvisorContext(
            complexity="low",
            domain="claim_verification",
            max_advisor_cost_pct=0.05  # almost never escalate for binary verdict
        )
    )
    answer = response.text.strip().lower()

    if "verified" in answer and "unverified" not in answer:
        status = "verified"
    elif "contradicted" in answer:
        status = "contradicted"
    else:
        status = "unverified"

    # Use best matching snippet as evidence
    best_evidence = results[0].get("chunk_text", "")

    return ReviewClaim(
        claim_text=claim,
        status=status,
        evidence=best_evidence,
        source_file=best_file,
    )


def review_document(
    content_md: str,
    db_path: Path,
    llm_client: LLMClient,
    max_claims: int = 10,
    max_workers: int = 5,
    on_total_known: Callable[[int], None] | None = None,
    on_claim_done: Callable[["ReviewClaim"], None] | None = None,
) -> list[ReviewClaim]:
    """Full self-review pipeline for one document.

    1. Extract claims (capped at max_claims)
    2. Pre-embed all claim strings to avoid N embedding calls during verification
    3. Verify all claims concurrently against RAG
    4. Return list of ReviewClaim in original order

    Callbacks (called from worker threads — must be thread-safe):
      on_total_known(n)   — called once after extraction, with the capped claim count
      on_claim_done(claim) — called after each claim is verified
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    claims = extract_claims(content_md, llm_client)[:max_claims]
    if not claims:
        if on_total_known:
            on_total_known(0)
        return []

    if on_total_known:
        on_total_known(len(claims))

    # Pre-embed all claim strings to avoid N embedding calls inside verify_claim
    claim_vecs: dict[str, list[float]] = {}
    for claim in claims:
        try:
            claim_vecs[claim] = llm_client.get_embedding(claim)
        except Exception as exc:
            logger.warning("Failed to pre-embed claim: %s", exc)

    reviewed: list[ReviewClaim | None] = [None] * len(claims)

    # 60s per claim, hard cap at 5 min total
    _claim_timeout = max(60, len(claims) * 10)

    executor = ThreadPoolExecutor(max_workers=min(max_workers, len(claims)))
    try:
        futures = {
            executor.submit(verify_claim, claim, db_path, llm_client, query_vec=claim_vecs.get(claim)): i
            for i, claim in enumerate(claims)
        }
        try:
            for future in as_completed(futures, timeout=_claim_timeout):
                idx = futures[future]
                try:
                    reviewed[idx] = future.result()
                    logger.debug("Claim '%s' -> %s", claims[idx][:60], reviewed[idx].status)
                    if on_claim_done:
                        on_claim_done(reviewed[idx])
                except Exception as exc:
                    logger.warning("Claim verification failed for '%s': %s", claims[idx][:60], exc)
        except _FuturesTimeout:
            logger.warning("Claim verification timed out after %ds — %d/%d claims completed",
                           _claim_timeout, sum(1 for r in reviewed if r is not None), len(claims))
            for f in futures:
                f.cancel()
    finally:
        executor.shutdown(wait=False)  # don't block on hung LLM threads

    return [r for r in reviewed if r is not None]


def persist_claims(
    claims: list[ReviewClaim],
    doc_db_id: int,
    db_path: Path,
) -> None:
    """Write review claims to review_claims table."""
    conn = get_conn(db_path)
    try:
        for claim in claims:
            conn.execute(
                """INSERT INTO review_claims (doc_id, claim_text, status, evidence, source_file, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    doc_db_id,
                    claim.claim_text,
                    claim.status,
                    claim.evidence,
                    claim.source_file,
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def annotate_document(content_md: str, claims: list[ReviewClaim]) -> str:
    """Append a Self-Review section to the document body.

    Only unverified and contradicted claims are shown — verified claims pass
    silently. If there are no claims at all, the document is returned unchanged.
    """
    if not claims:
        return content_md

    contradicted = [c for c in claims if c.status == "contradicted"]
    unverified = [c for c in claims if c.status == "unverified"]
    verified_count = sum(1 for c in claims if c.status == "verified")

    lines = [
        "",
        "---",
        "",
        "## Self-Review Notes",
        "",
        "> *Auto-generated by discovery pipeline — claims verified against source code via RAG.*",
        "",
    ]

    if not contradicted and not unverified:
        lines.append(f"✅ All {len(claims)} extracted claims verified against source code.")
        return content_md + "\n".join(lines) + "\n"

    if contradicted:
        lines += [
            "### ❌ Contradicted Claims",
            "",
            "Source code evidence suggests these claims may be incorrect:",
            "",
        ]
        for c in contradicted:
            lines.append(f'- **"{c.claim_text}"**')
            if c.source_file:
                lines.append(f'  - Evidence in `{c.source_file}`')
        lines.append("")

    if unverified:
        lines += [
            "### ⚠️ Unverified Claims",
            "",
            "No matching source code found to confirm these claims:",
            "",
        ]
        for c in unverified:
            lines.append(f'- "{c.claim_text}"')
        lines.append("")

    lines.append(f"*{verified_count}/{len(claims)} claims verified.*")

    return content_md + "\n".join(lines) + "\n"


def get_review_summary(claims: list[ReviewClaim]) -> dict:
    """Return summary: {verified: N, unverified: N, contradicted: N, total: N, confidence: float}.
    confidence = verified / total (or 1.0 if no claims).
    """
    total = len(claims)
    if total == 0:
        return {
            "verified": 0,
            "unverified": 0,
            "contradicted": 0,
            "total": 0,
            "confidence": 1.0,
        }

    verified = sum(1 for c in claims if c.status == "verified")
    unverified = sum(1 for c in claims if c.status == "unverified")
    contradicted = sum(1 for c in claims if c.status == "contradicted")

    return {
        "verified": verified,
        "unverified": unverified,
        "contradicted": contradicted,
        "total": total,
        "confidence": round(verified / total, 2),
    }


def _find_section_for_claim(content_md: str, claim_text: str) -> str | None:
    """Find the ## section heading that contains a claim.

    Returns the heading text (without ##) or None if not found.
    """
    sections = re.split(r"(?=^## )", content_md, flags=re.MULTILINE)
    for section in sections:
        if claim_text[:60] in section:  # fuzzy match on first 60 chars
            heading_match = re.match(r"^## (.+)", section)
            if heading_match:
                return heading_match.group(1).strip()
    return None


def regenerate_sections(
    content_md: str,
    claims: list[ReviewClaim],
    llm_client: "LLMClient",
    db_path: "Path | None" = None,
) -> str:
    """Re-generate only sections that contain unverified or contradicted claims.

    For each affected section:
    1. Optionally retrieve RAG context for the section heading
    2. Ask LLM to rewrite the section using available evidence
    3. Replace the section in the original document

    Returns the updated markdown.
    """
    bad_claims = [c for c in claims if c.status in ("unverified", "contradicted")]
    if not bad_claims:
        return content_md

    # Map sections to their bad claims
    sections_to_fix: dict[str, list[ReviewClaim]] = {}
    for claim in bad_claims:
        heading = _find_section_for_claim(content_md, claim.claim_text)
        if heading:
            sections_to_fix.setdefault(heading, []).append(claim)

    if not sections_to_fix:
        return content_md

    # Split document into sections
    parts = re.split(r"(?=^## )", content_md, flags=re.MULTILINE)
    result_parts = []

    for part in parts:
        heading_match = re.match(r"^## (.+)", part)
        if heading_match and heading_match.group(1).strip() in sections_to_fix:
            heading = heading_match.group(1).strip()
            bad = sections_to_fix[heading]

            # Build re-gen prompt
            rag_context = ""
            if db_path is not None:
                try:
                    from ..rag.retriever import search
                    results = search(heading, db_path, llm_client, top_k=3)
                    if results:
                        rag_context = "\n".join(
                            f"// {r['qualified_name']}\n{r['chunk_text'][:500]}"
                            for r in results
                        )
                except Exception:
                    pass

            claim_list = "\n".join(f"- {c.claim_text} [{c.status}]" for c in bad)
            prompt = (
                f"Rewrite the following documentation section. Some claims were "
                f"unverified against source code. Remove or correct them.\n\n"
                f"## {heading}\n{part}\n\n"
                f"Unverified claims:\n{claim_list}\n"
            )
            if rag_context:
                prompt += f"\nRelevant source code:\n{rag_context}\n"
            prompt += (
                "\nRewrite only this section. Keep the ## heading. "
                "Only include statements supported by the code context above."
            )

            response = llm_client.invoke_with_advisor(
                "tier1", prompt, max_tokens=1024,
                context=AdvisorContext(
                    complexity="medium",
                    domain="section_regeneration",
                    max_advisor_cost_pct=0.15
                )
            )
            result_parts.append(response.text.strip() + "\n\n")
        else:
            result_parts.append(part)

    return "".join(result_parts)
