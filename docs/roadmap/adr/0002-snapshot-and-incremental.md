# ADR-0002: Snapshot-per-scan + content-keyed incremental

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related items:** M1.4, M1.5, M1.8, M1.9, M1.11, M3.5, M3.6, M3.7, M4.1, M5.1

## Context
Today, rows are keyed by `scan_id`, and each scan is a new snapshot. Several things are wrong:
- **Reuse:** only Tier-1 summaries are reused across commits, and their key `(qualified_name, file_path, file_hash)` lacks model/prompt version (`src/ai_discovery/ai/summarizer.py:566-572`). Tier-2 flows and Tier-3 rollups are always recomputed.
- **Uncommitted changes are ignored.** A same-SHA rescan says "already scanned" (`src/ai_discovery/pipeline.py:401-436`).
- **Resume matches the raw path string**, so `./repo` and the absolute path are treated as different repos (`pipeline.py:273-283`).
- **There is no run lock**, so two scans can interleave.

repowise does fine-grained incremental patching of its graph. Its changelog shows that is a large, ongoing source of staleness bugs: 58 incremental-parity fixes ("five places the incremental path knew less than a full index").

## Decision
1. **Keep snapshot-per-scan, and add a `current_scan` pointer** (single-row table) that is flipped atomically at the end of a successful scan. Readers (`serve`, MCP, `ask`) always read `current_scan`, so a running scan never disturbs them. GC keeps the last N=3 snapshots (configurable).
2. **Incremental = caching, not patching:**
   - **Parse:** per-file cache `file_parse_cache(path, content_hash, parser_version, encoding) → serialized nodes/raw calls` (encoding = the M0.4 decode decision, so a changed fallback re-parses). Unchanged files load from the cache, and working-tree content counts.
   - **Resolve:** the whole graph is re-resolved every scan, in memory. This is cheap compared with parsing, and it guarantees that full and incremental runs match.
   - **Pages:** every structural page is rebuilt deterministically every scan. Unchanged pages are byte-identical (idempotence test, M3.5).
   - **LLM prose:** reused from `llm_cache` keyed by `sha256(page_facts_json, prompt_id, prompt_version, model_id, role)`. A failed call never writes the cache.
3. **Fingerprint stamp:** every artifact is stamped with `(extractor_version, config_hash, commit_sha, dirty_flag)`. A config change invalidates via `config_hash`, even when the commit is unchanged.
4. **Single-flight lock:** `.discovery/lock` is created with O_EXCL and holds `{pid, hostname, started_at, token}`.
   - A dead PID on the same host is reclaimed.
   - A live owner is never evicted. It is reported as suspect after 30 min.
   - The second writer exits 4 with `lock_held`.
5. **Deletions:** since pages are rebuilt from the current snapshot, a deleted file simply has no page. Page ids that disappeared get a tombstone row so backlinks and the web UI can show "removed in <commit>".

## Consequences
- **Parity test:** full == incremental is cheap to test (M3.6), because the only thing cached is parse output and LLM output.
- **Cost:** scans always pay re-resolution and page rendering. Expected to be seconds to minutes (Inference) and measured in M0.12.
- **Disk:** DB growth is bounded by snapshot GC.
- **Concurrent readers:** WAL mode (already on, `src/ai_discovery/db.py:332-395`) allows them during a scan.
