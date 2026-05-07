# Architecture: AI-Discovery

## Purpose

AI-Discovery is a brownfield reverse-engineering engine. Given a source code repository (or a set of repositories), it automatically:

1. Parses code into a structured graph
2. Classifies business domains
3. Infers execution flows and scenarios
4. Generates SDLC documents (As-Is assessments, schema docs, process flows, BPMN diagrams) using a tiered LLM pipeline
5. Writes output as offline markdown under a predictable folder tree ready for batch ingestion into DocHub or Gitea

---

## High-Level Data Flow

Pipeline phase numbers in brackets match `_PHASE_SPECS` in `pipeline.py` (canonical). See `guides/pipeline/phase-breakdown.md` for per-phase details.

```
Source Repository
       │
       ▼
  [1–4] Pre-pipeline ─── DB init, repo resolve, resume/rescan, scan_run create
       │
       ▼
  [5] Detect Languages ──── extension + manifest scan ──── {python, java, csharp, javascript}
       │
       ▼
  [6] Parse ──── tree-sitter AST per file ──── CodeNode[]
       │           (class, method, endpoint, db_model, batch_job, ui_component)
       │
       ▼
  [7] Classify Domains + Build Call Graph ──── multi-strategy name resolution ──── CallEdge[]
       │
       ▼
  [8] Build Execution Slices ──── bounded BFS from entry points ──── Scenario[]
       │              + entity FSM rollup, consolidation (Phase 2a–2e), classification
       │
       ▼
  [9–10] Chunk + Embed ──── method-level splits + RAG overlap ──── sqlite-vec vectors
       │
       ▼
 [11] Tier 1 Summarize ──── Haiku / fast model, concurrent ──── node_summaries
       │
       ▼
 [12] Tier 2 Flow Analysis ──── Sonnet, per domain ──── business_flows
       │
       ▼
 [13] Scenario Flow Inference ──── per-scenario LLM reconstruction ──── scenario_flows
       │
       ▼
 [14] Tier 3 Doc Rollup ──── Sonnet/Opus, per domain×doc_type ──── generated_docs
       │
       ▼
 [15] Visual Artifacts ──── BPMN + DMN + EARS + Mermaid generation
       │
       ▼
 [16] Process Mining (optional) ──── pm4py inductive miner + conformance
       │
       ▼
 [17] Self-Review ──── claim extraction + RAG verify ──── review_claims, annotations
       │
       ▼
 [18] Render Markdown ──── Jinja2 templates ──── data/{slug}/{PREFIX}/*.md
       │
       ▼
 [19] Finalise ──── totals, scan_runs.status=completed
       │
       ▼
       discover ingest (separate command) ──── DocHub API / Gitea
```

---

## L1–L7 Decomposition Framework

AI-Discovery reconstructs and documents business processes at multiple abstraction levels:

| Level | Name | Scope | Artifacts | Source |
|-------|------|-------|-----------|--------|
| L1 | Business Domain | Business capability (Order, Payment, Fulfillment) | Domain metadata, tech stack, entry points | Domain classifier |
| L2 | Business Process | High-level workflow (Create Order → Pay → Ship) | As-Is, Process Flow BPMN, pseudo event logs | Tier 2 Flow Analysis |
| L3 | Business Flow | Coherent user/system action sequence | Business flows (user_flow, batch_flow, integration_flow, cross_cutting) | Tier 2 Flow Analysis |
| L4 | Scenario / Use Case | Single entry point + bounded execution | Scenario Flow, IPO tables, alternate paths | Execution Slice Builder + Tier 2 ScenarioFlowInference |
| L5 | Service / Component | Core business logic handler (Service, Repository, Manager) | As-Is-Detail, function summaries, public APIs | Tier 1 + Code structure |
| L6 | Function / Method | Individual operation (process, validate, save) | Docstring, summaries (purpose, business_rules), io_summary | Tier 1 Summarization |
| L7 | Code Statement | Individual lines of logic | Source code, comments, state transitions | AST parsing, framework_hints |

