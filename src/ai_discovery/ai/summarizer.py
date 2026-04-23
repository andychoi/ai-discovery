"""Tier 1 chunk summarizer — uses cheap LLM (Haiku) to summarize each code chunk."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..ai.llm_client import LLMClient
from ..db import get_conn, now_iso
from ..graph.models import CodeChunk

logger = logging.getLogger(__name__)

# Node types that get full LLM summarization
_SUMMARIZE_TYPES = frozenset({"class", "method", "endpoint", "function", "batch_job", "ui_component"})

# Node types that get structural-only summary (no LLM call)
_SKIP_TYPES = frozenset({"dto", "enum", "constant", "import"})

# Directory names that indicate test code.
_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})


def _is_test_chunk(file_path: str) -> bool:
    """Return True if *file_path* looks like a test file.

    Tests are parsed (so call edges from tests into production code are kept),
    but skipped at Tier 1 summarization — their summaries add little value for
    BPMN/DMN/EARS generation while often being 50%+ of chunk volume.
    """
    parts = file_path.replace("\\", "/").split("/")
    if any(p in _TEST_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith("_tests")
        or stem.endswith(".test")
        or stem.endswith(".spec")
        or stem.endswith("Test")
        or stem.endswith("Tests")
    )


def _build_prompt(chunk: CodeChunk, rag_context: str = "") -> str:
    """Build Tier 1 summarization prompt."""
    rag_section = ""
    if rag_context:
        rag_section = (
            "\n\n## Related code context (for grounding)\n"
            f"{rag_context}\n"
        )

    return f"""Analyze the following {chunk.language} code chunk and return a JSON object with exactly these fields:

- "purpose": A concise 1-2 sentence description of what this code does and why it exists.
- "business_rules": Key business rules or domain logic encoded in this code. Use "None detected" if none.
- "io_summary": Inputs, outputs, side effects (DB writes, API calls, file I/O, events emitted).
- "tech_debt_signals": Any code smells, TODOs, complexity issues, or missing error handling. Use "None detected" if none.

Return ONLY valid JSON, no markdown fences, no extra text.

## Code chunk
- File: {chunk.file_path}
- Type: {chunk.chunk_type}
- Name: {chunk.qualified_name}
- Language: {chunk.language}
- Annotations: {', '.join(chunk.annotations) if chunk.annotations else 'none'}
- Calls: {', '.join(chunk.calls[:10]) if chunk.calls else 'none'}

