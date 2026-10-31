---
name: pipeline-analyze
description: Find where an ai-discovery scan spends time and money and recommend measured optimizations — profile a scan, rank stages, break LLM cost down by role and domain, and compare before/after. Use when a scan is slow or expensive.
---

# pipeline-analyze

Follow `docs/guides/pipeline/profiling.md` and `cost-tracking.md`. Measure before optimizing.

1. **Profile a scan.** `discover scan --progress json` emits per-stage NDJSON events with timings; `discover status --json` shows per-scan spend. Keep the raw output in the scratchpad, not in chat.
2. **Rank stages** by wall time; the stage runner (`src/ai_discovery/stages/runner.py`) skips stages whose fingerprint did not change — check whether a slow stage was re-run needlessly.
3. **LLM cost.** `llm_costs` in `.discovery/discovery.db` (read-only) by role (`fast|standard|deep`) and stage; check the response cache (`<state>/cache/llm_cache.db`) hit rate and whether prompt caching applied.
4. **Recommend** with trade-offs (quality vs cost), one change at a time.
5. **Re-measure** on the same commit and report the delta; do not claim a saving that was not measured.

References: `docs/guides/pipeline/phase-breakdown.md`, roadmap items M1.11 (cost) and M2.4 (cost gate).