**Bridging**: Each level links to lower levels:
- L2 processes embed L3 flows
- L3 flows reference L4 scenarios
- L4 scenarios contain L5 service references
- L5 services list L6 function calls
- L6 functions map to L7 code

**Documents reflect this hierarchy**:
- `as-is.md` → L1–L3 (business domains, processes, flows)
- `as-is-detail.md` → L4–L5 (scenarios, service contracts)
- `as-is-schema.md` → L6–L7 (entity models, field descriptions)
- `process-flow.md` → L2–L4 (BPMN, IPO, pseudo event logs)

---

## LLM Tier Model

| Tier | Slot | Bedrock default | Ollama default | Role | Concurrency |
|------|------|-----------------|----------------|------|------------|
| Tier 1 | `tier1` | claude-haiku-4-5 | gemma4:e2b (2B) | Chunk summarization | High (`max_concurrent`) |
| Tier 2 | `tier2` | claude-sonnet-4-6 | gemma4:26b | Flow analysis | Per domain |
| Tier 3 dev | `tier3d` | claude-haiku-4-5 | gemma4:26b | Doc rollup (fast/cheap) | Configurable |
| Tier 3 prod | `tier3p` | claude-sonnet-4-6 | gemma4:31b | Doc rollup (deeper) | Configurable |

Tier 3 has two slots — `tier3d` (dev-default, cheap) and `tier3p` (prod-default, deeper). Pass `--prod` at scan time to swap in `tier3p`. Override any slot via `discovery.yaml` to pin Opus where you want it.

Budget guard: each tier checks `total_cost_usd < budget_limit_usd` before running.

---

## Storage Layout

### SQLite Database (`data/discovery-output/{slug}/discovery-{slug}.db`)

| Table | Purpose |
|-------|---------|
| `scan_runs` | One row per scan; status, commit SHA, config JSON |
| `code_nodes` | Parsed AST nodes; qualified_name, source_code, domain, framework_hints |
| `call_edges` | caller_id → callee_id; edge_type, confidence (0.5–1.0) |
| `domains` | Business domain metadata; entry_points, tech_stack (JSON) |
| `node_summaries` | Tier 1 LLM output per node; purpose, business_rules, io_summary |
| `business_flows` | Tier 2 domain flows; flow_type, name, involved node_ids |
| `scenario_flows` | Execution scenario; steps, IPO, mermaid/plantuml/bpmn artifacts |
| `generated_docs` | Tier 3 output; content_md, confidence, push_status, unverified_claims |
| `review_claims` | Self-review; claim_text, status (verified/unverified/contradicted) |
| `llm_costs` | Per-tier token counts and estimated USD cost |
| `schema_version` | Migration tracking |

### Generated Docs (`data/{slug}/{PREFIX}/{doc_id}.md`)

DocHub prefix folders:

| Doc Type | Folder | doc_id example |
|----------|--------|----------------|
| as-is | `ASIS/` | `myproj-orders-as-is.md` |
| as-is-detail | `ASD/` | `myproj-orders-as-is-detail.md` |
| as-is-schema | `ASSC/` | `myproj-orders-as-is-schema.md` |
| process-flow | `PF/` | `myproj-scenario-create-order-process-flow.md` |
| spec | `SPEC/` | `myproj-orders-spec.md` |

---

## CLI Commands

```
discover scan   REPO -p SLUG [--branch BRANCH] [--docs-root ./data]
                             [--output ./data/discovery-output]
                             [--resume] [--rescan] [--budget N] [--prod]

discover ingest -p SLUG --target dochub|gitea
                         [--api-url URL] [--api-key KEY]
                         [--gitea-url URL] [--gitea-token TOKEN]
                         [--only-failed]

discover chat   -p SLUG [--top-k 5] [--tier2]

discover query  "SELECT ..." --db path/to/discovery.db
```

---

**See also**:
- `docs/architecture/components.md` — Module breakdown
- `docs/architecture/decisions.md` — Design rationale
- `docs/guides/pipeline/phase-breakdown.md` — Detailed phase information
