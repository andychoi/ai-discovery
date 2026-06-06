# Understand-Anything vs AI-Discovery — Comparative Assessment (Fix / Adopt / Complete / Drop)

**Date:** 2026-06-06
**Subject:** `~/ai/Understand-Anything` (TypeScript Claude Code plugin, knowledge-graph builder) assessed against `~/ai/ai-discovery` (this repo), with **ai-discovery as the primary project**.
**Method:** Two parallel deep-exploration passes (one per repo), followed by direct re-verification of every high-impact claim against current `main` (commit `b17f367`). Claims verified in source are cited `file:line`; anything not directly re-verified is marked *(from exploration — verify before implementing)*.
**Relationship to prior audits:** Supersedes the defect snapshot in `06-architecture-production-readiness-audit.md` where noted — the 2026-05-31 remediation wave (~18 commits, see `04-reverse-spec-solution-review.md` §0) closed most of those findings. This document records the *current* deltas only.

---

## 1. Executive Summary

The two projects attack the same problem — "make an unfamiliar codebase legible" — from opposite ends:

- **ai-discovery** is **deterministic-first**: tree-sitter AST → 7-stage graded call resolution → SQLite → tiered LLM (Haiku/Sonnet/Opus) → SDLC documents (ASIS/ASD/ASSC/PF/BPMN/DMN/EARS/IMPACT), with a human-labeled corpus accuracy harness as ground truth.
- **Understand-Anything (U-A)** is **agent-first**: deterministic scripts only for scanning/import-maps/batching, then parallel LLM agents produce a single `knowledge-graph.json` (21 node types / 35 edge types) rendered in an interactive React dashboard with pedagogical tours.

**Verdict:** ai-discovery should keep its analysis engine — confidence scoring, corpus harness, verified-fact tables, and tiered model routing are all *stronger* than U-A's equivalents — and adopt U-A's **orchestration and UX ideas**: Louvain semantic batching, fingerprint-based incremental re-analysis, cross-batch neighbor symbols, a committed portable graph artifact, and selective dashboard interactions. Nothing in U-A's analysis core should replace ai-discovery's; nothing in ai-discovery's recently-hardened pipeline needs to be dropped wholesale.

---

## 2. Side-by-Side Architecture

| Dimension | ai-discovery | Understand-Anything |
|---|---|---|
| **Implementation** | Python, ~26.7K LOC, 90 modules, CLI (`discover`) | TypeScript monorepo, Claude Code plugin (+ Cursor/Copilot/Codex/Gemini ports) |
| **Pipeline shape** | 15+ numbered phases, checkpointed, resumable (`pipeline.py`) | 7 phases orchestrated by an 853-line skill prompt + 6 agents |
| **Parsing** | Tree-sitter deep parsers: Python, Java, C#, JS/TS, JSP, WebForms — full AST, framework hints, endpoints, entities | Tree-sitter *import/structure extraction only*, 12 languages + 40 config formats — shallow but broad |
| **Call/edge resolution** | 7-stage graded confidence 0.5–1.0 (exact → import-scoped → receiver-type DI → contextual → suffix), `_MAX_FANOUT = 8` collapse (`graph/call_graph.py:15`) | LLM-inferred edges, 35 semantic types, weight 0–1 but no resolution-stage provenance, no ground truth |
| **Storage** | SQLite (WAL, FK-enforced, UNIQUE-constrained: `db.py:56,71,131`) + canonical JSON backbone (FSMs, cross-entity transitions) | Single committed `knowledge-graph.json` (2–5 MB typical) |
| **LLM strategy** | Explicit 3-tier routing (Haiku/Sonnet/Opus) across Bedrock/Ollama/MLX, per-phase cost ledger, in-phase budget stop (`rollup.py:707`, P1-e) | Platform-default model, no tier control, no cost ledger |
| **Grounding/verification** | RAG-grounded Tier-1/2/3 (`flow_analyzer.py:312` `_retrieve_flow_context`), Phase-17 self-review with evidence gating, blended doc confidence, human-labeled corpus harness (11 fixtures, 6 dimensions, `tests/corpus/`) | Deterministic schema + referential-integrity validation (`packages/core/src/schema.ts`); optional LLM review (`--review`); **no accuracy ground truth** |
| **Outputs** | SDLC markdown suite + BPMN/DMN/EARS XML + screen specs + impact analysis | Interactive dashboard (React Flow), pedagogical tours, onboarding guides, diff overlay |
| **Incremental updates** | Phase checkpoints + content-hash resume for embeddings and rollups | File-fingerprint change detection across the whole graph + `--auto-update` hook |
| **Cross-repo** | Federation (FSM merge by name + Jaccard), cross-repo integration correlator | None (single-repo graphs) |
| **Failure posture** | Fails loudly on total rollup failure (`RollupTotalFailureError`, `rollup.py:723`); partial failures logged + tolerated | No LLM-failure fallback strategy; malformed agent output relies on merge-script recovery |
| **Tests** | 840+ tests incl. accuracy-gated corpus baseline | 43 core unit tests + 4 large skill integration tests (e.g. 51K-line merge harness) — script-layer discipline, no accuracy layer |

