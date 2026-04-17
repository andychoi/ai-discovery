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

```
Source Repository
       │
       ▼
  [1] Resolve ──── git clone / local path ──── ResolvedRepo (commit SHA, branch)
       │
       ▼
  [2] Detect Languages ──── extension + manifest scan ──── {python, java, csharp, javascript}
       │
       ▼
  [3] Parse ──── tree-sitter AST per file ──── CodeNode[]
       │           (class, method, endpoint, db_model, batch_job, ui_component)
       │
       ▼
  [4] Classify Domains ──── namespace / path heuristics ──── Domain[]
       │
       ▼
  [5] Build Call Graph ──── multi-strategy name resolution ──── CallEdge[]
       │
       ▼
  [6] Build Execution Slices ──── bounded BFS from entry points ──── Scenario[]
       │
       ▼
  [7] Chunk + Embed ──── method-level splits + RAG overlap ──── sqlite-vec vectors
       │
       ▼
  [8] Tier 1 Summarize ──── Haiku / fast model, concurrent ──── node_summaries
       │
       ▼
  [9] Tier 2 Flow Analysis ──── Sonnet, per domain ──── business_flows, scenario_flows
       │
       ▼
 [10] Tier 3 Doc Rollup ──── Opus, per domain×doc_type ──── generated_docs (markdown)
       │
       ▼
 [11] Self-Review ──── claim extraction + RAG verify ──── review_claims, annotations
       │
       ▼
 [12] Render Markdown ──── Jinja2 templates + BPMN/Mermaid ──── data/{slug}/{PREFIX}/*.md
       │
       ▼
 [13] Ingest (optional) ──── discover ingest ──── DocHub API / Gitea
```

---

## Component Map

```
app/
├── pipeline.py          ← Orchestrator (phases 1–13)
├── cli.py               ← CLI: discover scan | ingest | chat | query
├── config.py            ← YAML config + provider model routing
├── db.py                ← SQLite schema, connection helpers
│
├── repo/
│   ├── resolver.py      ← Clone/pull git; fingerprint local paths
│   ├── lang_detector.py ← Detect languages from file extensions + manifests
│   └── file_walker.py   ← Walk source files, skip framework dirs
│
├── parsers/
│   ├── base.py          ← LanguageParser interface
│   ├── python_parser.py ← Python (tree-sitter)
│   ├── java.py          ← Java (tree-sitter)
│   ├── csharp.py        ← C# (tree-sitter)
│   └── javascript.py    ← JS/TS (tree-sitter)
│
├── graph/
│   ├── models.py        ← CodeNode, CallEdge, Domain, Scenario, ExecutionNode, ScenarioFlow
│   ├── call_graph.py    ← Call resolution + ExecutionSliceBuilder
│   └── domain_classifier.py ← Namespace/path-based domain grouping
│
├── ai/
│   ├── chunker.py       ← Method-level splits; RAG chunk generation
│   ├── summarizer.py    ← Tier 1: per-chunk summaries
│   ├── flow_analyzer.py ← Tier 2: domain flows + ScenarioFlowInference
│   ├── rollup.py        ← Tier 3: full SDLC doc generation
│   ├── self_review.py   ← Claim extraction + RAG-grounded verification
│   └── llm_client.py    ← Unified Bedrock / Ollama / MLX client + cost tracking
│
├── rag/
│   ├── embedder.py      ← Embed chunks into sqlite-vec; resume-aware
│   ├── doc_embedder.py  ← Embed generated docs for doc-level search
│   ├── retriever.py     ← KNN semantic search
│   └── chat.py          ← Interactive RAG REPL
│
├── output/
│   ├── doc_generator.py ← Jinja2 render; slug doc_ids; PREFIX folder layout
│   ├── bpmn_generator.py← Mermaid, PlantUML, BPMN 2.0 XML, IPO table
│   ├── push.py          ← Ingest to DocHub API / Gitea / offline copy
│   └── templates/       ← *.md.j2 per doc type
│
└── ingest/
    ├── frontmatter.py   ← Parse/generate doc frontmatter
    ├── classifier.py    ← Classify existing docs by keyword
    └── reference_extractor.py ← Extract doc cross-references
```

---

## Pipeline Phases (detail)

| # | Phase | Key Input | Key Output | Notes |
|---|-------|-----------|------------|-------|
| 1 | Init DB | db_path | SQLite schema | WAL mode, schema_version migration |
| 2 | Resolve Repo | repo URL / path | ResolvedRepo | Clone or fingerprint; branch fallback |
| 3 | Resume/Rescan | prev scan_run | skip or resume | Same-SHA cache skips parse+LLM |
| 4 | Create Scan Run | metadata | scan_id | Status: running |
| 5 | Detect Languages | repo_path | lang_stats | Extensions + manifest files |
| 6 | Parse Files | source files | CodeNode[] | Tree-sitter AST per language |
| 7 | Classify Domains | CodeNode[] | Domain[] | Namespace/path heuristics |
| 8 | Persist Nodes | CodeNode[] | DB rows | code_nodes table |
| 9 | Build Call Graph | CodeNode[] | CallEdge[] | Multi-strategy name resolution |
| 10 | Build Exec Slices | entry points + edges | Scenario[] | Bounded BFS depth≤5 |
| 11 | Distribute Edges | edges + domain map | domain.{internal,external}_edges | Cross-domain adjacency |
| 12 | Persist Domains/Edges | domains, edges | DB rows | domains, call_edges tables |
| 13 | Chunk + Embed | CodeNode[] | CodeChunk[], RAG vectors | sqlite-vec; resume-aware |
| 14 | Tier 1 Summarize | chunks | node_summaries | Concurrent; Haiku/fast model |
| 15 | Tier 2 Flow Analysis | domains + summaries | business_flows, scenario_flows | Per domain; Sonnet/std model |
| 16 | Tier 3 Doc Rollup | domains + flows | generated_docs (markdown) | Per domain×type; Opus/deep model |
| 17 | Self-Review | generated_docs | review_claims | Claim verify via RAG; section regen |
| 18 | Render Markdown | rollups + artifacts | .md files | data/{slug}/{PREFIX}/{doc_id}.md |
| 19 | Finalise | scan_id | status=completed | LLM cost persisted |

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

