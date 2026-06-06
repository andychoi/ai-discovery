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


# Structured-output schema for batched Tier-1 calls (A-1 semantic batching):
# one summary object per chunk, keyed by qualified_name so members can be
# matched back and missing ones re-summarized individually.
_BATCH_SUMMARY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "qualified_name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "business_rules": {"type": "string"},
                    "io_summary": {"type": "string"},
                    "tech_debt_signals": {"type": "string"},
                },
                "required": [
                    "qualified_name", "purpose", "business_rules",
                    "io_summary", "tech_debt_signals",
                ],
            },
        }
    },
    "required": ["summaries"],
}


def _build_batch_prompt(chunks: list[CodeChunk]) -> str:
    """Build one Tier-1 prompt covering a whole semantic batch.

    The instruction block is paid once per batch (not once per chunk), and the
    model sees the chunk's callers/callees from the same call-graph community —
    context a per-chunk prompt can't provide without RAG.
    """
    header = (
        f"Analyze the following {len(chunks)} code chunks. They belong to the "
        "same module community — they call or are called by each other, so use "
        "the surrounding chunks as context when describing each one.\n\n"
        "For EVERY chunk, emit one summary object with exactly these fields:\n"
        '- "qualified_name": the chunk\'s qualified name, copied exactly.\n'
        '- "purpose": a concise 1-2 sentence description of what the code does and why it exists.\n'
        '- "business_rules": key business rules or domain logic. Use "None detected" if none.\n'
        '- "io_summary": inputs, outputs, side effects (DB writes, API calls, file I/O, events).\n'
        '- "tech_debt_signals": code smells, TODOs, complexity, missing error handling. Use "None detected" if none.\n'
    )
    sections = []
    for i, chunk in enumerate(chunks, start=1):
        sections.append(
            f"## Chunk {i}\n"
            f"- File: {chunk.file_path}\n"
            f"- Type: {chunk.chunk_type}\n"
            f"- Name: {chunk.qualified_name}\n"
            f"- Language: {chunk.language}\n"
            f"- Annotations: {', '.join(chunk.annotations) if chunk.annotations else 'none'}\n"
            f"- Calls: {', '.join(chunk.calls[:10]) if chunk.calls else 'none'}\n\n"
            f"```{chunk.language}\n{chunk.text}\n```"
        )
    return header + "\n" + "\n\n".join(sections)


def _failed_summary(qualified_name: str) -> dict:
    return {
        "purpose": "Summarization failed",
        "business_rules": "",
        "io_summary": "",
        "tech_debt_signals": "",
        "tokens_in": 0,
        "tokens_out": 0,
        "model": "",
        "tier": "tier1",
        "qualified_name": qualified_name,
        "raw_response": "",
    }