**Philosophical takeaway:** U-A validates *structure* (is the graph well-formed?); ai-discovery validates *truth* (does the output match the source?). For a brownfield reverse-engineering product, truth-validation is the harder, more valuable asset — do not trade it away.

---

## 3. FIX — still-open items in ai-discovery (current state, post-2026-05-31 remediation)

> Most defects listed in audit 06 are **closed** on current `main` and are intentionally absent here. Verified-closed examples: silent Tier-3 total failure (now `RollupTotalFailureError`, `rollup.py:715-730`), Tier-2 grounding gap (RAG retrieval at `flow_analyzer.py:312,480-485`), advisor cost-gate bypass (gate enforced in `llm_client.py:263-279`), `call_edges` duplicate INSERT (`db.py:71` UNIQUE), screen-mapper silent excepts (one bare `pass` remains at `screen_mapper.py:575`).

| # | Item | Status / Evidence | U-A-informed angle |
|---|---|---|---|
| F-1 | **CRIT-3 (partial): screen-spec per-claim RAG verification.** Screen specs get Source-Files citations + capped confidence, but per-claim verification is blocked because Phase 2 (screen gen) runs before Phase 10 (RAG embed). Needs a Phase-17 verify pass over screen specs or a phase reorder. | Open — `04-reverse-spec-solution-review.md` §0 | U-A's pattern of deferring all semantic work until after deterministic context is fully assembled (Phases 1/1.5 before any agent runs) is the right ordering instinct: embed first, generate second. |
| F-2 | **LSP/compiler resolver tier.** Stage-0 symbol-index resolution exists only if an external SCIP/LSP index is supplied; no built-in indexer. The one big architectural bet not yet taken. | Open — `04-reverse-spec-solution-review.md` §0 | U-A doesn't have this either — neither project resolves polymorphism authoritatively. If built, gauge with a `*-di-collision`-style corpus fixture *first* (per the corpus-harness rule: ground truth before resolution work). |
| F-3 | **Large-scale performance ceiling.** Audit 06 flagged single-threaded call-graph construction and O(N) suffix scans as a P0 at 500k-method scale. `_MAX_FANOUT = 8` collapse exists (`graph/call_graph.py:15`) and `source_code` is no longer carried on the call-graph path, but end-to-end scale behavior is unbenchmarked. *(partially from exploration — benchmark before optimizing, per Principle 5)* | Open (unbenchmarked) | U-A caps practical scale too (~3000 dashboard nodes, ELK layout untested beyond). Neither project has a scale benchmark; add one to the corpus harness before touching resolution code. |
| F-4 | **Corpus Phase 3: pinned external real repos at fixed SHA.** Harness currently runs on 11 authored fixtures only. | Open — `tests/corpus/`, `04` §0 | U-A's fixture projects (TS/Python/Go/Rust/Java samples) show the value of *small per-language* fixtures; ai-discovery's Phase 3 should pin *real* repos — a stronger standard U-A lacks entirely. |
| F-5 | **`ai/llm_client.py:1` misleading docstring** — claimed the client wraps `shared.llm_router`; it actually imports from `shared/llm_invoke.py` (`llm_client.py:27`). | **Fixed 2026-06-06** (docstring now says `shared.llm_invoke`); see D-1 for the router decision. | — |

---

## 4. ADOPT — proven U-A mechanisms worth porting

Ordered by leverage. Every item names the U-A source and the ai-discovery integration point.

