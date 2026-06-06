# Pipeline Phases: Detailed Breakdown

Reference guide for all AI-Discovery pipeline phases. The canonical phase list lives in `_PHASE_SPECS` (`src/ai_discovery/pipeline.py`): a screen-centric checkpoint phase (2) plus the domain-centric checkpoint phases (5–19). A few non-checkpointed setup steps run before phase 2 (DB init, repo resolution, resume/rescan logic, scan-run creation). All phase numbers are integers — there are no decimal sub-phases. Optional / skippable stages (`execution_slices`, `process_mining`) get their own integer slot.

---

## Pre-Pipeline Setup Steps (Not Checkpointed)

These run before phase 2 and are not tracked as checkpoint phases (no entry in `_PHASE_SPECS`).

### Database Initialization
**Input**: `db_path`  
**Output**: SQLite schema ready  
**Time**: < 1s

Initializes SQLite database with schema (code_nodes, call_edges, domains, etc.). Enables WAL mode for concurrent writes. Checks schema version for migrations.

### Repository Resolution
**Input**: Repo URL or local path, branch  
**Output**: `ResolvedRepo(commit_sha, branch, local_path)`  
**Time**: 1–30s (depending on clone size)

If URL: clones repository to local cache (skip if cached).  
If local path: fingerprints it (checks commit SHA, branch).  
Fallback: if target branch unavailable, tries main/master.

### Resume/Rescan Logic
**Input**: Previous scan run (if resuming)  
**Output**: Decision to skip, resume, or rescan  
**Time**: < 1s

If `--resume` flag: loads previous scan state, resumes at the phase after the last completed checkpoint.  
If `--rescan` flag: deletes previous scan, starts fresh.

### Create Scan Run
**Input**: Scan metadata (repo, branch, config)  
**Output**: `scan_id` (unique identifier for this scan)  
**Time**: < 1s

Creates entry in `scan_runs` table. Records start time, status, config. Later updated with completion time and cost.

---

## Checkpoint Phases (2, 5–19)

### Phase 2: Screen-Centric LLM Spec Generation
**Input**: Resolved repo path  
**Output**: Screen specs (`screen_specs` rows) + rendered screen markdown  
**Time**: 1–15 min (LLM, only when a menu system is detected)

Runs **before phase 5**, parallel to the domain-centric track (phases 5–19). A checkpointed phase (`screen_llm_specs` in `_PHASE_SPECS`):

1. **Detect screens** — `detect_and_build_screens(repo_path)` scans for a menu system (JSON/YAML files, TS/JS constants, framework routing, WebForms, JSP). If none matches, the phase logs the formats tried and skips screen generation (no silent 0-screen run).
2. **Map screens to backend** — `map_all_screens` links each screen to its frontend APIs, controllers/services, tables, batch jobs, and external interfaces.
3. **Generate specs (LLM)** — one combined spec per screen; budget-gated (`_budget_ok`).
4. **Persist + render** — `persist_screen_specs` then `write_all_screen_specs`.

Screen specs are NEW user-facing entry points that link OUT to the domain-centric docs; they do not replace them.

---

## Domain-Centric Checkpoint Phases (5–19)

### Phase 5: Language Detection
**Input**: Repository files  
**Output**: `lang_stats { python: 150, java: 50, javascript: 20, ... }`  
**Time**: 1–5s

Scans file extensions and manifest files (package.json, pom.xml, requirements.txt) to detect languages.

---

## Phases 6–7: Code Parsing & Domain Classification

### Phase 6: Parse Files
**Input**: Source files per language  
**Output**: `CodeNode[]` (parsed AST nodes)  
**Time**: 5–60s (depending on codebase size)

Tree-sitter AST parsing per language. Extracts:
- Classes, functions, methods
- Call references
- Data models
- Framework hints
- Entry points

Parallelized per language. Failures on individual files are logged; parsing continues.

### Phase 7: Classify Domains
**Input**: `CodeNode[]`  
**Output**: `Domain[]` (business domain groupings)  
**Time**: 1–5s