def summarize_batch(
    chunks: list[CodeChunk],
    llm_client: LLMClient,
    db_path: Path | None = None,
    skip_rag: bool = True,
) -> list[dict]:
    """Summarize a semantic batch with ONE structured Tier-1 call.

    Members the model skipped (or the whole batch, if the structured call
    fails) fall back to individual `summarize_chunk` calls — batching is a
    cost optimization, never a coverage regression. Returns one summary dict
    per input chunk, same shape as `summarize_chunk`.
    """
    if not chunks:
        return []

    raw_items: list | None
    response = None
    try:
        response = llm_client.invoke_structured(
            "tier1",
            _build_batch_prompt(chunks),
            _BATCH_SUMMARY_SCHEMA,
            tool_name="emit_summaries",
            tool_description="Emit one summary object per code chunk.",
            max_tokens=4096,
        )
        raw_items = response.data.get("summaries") or []
    except Exception as exc:
        logger.warning(
            "Batched Tier-1 call failed for %d chunks (%s) — falling back to per-chunk",
            len(chunks), exc,
        )
        raw_items = None

    results: list[dict] = []
    missing: list[CodeChunk] = []
    if raw_items is None:
        missing = list(chunks)
    else:
        by_qname = {
            item["qualified_name"]: item
            for item in raw_items
            if isinstance(item, dict) and item.get("qualified_name")
        }
        for chunk in chunks:
            item = by_qname.get(chunk.qualified_name)
            if item is None:
                missing.append(chunk)
                continue
            results.append({
                "purpose": str(item.get("purpose", "")),
                "business_rules": str(item.get("business_rules", "")),
                "io_summary": str(item.get("io_summary", "")),
                "tech_debt_signals": str(item.get("tech_debt_signals", "")),
                "tokens_in": 0,  # split below
                "tokens_out": 0,
                "model": response.model,
                "tier": response.tier,
                "qualified_name": chunk.qualified_name,
                "raw_response": response.raw_text,
            })
        # Split the batch's token cost across matched members so per-node
        # ledger rows in node_summaries stay meaningful in aggregate.
        if results:
            per_in, rem_in = divmod(response.tokens_in, len(results))
            per_out, rem_out = divmod(response.tokens_out, len(results))
            for i, summary in enumerate(results):
                summary["tokens_in"] = per_in + (rem_in if i == 0 else 0)
                summary["tokens_out"] = per_out + (rem_out if i == 0 else 0)
        if missing:
            logger.warning(
                "Batched Tier-1 response missing %d/%d members — summarizing individually",
                len(missing), len(chunks),
            )

    for chunk in missing:
        try:
            results.append(summarize_chunk(chunk, llm_client, db_path, None, skip_rag))
        except Exception:
            logger.exception("Per-chunk fallback failed for %s", chunk.qualified_name)
            results.append(_failed_summary(chunk.qualified_name))
    return results


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
    budget_exhausted: Callable[[], bool] | None = None,
    call_edges: list | None = None,
    semantic_batching: bool = False,
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
    - If semantic_batching is True and call_edges are provided, group chunks by
      call-graph community (A-1 Louvain batching) and fire ONE structured call
      per batch instead of one call per chunk. Community context replaces RAG
      in this mode; per-chunk fallback covers failed/missing members.
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

    # A-1 semantic batching: one structured call per call-graph community.
    # Runs after all filters so dedup/resume/test-skip semantics are identical
    # to the per-chunk path.
    if semantic_batching and call_edges is not None:
        from .semantic_batching import compute_semantic_batches

        batches = compute_semantic_batches(to_summarize, call_edges)
        logger.info(
            "Tier-1 semantic batching: %d chunks -> %d batches (%s)",
            total, len(batches), batches[0].algorithm if batches else "n/a",
        )
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            future_to_batch = {
                executor.submit(summarize_batch, b.chunks, llm_client, db_path, True): b
                for b in batches
            }
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                try:
                    results.extend(future.result())
                except Exception:
                    logger.exception("Failed to summarize batch %d", batch.index)
                    results.extend(_failed_summary(c.qualified_name) for c in batch.chunks)
                completed += len(batch.chunks)
                if on_progress is not None:
                    on_progress(completed, total)
                # P1-e: stop mid-phase once the budget is spent; cancel queued batches.
                if budget_exhausted is not None and budget_exhausted():
                    cancelled = sum(1 for f in future_to_batch if f.cancel())
                    logger.warning(
                        "Tier-1 budget limit reached after %d/%d chunks; skipping %d remaining batches",
                        completed, total, cancelled,
                    )
                    break
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
            # P1-e: stop mid-phase once the budget is spent; cancel queued chunks.
            if budget_exhausted is not None and budget_exhausted():
                cancelled = sum(1 for f in future_to_chunk if f.cancel())
                logger.warning(
                    "Tier-1 budget limit reached after %d/%d chunks; skipping %d remaining",
                    completed, total, cancelled,
                )
                break

    return results


def reuse_prior_summaries(db_path: Path, scan_id: int) -> int:
    """A-2 incremental re-scan: copy Tier-1 summaries from the most recent
    prior scan for nodes whose (qualified_name, file_path, file_hash) are
    unchanged. Returns the number of summaries copied.

    Runs before summarize_chunks: the copied rows make the existing same-scan
    resume guard skip those nodes, so only changed/new files pay an LLM call.

    - Blank hashes never match (extractor nodes, pre-migration scans) — a
      blank == blank join would "reuse" across real changes.
    - Token counts are zeroed: this scan paid nothing for reused rows, and
      copying the prior counts would double-bill the aggregate ledger.
    - INSERT OR IGNORE: an existing summary for the node (same-scan resume)
      is never clobbered.
    """
    conn = get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT MAX(cn.scan_id) AS prev
                 FROM node_summaries ns
                 JOIN code_nodes cn ON cn.id = ns.node_id
                WHERE cn.scan_id < ?""",
            (scan_id,),
        ).fetchone()
        prev_scan = row["prev"] if row else None
        if prev_scan is None:
            return 0
        cur = conn.execute(
            """INSERT OR IGNORE INTO node_summaries
                   (node_id, tier, model_used, purpose, business_rules,
                    io_summary, tech_debt_signals, raw_response,
                    tokens_in, tokens_out, created_at)
               SELECT new.id, ns.tier, ns.model_used, ns.purpose, ns.business_rules,
                      ns.io_summary, ns.tech_debt_signals, ns.raw_response,
                      0, 0, ?
                 FROM code_nodes new
                 JOIN code_nodes prev
                   ON prev.qualified_name = new.qualified_name
                  AND prev.file_path = new.file_path
                  AND prev.file_hash = new.file_hash
                  AND prev.file_hash != ''
                  AND prev.scan_id = ?
                 JOIN node_summaries ns ON ns.node_id = prev.id
                WHERE new.scan_id = ?""",
            (now_iso(), prev_scan, scan_id),
        )
        conn.commit()
        copied = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if copied:
            logger.info(
                "Incremental re-scan: reused %d Tier-1 summaries from scan %d (unchanged files)",
                copied, prev_scan,
            )
        return copied
    finally:
        conn.close()


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