| # | Adopt | U-A source | ai-discovery integration point | Why |
|---|---|---|---|---|
| A-1 | **Louvain semantic batching.** Cluster files by import-graph community before LLM work; fall back to deterministic chunking when clustering fails; merge singletons/orphans (U-A found 87 singletons on a 314-file run and handles them explicitly). | `understand-anything-plugin/skills/understand/compute-batches.mjs` (graphology + `graphology-communities-louvain`, lines 5, 33, 197-237 — verified) | Phase 9 (chunk) / Phase 11 (Tier-1 summarize): group chunks by call-graph + import community so each Tier-1 batch shares context; improves RAG locality and summary coherence vs. per-chunk fan-out. ai-discovery already *has* the import + call graph — the clustering step is cheap to add (Python: `python-louvain`/`networkx`). | Best single idea in U-A. Semantic locality per batch is strictly better than count-based batching, and the fallback/singleton handling is already designed. |
| A-2 | **Fingerprint-based incremental re-scan, generalized.** U-A fingerprints every file and re-analyzes only changes; ai-discovery has content-hash resume for embeddings/rollups and SHA256 drift detection for screen specs (`screen_mapper.py:597-637` *(from exploration)*) — but a full re-scan still re-parses and re-summarizes everything. | `packages/core/src/__tests__/fingerprint.test.ts`, staleness machinery *(from exploration)* | Phases 6/11/14: store per-file content hash at parse time; on re-scan, skip parse/summarize/rollup for unchanged files and only re-blend affected domains. The screen-spec drift machinery is the in-repo prototype to generalize. | Turns the "regen only what changed" cost-saving promise (already in CLAUDE.md for screens) into a pipeline-wide property. Biggest cost lever after tiering. |
| A-3 | **Cross-batch neighbor symbols.** Each U-A batch receives the exported symbols of neighboring batches, so inter-batch edges resolve without re-analysis. | `compute-batches.mjs` neighbor-map output + `file-analyzer` agent contract *(from exploration)* | Stage-5 contextual short-name resolution (`graph/call_graph.py`): when resolving within a domain/batch, supply the neighbor batches' exported symbol table as candidate set — narrows fan-out before the `_MAX_FANOUT` collapse triggers. | Directly attacks the noisiest resolution stage with information ai-discovery already computes but doesn't route. Gauge with a corpus fixture first (harness rule). |
| A-4 | **Committed, portable graph artifact.** U-A commits `knowledge-graph.json`; teammates load it and skip the entire pipeline. ai-discovery's FSM backbone JSONs are portable, but the doc/graph layer lives only in `discovery-{slug}.db`. | `.understand-anything/knowledge-graph.json` convention + `packages/core/src/types.ts` schema | New `discover export-graph -p SLUG` producing one canonical JSON (nodes, edges incl. confidence + resolution stage, domains, FSMs, doc index). Feeds the viewer, federation, and any future dashboard without shipping SQLite. | Cheap; makes outputs shareable/diffable in PRs the way U-A's are. Keep SQLite as the working store — the export is a *view*, not a migration. |
| A-5 | **Viewer interactions (selective).** `discover view` is metrics-only (confidence histograms, artifact checklist). U-A's node-click → code viewer, layer color-coding, fuzzy + semantic search, and persona-aware detail levels are the high-value interactions. | `packages/dashboard/src/App.tsx`, `GraphView.tsx` *(from exploration)* | `viewer/dashboard.py` + `viewer/server.py`: add per-node drill-down (node → source snippet + summary + confidence provenance) and search. **Do not** port the React/ELK stack — keep the Flask viewer, borrow the interaction design. | The metrics viewer answers "is the scan good?"; U-A's interactions answer "what does this code do?" — the question users actually have. |
| A-6 | **Import-map extraction as a language on-ramp.** U-A resolves imports for 12 languages with small per-language extractors — far cheaper than a full parser. | `skills/understand/extract-import-map.mjs` (per-language rules) *(from exploration)* | Pre-stage for `/parser-extension`: for Go/Rust/Ruby/PHP, ship import-map extraction first to power Stage-4 import-scoped resolution and domain hints *before* a deep tree-sitter parser exists. Register as a lightweight parser tier in `lang_detector.py`. | Converts "unsupported language" from a cliff into a gradient. Fits the templated extension checklist. |
| A-7 | **Tour/onboarding generation.** Single LLM call over the existing graph: rank entry points by fan-in, BFS-order a 5–15 step learning path. | `agents/tour-builder.md` + tour topology script; `onboard-builder.ts` *(from exploration)* | New cheap Tier-2 artifact (e.g. `ONBOARD/{domain}.md`) generated from call graph + domain entry points already in `domains.entry_points`. | High doc value per token; ai-discovery has *better* inputs for it (real call graph + confidence) than U-A does. |