```{chunk.language}
{chunk.text}
```{rag_section}"""


def _parse_response(text: str) -> dict:
    """Parse LLM JSON response. Return dict with purpose, business_rules, io_summary, tech_debt_signals.
    Gracefully handle non-JSON responses by extracting what we can."""
    fields = ("purpose", "business_rules", "io_summary", "tech_debt_signals")
    defaults = {f: "" for f in fields}

    # Strip markdown code fences if present
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return {f: str(parsed.get(f, "")) for f in fields}
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: try to extract fields from plain text
    result = dict(defaults)
    for field in fields:
        pattern = rf'["\']?{field}["\']?\s*[:=]\s*["\']?(.+?)(?:["\']?\s*[,}}\n]|$)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result[field] = match.group(1).strip().strip("\"'")

    # If nothing was extracted, put the whole response in purpose
    if not any(result.values()):
        result["purpose"] = text.strip()[:500]

    return result


def summarize_chunk(
    chunk: CodeChunk,
    llm_client: LLMClient,
    db_path: Path | None = None,
    query_vec: list[float] | None = None,
    skip_rag: bool = False,
) -> dict:
    """Summarize a single chunk using Tier 1 LLM.

    Optionally retrieves RAG context from db_path if provided.
    If query_vec is provided, it is forwarded to search() to skip re-embedding.
    If skip_rag is True, skip RAG retrieval entirely (faster, no grounding context).
    Returns dict with: purpose, business_rules, io_summary, tech_debt_signals,
                       tokens_in, tokens_out, model, tier, qualified_name, raw_response
    """
    rag_context = ""
    if db_path is not None and not skip_rag:
        try:
            from ..rag.retriever import search

            results = search(chunk.qualified_name, db_path, llm_client, top_k=3, query_vec=query_vec)
            if results:
                snippets = []
                for r in results:
                    snippets.append(
                        f"// {r['qualified_name']} ({r['file_path']})\n"
                        f"{r['chunk_text'][:500]}"
                    )
                rag_context = "\n\n".join(snippets)
        except Exception:
            logger.debug("RAG retrieval failed for %s, proceeding without context", chunk.qualified_name)

    prompt = _build_prompt(chunk, rag_context)
    response = llm_client.invoke("tier1", prompt, max_tokens=1024)

    parsed = _parse_response(response.text)
    return {
        **parsed,
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "model": response.model,
        "tier": response.tier,
        "qualified_name": chunk.qualified_name,
        "raw_response": response.text,
    }


def summarize_chunks(
    chunks: list[CodeChunk],
    llm_client: LLMClient,
    db_path: Path | None = None,
    max_concurrent: int = 10,
    on_progress: Callable | None = None,
    scan_id: int | None = None,
    skip_rag: bool = False,
    skip_tests: bool = False,
) -> list[dict]:
    """Summarize multiple chunks concurrently using ThreadPoolExecutor.

    - Skip chunks whose chunk_type is in _SKIP_TYPES
    - Deduplicate by qualified_name
    - Skip chunks already summarized in the DB (resume support, requires scan_id + db_path)
    - Pre-embed all qualified names for RAG context to avoid N embedding calls in threads
    - Use max_concurrent workers
    - Call on_progress(completed, total) after each chunk
    - If skip_rag is True, bypass RAG context retrieval (faster, no grounding);
      db_path is still used for the resume guard.
    - If skip_tests is True, bypass LLM summarization for test files (still parsed
      into the graph; just no Tier 1 summary).
    - Return list of summary dicts
    """
    to_summarize = [c for c in chunks if c.chunk_type not in _SKIP_TYPES]
    if skip_tests:
        to_summarize = [c for c in to_summarize if not _is_test_chunk(c.file_path)]

    # Dedup by qualified_name
    seen: set[str] = set()
    deduped = []
    for c in to_summarize:
        if c.qualified_name not in seen:
            deduped.append(c)
            seen.add(c.qualified_name)
    to_summarize = deduped

    # Resume guard: filter out chunks already summarized in the DB. A DB error
    # here is load-bearing — silently falling through would re-summarize every
    # chunk on a resume, wasting a full Tier 1 budget. Log and re-raise.
    if scan_id is not None and db_path is not None:
        try:
            conn = get_conn(db_path)
            try:
                rows = conn.execute(
                    """SELECT cn.qualified_name
                         FROM node_summaries ns
                         JOIN code_nodes cn ON cn.id = ns.node_id
                        WHERE cn.scan_id = ?""",
                    (scan_id,),
                ).fetchall()
                existing_qnames = {r["qualified_name"] for r in rows}
            finally:
                conn.close()
        except Exception:
            logger.exception(
                "Resume guard DB query failed for scan_id=%s; aborting so the "
                "caller can decide whether to re-summarize or fix the DB.",
                scan_id,
            )
            raise

        to_summarize = [c for c in to_summarize if c.qualified_name not in existing_qnames]

        if not to_summarize:
            # All already summarized — load from DB and return
            conn = get_conn(db_path)
            try:
                rows = conn.execute(
                    """SELECT ns.*, cn.qualified_name
                         FROM node_summaries ns
                         JOIN code_nodes cn ON cn.id = ns.node_id
                        WHERE cn.scan_id = ?""",
                    (scan_id,),
                ).fetchall()
                return [
                    {
                        "purpose": r["purpose"],
                        "business_rules": r["business_rules"],
                        "io_summary": r["io_summary"],
                        "tech_debt_signals": r["tech_debt_signals"],
                        "tokens_in": r["tokens_in"],
                        "tokens_out": r["tokens_out"],
                        "model": r["model_used"],
                        "tier": r["tier"],
                        "qualified_name": r["qualified_name"],
                        "raw_response": r["raw_response"],
                    }
                    for r in rows
                ]
            finally:
                conn.close()

    total = len(to_summarize)
    results: list[dict] = []
    completed = 0

    if total == 0:
        return results

    # Pre-embed all qualified names concurrently to avoid N serial API calls before summarization.
    # Use as_completed so one slow/broken embedding doesn't block the whole phase (executor.map
    # yields in submission order and waits on the slowest item before any faster ones are usable).
    precomputed_vecs: dict[str, list[float] | None] = {}
    if db_path is not None and not skip_rag:
        with ThreadPoolExecutor(max_workers=max_concurrent) as embed_executor:
            futures = {
                embed_executor.submit(llm_client.get_embedding, chunk.qualified_name): chunk.qualified_name
                for chunk in to_summarize
            }
            for fut in as_completed(futures):
                qname = futures[fut]
                try:
                    precomputed_vecs[qname] = fut.result()
                except Exception:
                    logger.debug("Failed to pre-embed %s, will embed on demand", qname)
                    precomputed_vecs[qname] = None

    with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        future_to_chunk = {
            executor.submit(
                summarize_chunk,
                chunk,
                llm_client,
                db_path,
                precomputed_vecs.get(chunk.qualified_name),
                skip_rag,
            ): chunk
            for chunk in to_summarize
        }
        for future in as_completed(future_to_chunk):
            chunk = future_to_chunk[future]
            try:
                summary = future.result()
                results.append(summary)
            except Exception:
                logger.exception("Failed to summarize chunk %s", chunk.qualified_name)
                results.append({
                    "purpose": "Summarization failed",
                    "business_rules": "",
                    "io_summary": "",
                    "tech_debt_signals": "",
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "model": "",
                    "tier": "tier1",
                    "qualified_name": chunk.qualified_name,
                    "raw_response": "",
                })
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

    return results


def persist_summaries(
    summaries: list[dict],
    scan_id: int,
    db_path: Path,
) -> None:
    """Write summaries to node_summaries table.

    Maps qualified_name to node_id via code_nodes table.
    Summaries without a matching code_node are logged and skipped.
    """
    conn = get_conn(db_path)
    try:
        # Batch SELECT to resolve all qualified_names -> node ids in one query (fixes N+1)
        qnames = [s.get("qualified_name", "") for s in summaries if s.get("qualified_name")]
        if not qnames:
            return

        placeholders = ",".join("?" * len(qnames))
        rows = conn.execute(
            f"SELECT id, qualified_name FROM code_nodes WHERE scan_id = ? AND qualified_name IN ({placeholders})",
            [scan_id, *qnames],
        ).fetchall()
        qname_to_id = {r["qualified_name"]: r["id"] for r in rows}

        for s in summaries:
            qname = s.get("qualified_name", "")
            node_id = qname_to_id.get(qname)
            if node_id is None:
                logger.warning("No code_node found for qualified_name=%s, skipping", qname)
                continue
            conn.execute(
                """INSERT OR REPLACE INTO node_summaries
                   (node_id, tier, model_used, purpose, business_rules,
                    io_summary, tech_debt_signals, raw_response,
                    tokens_in, tokens_out, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node_id,
                    s.get("tier", "tier1"),
                    s.get("model", ""),
                    s.get("purpose", ""),
                    s.get("business_rules", ""),
                    s.get("io_summary", ""),
                    s.get("tech_debt_signals", ""),
                    s.get("raw_response", ""),
                    s.get("tokens_in", 0),
                    s.get("tokens_out", 0),
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()
