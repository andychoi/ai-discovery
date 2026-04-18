# Pipeline Phases: Detailed Breakdown

Reference guide for all AI-Discovery pipeline phases: pre-pipeline setup (phases 1–4) and checkpoint phases (5–16, including optional sub-phases 8.5, 12.5, 13.5, 13.6).

---

## Phases 1–5: Repository Resolution & Parsing

### Phase 1: Database Initialization
**Input**: `db_path`  
**Output**: SQLite schema ready  
**Time**: < 1s

Initializes SQLite database with schema (code_nodes, call_edges, domains, etc.). Enables WAL mode for concurrent writes. Checks schema version for migrations.

### Phase 2: Repository Resolution
**Input**: Repo URL or local path, branch  
**Output**: `ResolvedRepo(commit_sha, branch, local_path)`  
**Time**: 1–30s (depending on clone size)

If URL: clones repository to local cache (skip if cached).  
If local path: fingerprints it (checks commit SHA, branch).  
Fallback: if target branch unavailable, tries main/master.

### Phase 3: Resume/Rescan Logic
**Input**: Previous scan run (if resuming)  
**Output**: Decision to skip, resume, or rescan  
**Time**: < 1s

If `--resume` flag: loads previous scan state, skips already-parsed files.  
If `--rescan` flag: deletes previous scan, starts fresh.  
If same commit SHA: skips phases 4–7 (parsing), resumes at phase 8 (chunking).

### Phase 4: Create Scan Run
**Input**: Scan metadata (repo, branch, config)  
**Output**: `scan_id` (unique identifier for this scan)  
**Time**: < 1s

Creates entry in `scan_runs` table. Records start time, status, config. Later updated with completion time and cost.

### Phase 5: Language Detection
**Input**: Repository files  
**Output**: `lang_stats { python: 150, java: 50, javascript: 20, ... }`  
**Time**: 1–5s

Scans file extensions and manifest files (package.json, pom.xml, requirements.txt) to detect languages.

---

## Phases 6–9: Code Parsing & Graph Building

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

### Phase 8: Persist Nodes
**Input**: `CodeNode[]`, `Domain[]`  
**Output**: Rows in `code_nodes` and `domains` tables  
**Time**: 1–10s

Writes to SQLite. Handles duplicates (same node parsed multiple times). Sets resume checkpoints.

### Phase 9: Build Call Graph
**Input**: `CodeNode[]` (all parsed nodes with call references)  
**Output**: `CallEdge[]` (resolved calls with confidence scores)  
**Time**: 10–60s (depends on codebase size and complexity)

Multi-strategy call resolution (7-level confidence scoring).  
For each unresolved call reference:
1. Try exact match (confidence 1.0)
2. Try class owner prefix (0.95)
3. Try file local (0.90)
4. Try module local (0.85)
5. Try suffix unique (0.85)
6. Try prefix overlap (0.65–0.75)
7. Mark unresolved (0.50)

Stores edges in `call_edges` table.

---

## Phases 10–12: Execution Slicing & Distribution

### Phase 10: Build Execution Slices
**Input**: `CodeNode[]`, `CallEdge[]`, entry points  
**Output**: `Scenario[]` (execution scenarios from each entry point)  
**Time**: 5–20s

Bounded BFS traversal (depth ≤ 5) from each entry point:
- Primary path: top-15 nodes by confidence, in BFS order
- Alternate paths: conditional branches detected from nodes with >1 callee
- Confidence scoring: multi-signal (depth, state transition, data boundary, etc.)

Stores scenarios in `scenario_flows` table.

### Phase 11: Distribute Edges
**Input**: `CallEdge[]`, `Domain[]`  
**Output**: `domain.internal_edges`, `domain.external_edges`  
**Time**: < 1s

Categorizes call edges as:
- Internal: both caller and callee in same domain
- External: caller and callee in different domains

Used by Tier 2 analysis to understand domain boundaries.

### Phase 12: Persist Domains & Edges
**Input**: `domain.internal_edges`, `domain.external_edges`  
**Output**: Updated `domains` table  
**Time**: 1–5s

Writes domain metadata (internal/external edge counts, cross-domain dependencies).

---

## Phases 13–15: Chunking & Embedding

### Phase 13: Chunk & Embed
**Input**: `CodeNode[]` (all parsed nodes)  
**Output**: `CodeChunk[]` + vectors in sqlite-vec  
**Time**: 30–120s (depends on codebase size and embedding model)

Method-level splits with RAG overlap:
- Each method → one chunk
- Surrounding context (class definition, imports) → prepended

Embeds chunks using embedding model (typically Ada or similar).  
Stores vectors in sqlite-vec for later semantic search.

Resume-aware: skips already-embedded chunks.

---

## Phases 14–16: LLM Pipeline (Tiered)

### Phase 14: Tier 1 Summarize
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

### Phase 15: Tier 2 Flow Analysis
**Input**: `domain[]` + `node_summaries`  
**Output**: `business_flows`, `scenario_flows` (execution flows per scenario)  
**Time**: 5–30 min (Sonnet model, per-domain)

Per-domain analysis:
1. Extract business flows (user_flow, batch_flow, integration_flow, cross_cutting)
2. Analyze scenario execution (steps, IPO, alternate paths)

Uses Claude Sonnet or Ollama gemma4:26b.  
Stores in `business_flows` and `scenario_flows` tables.

### Phase 16: Tier 3 Doc Rollup
**Input**: `domain[]` + `business_flows` + `scenario_flows`  
**Output**: `generated_docs` (markdown content, BPMN, diagrams)  
**Time**: 10–60 min (Opus model, per-domain×doc_type)

Final document generation per domain×doc_type:
- as-is.md (business overview)
- as-is-detail.md (technical details)
- as-is-schema.md (data models)
- process-flow.md (BPMN + IPO)

Uses Claude Opus or Ollama gemma4:31b.  
Stores in `generated_docs` table.

---

## Phases 17–19: Verification & Output

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
| 1–5 (Parse) | 10–100s | ~$0 | Cloning large repos |
| 6–12 (Graph) | 30–300s | ~$0 | Call resolution complexity |
| 13 (Embed) | 30–120s | ~$0.05 | Embedding API quota |
| 14 (Tier 1) | 2–10 min | ~$0.50 | High volume; LLM concurrency limit |
| 15 (Tier 2) | 5–30 min | ~$2–5 | Reasoning; per-domain |
| 16 (Tier 3) | 10–60 min | ~$5–10 | Deep reasoning; per-doc-type |
| 17 (Review) | 5–20 min | ~$1–2 | Verification prompts |
| 18–19 (Output) | 1–5s | ~$0 | Template rendering |

**Total typical cost**: $10–25 per medium codebase (5K–50K LOC).

---

## Tuning Phases

### Speed Tuning
- Increase `max_concurrent_tier_1`: faster Tier 1, but higher LLM quota usage
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