**Explicit non-adoption rationale for the rest of U-A:** see §6.

---

## 5. COMPLETE — scaffolded ai-discovery features to finish (retain, per project direction)

| # | Item | Current state (verified) | Plan |
|---|---|---|---|
| C-1 | **SPA menu detection: TypeScript constants + Vue/React/Angular routers.** | Still stubbed: `menu_detector.py` framework detectors return `None` (12 sites, e.g. lines 224-311), `_parse_ts_array` returns `[]`. **Note:** JSP and WebForms fallback menu detectors are now DONE (`JspMenuDetector` commit `193fba2`, `WebFormsMenuDetector` commit `47cbe00`) — the remaining gap is SPA frameworks only. | Implement TS-const parsing and router-table extraction (Vue Router / React Router / Angular routes). U-A's TS/JS import-map extractor (`extract-import-map.mjs`) and framework registry are working reference implementations for locating and walking route definitions. Until done, emit an explicit "menu format unsupported — skipped" signal instead of silently yielding 0 screens (CLAUDE.md already documents the gap; the *runtime* should too). |
| C-2 | **JSP/WebForms parsers — recently completed; close the documentation loop.** | DONE on `main`: parsers wired into the walker (`pipeline.py:581-586`), extractors + domain co-location + menu fallback shipped (commits `1a98774`…`b17f367`). Audit 06's "files never walked" claim is **obsolete**. | Remaining follow-ups only: code-behind-link follow-up noted in `cbc6669`; the self-closing-tag regex limitation locked in `c5f5970` is a known accepted bound. Update any docs still describing JSP/WebForms as stubs. |
| C-3 | **Screen-spec verification pass** (same as F-1) — listed here because the completion path is a *feature* (Phase-17 screen-claims verify or phase reorder), not a bug fix. | Partial (CRIT-3) | Prefer the Phase-17 verify pass over reordering: keeps Phase 2's "cheap early screens" property and reuses the existing self-review claim machinery. |

---

## 6. DROP / DO-NOT-ADOPT

### From ai-discovery

| # | Item | Verified finding | Recommendation |
|---|---|---|---|
| D-1 | **`shared/llm_router.py`** (22.2K) — **DECIDED 2026-06-06: retain** | Imported by **no** module in this repo; actual LLM calls route through `shared/llm_invoke.py` (`ai/llm_client.py:27` imports `invoke_bedrock`/`invoke_ollama`). `llm_router.py` itself imports `ai_discovery.shared.sdlc_db`, which **does not exist** in `shared/` — it crashes on import. Despite the name, it routes only bedrock + ollama-like providers (no OpenAI/Gemini/Anthropic-direct). | **Decision: retained** as the basis for planned multi-provider support (OpenAI, Gemini, Anthropic-direct, Bedrock). Before it can be wired in it must be refactored off DocHub's `sdlc_db` DB-config onto `DiscoveryConfig`, and the new providers implemented (its `_ollama_like_keys` key-tuple pattern generalizes well to OpenAI-compatible endpoints — OpenAI and Gemini both expose them). The `llm_client.py:1` docstring fix (F-5) is applied. |

### From Understand-Anything — patterns NOT to adopt

| # | Pattern | Why not |
|---|---|---|
| N-1 | **Agent-orchestrated analysis core** (LLM agents produce nodes/edges directly) | No resolution-stage provenance, no graded confidence, no ground truth. Adopting it would regress against the corpus harness — ai-discovery's central asset. LLM belongs *on top of* deterministic structure (which is also U-A's own stated thesis; ai-discovery just draws the line further toward determinism). |
| N-2 | **Platform-default model selection** (no model pinning, no cost knobs) | ai-discovery's explicit tier routing + per-phase cost ledger + in-phase budget stop is strictly superior for a tool that bills real Bedrock dollars. |
| N-3 | **Single-JSON-file primary storage** | Fine at U-A's ~3K-node scale; wrong for ai-discovery's 500k-method ambitions. SQLite stays primary; JSON is an export (A-4). |
| N-4 | **35-edge-type taxonomy wholesale** | Many U-A edge types (`similar_to`, `related`, knowledge-graph types) have no deterministic evidence source in ai-discovery. Adopt an edge type only when a parser/extractor can emit it with provenance — candidates: `configures`, `deploys`, `tested_by` (the infra/OpenAPI extractors in `src/ai_discovery/extractors/` could ground these). |
| N-5 | **853-line skill-prompt orchestration** | ai-discovery's checkpointed Python pipeline is more testable, resumable, and budget-controllable than prompt-encoded control flow. |