Groups code nodes into business domains using namespace/path heuristics:
- `orders.*` → "orders" domain
- `payments.*` → "payments" domain
- etc.

Creates domain metadata (entry points, tech stack).

**Inline (not a separate phase): persist nodes + build call graph.** Within phase 7, parsed `CodeNode[]` and `Domain[]` rows are written to the `code_nodes` and `domains` tables (duplicates handled; resume checkpoints set), and the call graph is built (`graph/call_graph.py:build_call_graph`). This is an inline operation — it has no entry in `_PHASE_SPECS`.

**Call resolution is a 4-stage graded cascade** (first match wins; higher confidence earlier), plus a stage-0 symbol-index tier. Edges are deduped per `(caller, callee, edge_type)`, keeping the highest confidence. See `docs/architecture/decisions.md` for the authoritative table.

- **Stage 0 — symbol index** (LSP/SCIP/compiler, `resolved_by: index`): authoritative resolution when a `symbol_index.json` is supplied; the only tier that resolves interface/polymorphic dispatch. Confidence 1.0. Absent an index, resolution is fully heuristic.
- **Stage 1 — exact qualified-name match** (`exact`): confidence 1.0.
- **Stage 2 — import-scoped** (`import_scope`): receiver matches a caller import — 0.95 when the import module matches the candidate's file, 0.85 otherwise.
- **Stage 3 — receiver-type (DI)** (`receiver_type`): receiver is a field/ctor-param of a known type `T` and `T` defines the method — pin to `T.method` at 0.93. Interface→single-impl fallback (`interface_impl`) resolves at 0.90.
- **Stage 4 — short-name contextual** (`short_name` / `short_name_community`): graded 0.95 … 0.6 — same class 0.95, same-file unique 0.90, same-module unique / unique suffix 0.85, call-graph community narrowing (unique-in-community 0.80, multiple 0.70), best prefix overlap 0.65–0.75, fan-out 0.60. Community narrowing (A-3) is built from the stage 0–3 edges (≥ 0.93) so the noisy short-name stage never feeds its own input.
- **Stage 5 — unresolved** (`unresolved`): no candidate; one edge to the raw call name at confidence 0.5.

---

## Phases 8–10: Execution Slicing & Embedding

### Phase 8: Build Execution Slices
**Input**: `CodeNode[]`, `CallEdge[]`, entry points  
**Output**: `Scenario[]` (execution scenarios from each entry point)  
**Time**: 5–20s

Bounded BFS traversal (depth ≤ 5) from each entry point:
- Primary path: top-15 nodes by confidence, in BFS order
- Alternate paths: conditional branches detected from nodes with >1 callee
- Confidence scoring: multi-signal (depth, state transition, data boundary, etc.)

Stores scenarios in `scenario_flows` table.

### Phase 9: Chunk
**Input**: `CodeNode[]` (all parsed nodes)  
**Output**: `CodeChunk[]`  
**Time**: 5–30s (depends on codebase size)

Method-level splits with RAG overlap:
- Each method → one chunk
- Surrounding context (class definition, imports) → prepended

Resume-aware: skips already-chunked nodes.

### Phase 10: RAG Embed
**Input**: `CodeChunk[]`  
**Output**: Vectors in sqlite-vec  
**Time**: 20–90s (depends on codebase size and embedding model)

Embeds chunks using embedding model (typically Ada or similar).  
Stores vectors in sqlite-vec for later semantic search.

Resume-aware: skips already-embedded chunks.

---

## Phases 11–14: LLM Pipeline (Tiered)

### Phase 11: Tier 1 Summarize
**Input**: `CodeChunk[]`  
**Output**: `node_summaries` (purpose, business_rules, io_summary)  
**Time**: 2–10 min (Haiku model, high concurrency)

Fast, concurrent summarization per chunk:
```
Input: validateOrder() function with context
Output: 
  purpose: "Validates order data (items, total, customer)"
  business_rules: ["Total must be > 0", "Items required"]
  io_summary: "Input: Order object; Output: boolean"
```