## LLM Tier Model

| Tier | Model (Bedrock) | Model (Ollama) | Role | Concurrency |
|------|----------------|----------------|------|------------|
| Tier 1 | Claude Haiku | gemma4:e2b (2B) | Chunk summarization | High (max_concurrent) |
| Tier 2 | Claude Sonnet | gemma4:26b (26B) | Flow analysis | Per domain |
| Tier 3 (dev) | Claude Opus | gemma4:26b | Doc rollup | Configurable |
| Tier 3 (prod) | Claude Opus | gemma4:31b | Doc rollup | Configurable |

Budget guard: each tier checks `total_cost_usd < budget_limit_usd` before running.

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

## BPMN Model: Lanes, SubProcesses, and Execution Types

### Swimlane Lanes (Horizontal Partitions)

Each BPMN diagram is divided into three swimlanes (left to right):

| Lane | Actors | Typical Elements |
|------|--------|------------------|
| **User** | End users, administrators | User Task, Start/End events |
| **System** | Application code, services | Service Task, Gateways, Data stores |
| **External** | Third-party systems, databases, APIs | Service Task (external), Message events |

Lane assignment heuristic:
- `USER_TASK` / `ENTRY` (HTTP from UI) → **User lane**
- `PROCESS` / `FUNCTION` / `TRANSITION` → **System lane**
- `DB` / `EXTERNAL_API` / `QUEUE` → **External lane**

### SubProcess Grouping

When scenario flow has >15 steps:
1. Group related steps into clusters (validation, processing, integration)
2. Wrap each cluster in `<bpmn:subProcess>` element
3. Preserve parent process flow linking to subprocess boundary

### Execution Node Type → BPMN Element Mapping

| ExecutionNode Type | BPMN Element | Icon |
|-------------------|------|------|
| ENTRY | startEvent | ⭕ |
| USER_TASK | userTask | 👤 |
| DB | dataStore + dataAssociation | 🗄 |
| QUEUE | intermediateThrowEvent (messageFlow) | 📨 |
| EXTERNAL_API | serviceTask (external) | 🔗 |
| GATEWAY | exclusiveGateway | ◇ |
| MANUAL (synthetic) | userTask (low confidence: 0.3–0.6) | ⚠️ |
| FUNCTION | serviceTask | ⚙️ |
| (end of flow) | endEvent | ⭕ |

---

## ExecutionNode Types

When building execution slices (Stage 5), each discovered node is classified into one of these types:

| Type | Meaning | Confidence | Example |
|------|---------|------------|---------|
| `ENTRY` | Entry point (HTTP endpoint, batch job, event consumer, CLI) | 1.0 (by definition) | `POST /api/orders` |
| `FUNCTION` | Internal function or method | 0.6–1.0 (from call resolution) | `validateOrder()`, `calculatePrice()` |
| `DB` | Database boundary (detected from framework hints) | 0.8–1.0 | Repository call, ORM persist |
| `QUEUE` | Message queue (Kafka, SQS, Redis) | 0.8–1.0 | Event publish, queue consume |
| `EXTERNAL_API` | External HTTP/gRPC call | 0.7–1.0 | Payment gateway, third-party API |
| `MANUAL` | Synthetic manual step (inferred, not in code) | 0.3–0.6 | Manager Approval, Compliance Review |
| `UNRESOLVED` | Unknown target from parsing | 0.5 | Unmatched call reference |

**MANUAL nodes** are injected via heuristics:
- Function names containing `approve`, `review`, `validate_manually` → synthetic user task
- State transitions with large gaps (DRAFT → APPROVED without intermediate) → suggest approval step
- Integration points with no direct code (business rule gates) → synthetic decision point

---

## Confidence Scoring & Human Feedback

### Node Confidence Signals (Stage 5)

Multi-signal scoring in ExecutionSliceBuilder combines:

```
score = 0
score += max(0, 5 - depth)                    // +5 entry, -1 per level
score += 4 if state_transition                // +4 state changes
score += 3 if node_type in (DB, QUEUE)        // +3 data boundary
score += 2 if node_type == EXTERNAL_API       // +2 external call
score += 3 if read_after_write_detected       // +3 state dependency
```

Nodes with confidence < 0.6 (ambient external calls, ambiguous resolves) are marked for human review.

### Human-in-the-Loop Correction

For scenarios with high unverified claim rates (>20%) or low scenario confidence (<0.7):

1. **AI suggests** low-confidence nodes and alternate paths
2. **Human validates** against domain knowledge or runtime logs
3. **System learns**: feedback updates confidence thresholds and heuristics
4. **Iterative refinement**: re-run analysis with feedback

This is especially valuable for:
- Synthetic MANUAL nodes (need business confirmation)
- Conditional branches with unclear semantics
- Multi-tenant or highly polymorphic code

---

## Call Resolution Strategy

When a parsed `calls` reference cannot be matched exactly:

1. Exact qualified_name match → confidence **1.0**
2. Same class owner prefix → **0.95**
3. Same file, unique → **0.90**
4. Same module, unique → **0.85**
5. Unique suffix match → **0.85**
6. Best prefix overlap → **0.65–0.75**
7. Ambiguous short-name → **0.60**
8. Unresolved / external → **0.50** (type `UNRESOLVED`, not `EXTERNAL_API`)

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