---

## 7. Prioritized Action Table

| Pri | Action | Type | Effort | Where |
|---|---|---|---|---|
| 1 | Screen-spec per-claim verification (Phase-17 verify pass) | Fix/Complete (F-1/C-3) | M | `ai/self_review.py`, `pipeline.py` Phase 17 |
| 2 | Louvain semantic batching for Tier-1 | Adopt (A-1) | M | new batching step feeding Phase 11; ref `compute-batches.mjs` |
| 3 | Pipeline-wide fingerprint incremental re-scan | Adopt (A-2) | M–L | Phases 6/11/14; generalize screen-drift SHA256 machinery |
| 4 | Cross-batch neighbor symbols → Stage-5 candidate narrowing | Adopt (A-3) | M | `graph/call_graph.py`; **add corpus fixture first** |
| 5 | SPA menu detectors (TS const, Vue/React/Angular) + explicit skip signal | Complete (C-1) | M | `menu_detector.py` |
| 6 | `discover export-graph` canonical JSON | Adopt (A-4) | S | new CLI command; reuse FSM backbone export patterns |
| 7 | Multi-provider router: refactor `llm_router.py` off `sdlc_db` onto `DiscoveryConfig`; add OpenAI/Gemini/Anthropic-direct invoke fns | Complete (D-1 decision) | M | `shared/llm_router.py`, `shared/llm_invoke.py`, `config.py` |
| 8 | Viewer drill-down + search | Adopt (A-5) | M | `viewer/dashboard.py`, `viewer/server.py` |
| 9 | Tour/onboarding artifact | Adopt (A-7) | S–M | new generator in `generators/` |
| 10 | Import-map tier for Go/Rust/Ruby/PHP | Adopt (A-6) | M | `parsers/`, `lang_detector.py`; follow extension checklist |
| 11 | Scale benchmark in corpus harness, then perf work if warranted | Fix (F-3) | M | `tests/corpus/` |
| 12 | Corpus Phase 3 (pinned external repos) | Fix (F-4) | M | `tests/corpus/` |
| 13 | LSP/compiler resolver tier (gauge with DI-collision fixture first) | Fix (F-2) | L | `graph/call_graph.py` Stage 0 |

---

## 8. Scorecard — what each project does better

**Understand-Anything does better:**
- Semantic batching (Louvain) and cross-batch context routing — genuinely clever orchestration.
- Incremental updates as a first-class, whole-graph property.
- Interactive exploration UX (dashboard, tours, persona-aware detail) — the *consumption* side.
- Breadth: 12 languages + 40 config formats reachable cheaply via shallow extraction.
- Script-layer test discipline (e.g. the 51K-line `test_merge_batch_graphs.py` harness).
- Multi-platform distribution and team-share-by-commit ergonomics.

**ai-discovery does better:**
- Truth validation: human-labeled corpus accuracy harness with regression-locked baselines — U-A has nothing comparable.
- Call resolution with graded, provenance-carrying confidence (7 stages incl. receiver-type DI).
- Verified-fact tables (endpoints/entities rendered from AST, not LLM prose).
- Cost engineering: explicit tiers, per-phase ledger, in-phase budget stop, loud total-failure errors.
- Depth of output: SDLC document suite, BPMN/DMN/EARS, FSM backbone, impact analysis, federation.
- Resumability and operational posture (checkpoints, WAL SQLite, UNIQUE constraints).

**Shared blind spots (neither project solves):**
- Authoritative polymorphism resolution (no LSP/compiler tier on either side).
- Benchmarked behavior at large scale (500k methods / 3000+ dashboard nodes).
- Message-broker topic↔consumer correlation.

---

*Prepared as assessment 07 in the `docs/assessments/` series. Implementation of any item above should follow the standard workflow: design → corpus fixture (where resolution accuracy is touched) → plan → implement → verify.*