Uses Claude Haiku (fast) or Ollama gemma4:e2b (local).  
Stores in `node_summaries` table.

**Louvain semantic batching (A-1)**: when `semantic_batching` is enabled (default), chunks are grouped by call-graph community before the LLM pass (`ai/semantic_batching.py`). Phase-7 call edges collapse into a confidence-weighted file graph; `networkx` `louvain_communities` partitions it, and each community becomes one structured LLM call (caps: 10 chunks / 12k tokens per batch; undersized batches pooled by domain). The summarizer therefore sees a chunk's callers/callees instead of one chunk in isolation. If clustering fails it degrades loudly to deterministic domain/path grouping — Tier 1 never crashes or drops chunks.

**Fingerprint-based cross-scan reuse (A-2)**: before summarizing, `reuse_prior_summaries(db_path, scan_id)` copies Tier-1 summaries from the latest prior scan for every node whose `(qualified_name, file_path, file_hash)` is unchanged (blank hashes never match, so a blank == blank join can't reuse across real changes). The resume guard then skips those nodes, so an incremental re-scan only pays for files that actually changed.

### Phase 12: Tier 2 Flow Analysis
**Input**: `domain[]` + `node_summaries`  
**Output**: `business_flows`, `scenario_flows` (execution flows per scenario)  
**Time**: 5–30 min (Sonnet model, per-domain)

Per-domain analysis:
1. Extract business flows (user_flow, batch_flow, integration_flow, cross_cutting)
2. Analyze scenario execution (steps, IPO, alternate paths)

Uses Claude Sonnet or Ollama gemma4:26b.  
Stores in `business_flows` and `scenario_flows` tables.

### Phase 13: Scenario Flow Inference
**Input**: `Scenario[]` + `node_summaries`  
**Output**: `scenario_flows` (per-scenario inferred business flow)  
**Time**: 5–15 min

Per-scenario LLM pass that turns each execution slice into a business-readable flow with IPO and alternate paths. Uses the Tier 2 model. Skippable; if absent the visual artifacts phase falls back to the empty-list rebuild path.

### Phase 14: Tier 3 Doc Rollup
**Input**: `domain[]` + `business_flows` + `scenario_flows`  
**Output**: `generated_docs` (markdown content, BPMN, diagrams)  
**Time**: 10–60 min (Opus model, per-domain×doc_type)

Final document generation per domain×doc_type:
- as-is.md (business overview)
- as-is-detail.md (technical details)
- as-is-schema.md (data models)
- process-flow.md (BPMN + IPO)

Uses Claude Opus (Bedrock), or Ollama gemma4:26b (dev) / gemma4:31b (prod, `--prod` flag).  
Stores in `generated_docs` table.

---

## Phases 15–19: Artifact Generation, Verification & Output

### Phase 15: Visual Artifacts
**Input**: `scenario_flows`  
**Output**: BPMN 2.0 XML, Mermaid sequence + flowchart diagrams, IPO markdown per scenario  
**Time**: 1–10s

Generates diagram artifacts from the inferred flows. Persists scenario+artifact rows so render and ingest can locate them. Always runs (no `enabled` flag).

### Phase 16: Process Mining (Optional)
**Input**: `scenario_flows`  
**Output**: pm4py inductive-miner Petri nets + conformance metrics per scenario  
**Time**: 10–20 min when enabled

Runs only when `process_mining.enabled: true` in `discovery.yaml`. When disabled, no checkpoint is written — resume logic skips it cleanly. Skip via `--skip-phases=16`.

### Phase 17: Self-Review
**Input**: `generated_docs`  
**Output**: `review_claims` (verified/unverified/contradicted)  
**Time**: 5–20 min

Claim extraction + RAG verification:
1. Extract factual claims from generated docs
2. Search code chunks (RAG) to verify claims
3. Mark as verified, unverified, or contradicted

Stores findings in `review_claims` table.  
High unverified rate (>20%) triggers human review flag.

### Phase 18: Render Markdown
**Input**: `generated_docs` + artifacts (BPMN, Mermaid, IPO tables)  
**Output**: `.md` files on disk  
**Time**: 1–5s

Jinja2 template rendering:
```
data/{slug}/
├── ASIS/
│   └── myproj-orders-as-is.md
├── ASD/
│   └── myproj-orders-as-is-detail.md
├── ASSC/
│   └── myproj-orders-as-is-schema.md
├── PF/
│   └── myproj-scenario-create-order-process-flow.md
└── event-logs/
    └── scenario_create_order_1.json
```

### Phase 19: Finalize
**Input**: Scan metadata  
**Output**: Updated `scan_runs` row  
**Time**: < 1s

Calculates total cost, stores in `llm_costs` table.  
Updates scan status to `completed`.  
Records end time.

---

## Cost & Timing Summary

| Phase | Typical Duration | Cost | Bottleneck |
|-------|---|---|---|
| Pre-pipeline setup | 10–100s | ~$0 | Cloning large repos |
| 2 (Screen specs, when a menu is detected) | 1–15 min | ~$1–3 | Per-screen LLM generation |
| 5–8 (Parse, Graph, Slices) | 30–300s | ~$0 | Call resolution complexity |
| 9–10 (Chunk & Embed) | 30–120s | ~$0.05 | Embedding API quota |
| 11 (Tier 1) | 2–10 min | ~$0.50 | High volume; LLM concurrency limit |
| 12 (Tier 2) | 5–30 min | ~$2–5 | Reasoning; per-domain |
| 13 (Scenario flow inference) | 5–15 min | ~$1–2 | Per-scenario reasoning |
| 14 (Tier 3) | 10–60 min | ~$5–10 | Deep reasoning; per-doc-type |
| 15 (Visual artifacts) | 1–10s | ~$0 | Template rendering |
| 16 (Process mining, opt.) | 10–20 min | ~$0 | pm4py inductive miner |
| 17 (Self-review) | 5–20 min | ~$1–2 | Verification prompts |
| 18–19 (Render & finalise) | 1–5s | ~$0 | Template rendering |

**Total typical cost**: $10–25 per medium codebase (5K–50K LOC).

---

## Tuning Phases

### Speed Tuning
- Increase `max_concurrent` in discovery.yaml: faster Tier 1, but higher LLM quota usage
- Reduce `execution_slice_depth`: shallower scenarios, less detail
- Skip Tier 3: only generate Tier 1–2 docs (faster, less detailed)

### Cost Tuning
- Use Haiku for Tier 2 (cheaper, less detailed)
- Skip self-review (Phase 17)
- Limit domains analyzed (analyze only top-N by LOC)

### Quality Tuning
- Increase `execution_slice_depth`: deeper scenarios (>5), more comprehensive
- Use Opus for Tier 2 (more expensive, higher quality reasoning)
- Enable self-review + human review loop

---

## Monitoring & Debugging

### Check Phase Status
```bash
sqlite3 data/discovery.db "SELECT status, COUNT(*) FROM scan_runs GROUP BY status;"
```

### Check Phase Duration
```bash
sqlite3 data/discovery.db "
  SELECT 
    created_at, 
    updated_at, 
    (julianday(updated_at) - julianday(created_at)) * 24 * 60 as duration_minutes
  FROM scan_runs 
  WHERE project_slug = 'myproj'
  ORDER BY created_at DESC 
  LIMIT 5;
"
```

### Check LLM Costs Per Tier
```bash
sqlite3 data/discovery.db "
  SELECT 
    tier, 
    SUM(input_tokens) as total_input, 
    SUM(output_tokens) as total_output, 
    SUM(estimated_usd) as total_cost
  FROM llm_costs
  GROUP BY tier;
"
```

---

## See Also
- `docs/guides/pipeline/cost-tracking.md` — Budget control and cost analysis
- `docs/guides/pipeline/profiling.md` — Performance optimization and bottleneck identification
