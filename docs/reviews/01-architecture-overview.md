# Architecture Overview & End-to-End Workflow

**Date**: 2026-07-12 · Reviewed at commit `d9d997b`

This document reverse-engineers the system as it actually is (from code, not from the docs tree), covering the product vision, the layered architecture, the end-to-end pipeline, and how state, configuration, and outputs flow.

---

## 1. Product vision and current capabilities

**Vision**: turn an undocumented brownfield codebase into a navigable, verified SDLC documentation set — organized both by business domain (ASIS/ASD/ASSC) and, since v0.3, by *user-facing screens* as the entry point — cheaply enough to re-run on every change (drift detection, incremental re-scan) and honestly enough to trust (graded confidence, self-review, deterministic fact-checking).

**Current capabilities** (all functional):

- Parse 6 languages with full tree-sitter AST (Python, Java, C#, JS/TS, + JSP/WebForms markup) and 4 more (Go, Rust, Ruby, PHP) via a lightweight regex "import-map" tier.
- Build a resolved call graph with 6-stage graded confidence (0.5–1.0) including DI/receiver-type resolution and community narrowing.
- Infer domains, execution scenarios, entity state machines (FSMs), cross-entity transition/condition mining, and DB relationships (declared FKs + SQL DDL + denormalization links).
- Detect screens from 5 menu-system formats and map them to backend/tables/jobs/interfaces with SHA256 drift hashes.
- Generate: ASIS/ASD/ASSC domain rollups (Tier-3), process-flow docs, BPMN 2.0 XML + Mermaid, DMN decision tables, EARS requirements, IMPACT dependency catalogs, ONBOARD tour guides, screen specs (with MANUAL-block edit preservation), and a canonical `knowledge-graph.json` export.
- Verify: RAG-backed claim verification with abstention, deterministic prose validation, `--prod` per-scenario/per-screen verification, confidence blending.
- Consume: local viewer (stdlib HTTP + client-rendered Mermaid/BPMN), RAG chat REPL, impact queries, SQL console, drift CLI for CI.
- Operate: 7 LLM providers (Bedrock, Anthropic direct, OpenAI, Gemini, Ollama, 2× MLX), 3–4 model tiers, budget guard, resumable checkpointed pipeline, incremental Tier-1 reuse across scans.

## 2. Layered architecture

```
┌────────────────────────── CLI (cli.py, 967 ln) ──────────────────────────┐
│ scan · detect-screens · verify-drift · view · chat · impact · query ·    │
│ export-graph · ingest-docs · init                                        │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌────────────────── Orchestrator (pipeline.py, 2,071 ln) ──────────────────┐
│ run_pipeline(): phases 2, 5–19 · resume/rescan arbitration · checkpoints │
│ (currently a single function — see doc 04, smell #1)                     │
└───┬───────────────┬───────────────┬────────────────┬─────────────────────┘
    │               │               │                │
┌───▼────────┐ ┌────▼─────────┐ ┌───▼────────────┐ ┌─▼──────────────────┐
│ Extraction │ │ Graph        │ │ AI             │ │ Output             │
│ parsers/   │ │ graph/       │ │ ai/            │ │ generators/        │
│ extractors/│ │ call_graph   │ │ chunker        │ │ doc_generator (J2) │
│ repo/      │ │ symbol_index │ │ summarizer T1  │ │ bpmn/dmn/ears      │
│ menu_      │ │ domain/      │ │ flow_analyzer  │ │ screen_doc_writer  │
│ detector   │ │ entity_*     │ │ T2             │ │ dependency_catalog │
│ screen_    │ │ fsm_*        │ │ rollup T3      │ │ onboarding, export │
│ mapper     │ │ impact,      │ │ self_review    │ │ push (api/gitea/   │
│ route_     │ │ federation   │ │ screen_spec_   │ │ offline)           │
│ parser     │ │              │ │ gen, advisor   │ │                    │
└───┬────────┘ └────┬─────────┘ └───┬────────────┘ └─┬──────────────────┘
    │               │               │                │
┌───▼───────────────▼───────────────▼────────────────▼──────────────────┐
│ Shared foundation                                                      │
│ · db.py — SQLite, WAL, schema v14, phase_checkpoints, llm_costs        │
│ · shared/llm_router + llm_invoke + model_defaults — 7-provider router  │
│ · rag/ — sqlite-vec embeddings (code + docs), retriever, chat          │
│ · config.py — discovery.yaml > env > model_defaults                    │
└────────────────────────────────────────────────────────────────────────┘
```

**Key architectural properties:**

- **SQLite is the single source of truth** (`discovery.db`, schema v14, WAL). Markdown output, the viewer, graph export, and RAG are all *views* over it. This is a deliberate, documented decision and a good one at this scale.
- **The LLM layer is a 3-level stack**: `LLMClient` (tiers, cost, structured output, advisor) → `llm_router` (config resolution, dispatch, usage hook) → `llm_invoke` (raw transports). Structured outputs use forced tool-choice where the provider supports it, with one shared tolerant-JSON fallback.
- **Confidence is a first-class column** on call edges, docs, screens, FSMs — the whole downstream (viewer quality gates, triage skill, BPMN inclusion) keys off it.
- **Two doc organizations coexist by design**: domain-centric (ASIS/ASD/ASSC/PF/BPMN/DMN/EARS) and screen-centric (`docs/screens/` linking out) — screens are additive entry points, not a parallel truth.

## 3. End-to-end workflow (the 18-phase pipeline)

Phases are contiguous integers 2, 5–19 registered in `_PHASE_SPECS` (`pipeline.py:54-71`); 1/3/4 are conceptual sub-steps folded into 2 and 8. Every phase writes `phase_checkpoints` rows (start/complete/error) enabling `--resume`.

| Phase | Name | Tier | What happens |
|---|---|---|---|
| — | init/resolve | — | Clone/checkout repo, resume/rescan arbitration (exact `commit_sha` match → cached re-render fast path), create `scan_runs` row |
| 2 | screen_llm_specs | T2 | `detect_and_build_screens` (5-format hybrid menu detection, first match wins) → `map_all_screens` (screen→API/controller/table/job/interface + SHA256 drift hashes) → LLM screen specs (threaded) |
| 5 | lang_detect | — | Extension + manifest based language census |
| 6 | parse | — | Threaded parse: 4 tree-sitter parsers + JSP/WebForms + import-map tier; merge OpenAPI/GraphQL/proto/infra extractor nodes; SHA256 `file_hash` stamped per node |
| 7 | domain_classify | — | `classify_domains`, persist `code_nodes`, **`build_call_graph`** (6-stage resolution), external-system extraction, persist edges, `interfaces.json` |
| 8 (+8a/8b) | execution_slices | — | Scenario slicing (BFS from entry points, primary-path DFS); 8a entry-point linking; 8b (inline, *uncheckpointed*): FSM rollup → identity consolidation → denorm links → entity classification → cross-entity mining → guard parsing → BPMN/DMN/EARS entity artifacts |
| 9 | chunk | — | AST-aware chunking (class→methods); RAG chunk build; **source text freed** afterwards |
| 10 | rag_embed | embed | Incremental content-hash embedding into sqlite-vec (threaded, batch endpoints where supported) |
| 11 | tier1_summarize | T1 | Cross-scan summary reuse by `file_hash`, then Louvain **semantic batching** → per-community batched summarization (threaded) |
| 12 | tier2_flow_analysis | T2 | Per-domain business-flow identification |
| 13 | scenario_flow_inference | T2 | Per-scenario structured flow inference (steps/IPO/interfaces); `--prod` adds verification. **Serial LLM loop** |
| 14 | tier3_doc_rollup | T3 | Per-domain ASIS/ASD/ASSC generation (3 doc types × domains, threaded); prose-validator annotation; `RollupTotalFailureError` guard on total failure |
| 15 | visual_artifacts | T2 | BPMN/Mermaid/IPO per scenario; ONBOARD tour narratives |
| 16 | process_mining | — | Optional pure-Python edge-frequency statistics (pm4py removed 2026-06-06 — honest reversal) → scan marked `llm_complete` |
| 17 | self_review | T1/T2 | Claim extraction → RAG verify (abstain on weak evidence) → section regeneration → re-verify → confidence blend; screen verification at `--prod` parity |
| 18 | render_markdown | — | Jinja2 rendering into DocHub-style tree (ASIS/, ASD/, PF/, BPMN/, …), dependency catalog, doc_id sync |
| 19 | finalise | — | `scan_runs.status = completed`, cost summary |

**Post-scan consumption**: `discover view` (dashboard: quality targets, confidence histogram, weakest docs, node drill-down), `discover chat` (dual code+doc RAG), `discover impact <Entity>`, `discover verify-drift` (CI gate on screen `source_hashes`), `discover export-graph`, `/discover-triage` and `/discover-consistency` skills for post-hoc verification.

## 4. State, resume, and incrementality

- **Checkpointing**: `_with_checkpoint` context manager per phase; `--resume` restarts after `MAX(complete phase)`; phases 6/7/11/12 reload persisted state, 8/13 rebuild in-memory (cheap), **8b re-runs in full on every resume** (uncheckpointed — a known cost).
- **Fast paths**: `llm_complete`/`push_failed` status → re-render docs from DB with zero LLM calls; same-SHA `--rescan` with cached docs → re-render.
- **Incremental**: `code_nodes.file_hash` (SHA256) lets `reuse_prior_summaries` copy Tier-1 summaries for unchanged files across scans; the RAG embedder is content-addressed (re-embeds only changed chunks, deletes stale rows); screens carry per-source-file hashes for drift detection.

## 5. Configuration model

`discovery.yaml` → `DiscoveryConfig.load` (unknown keys silently filtered) → registered globally into `llm_router` via `configure()`. Resolution priority: runtime `save_config()` > YAML > env vars (`LLM_*_MODEL`, `OLLAMA_URL`, …) > `model_defaults.MODELS`. Tier semantics: `tier1/tier2/tier3d/tier3p` per provider; `--prod` flips tier3 → tier3p *and* enables verification passes. Budget: `budget_limit_usd` halts LLM phases mid-run (status `budget_exceeded`).

## 6. Where reality diverges from the documented architecture

The docs tree is unusually good, but the review found specific drift (details in docs 02/04):

- `docs/guides/parsers/extension-checklist.md` documents registration points (`LANGUAGE_PARSERS`, `LANGUAGE_FILE_EXTENSIONS`) and a parser API (`parse()`, `get_calls()`, `CallReference`) that **do not exist**; real wiring is 3 files (`lang_detector._EXT_MAP`, `file_walker._LANG_EXTENSIONS`, the hardcoded list at `pipeline.py:593`).
- `docs/guides/call-graph/resolution-heuristics.md` describes a "7-level" resolver and normalized 0–1 node scoring; the code has 6 stages and raw unnormalized scores; `decisions.md` says "4-stage" — three different stage counts in circulation.
- `domain_classifier` implements neither the documented LCA extraction nor the ≥3-node domain threshold.
- "Community narrowing" is connected components (union-find) over the ≥0.93-confidence subgraph — *not* Louvain (Louvain is used in Tier-1 semantic batching only).
- `overview.md`'s PF filename example predates the Track-3 domain-grouped naming.
