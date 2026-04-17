# Execution Flow Detail

This document traces the data transformations at each stage in depth.

---

## Stage 1 — Repository Ingestion

```
Input:   repo URL or local path, branch name
Output:  ResolvedRepo { repo_path, branch, commit_sha, url, is_local }

├── If URL → clone to work_dir/{repo_name}/
│   ├── fetch origin
│   ├── checkout requested branch
│   └── fallback: remote HEAD branch if local branch missing
├── If local path with .git → read HEAD commit SHA
└── If local path without .git → compute SHA from content fingerprint
```

**Same-SHA cache:** if the resolved commit_sha matches an existing completed scan_run, the pipeline skips all parse and LLM phases and re-renders markdown from cached DB content.

---

## Stage 2 — Parsing

```
Input:   source files (filtered by language)
Output:  CodeNode[] per file

Per file:
  ├── Select parser: PythonParser | JavaParser | CSharpParser | JavaScriptParser
  ├── Run tree-sitter AST query
  └── Emit CodeNode for each:
        class, method, function, endpoint, db_model, batch_job, cli_command,
        event_consumer, ui_component

Each CodeNode carries:
  ├── qualified_name (e.g. com.corp.payments.OrderService.process)
  ├── calls[] — raw call references extracted from AST
  ├── annotations[] — decorators / attributes (e.g. @RestController)
  ├── framework_hints — boundaries (DB/API/QUEUE), state transitions
  ├── params, return_type, source_code, line_start, line_end
  └── domain (initially None; filled by domain_classifier)
```

---

## Stage 3 — Domain Classification

```
Input:   CodeNode[]
Output:  Domain[] — nodes grouped by inferred business capability

For each node:
  ├── Try: first non-framework segment from qualified_name
  ├── Try: first meaningful path segment (skip src/, app/, main/, etc.)
  └── Fallback: file stem with suffix stripped (Service, Controller, Repository…)

Domain object aggregates:
  ├── nodes[] — all CodeNodes in domain
  ├── entry_points[] — nodes with endpoint/batch_job/event_consumer type
  ├── db_models[] — nodes with db_model type
  ├── tech_stack — {language: set(frameworks)} detected from framework_hints
  └── internal_edges / external_edges — filled later after call graph build
```

---

## Stage 4 — Call Graph Construction

```
Input:   CodeNode[] (all nodes, with their .calls[] lists)
Output:  CallEdge[] { caller, callee, edge_type, confidence }

Index:
  ├── qualified_index: qualified_name → CodeNode
  └── short_name_index: name → [CodeNode, ...]

For each node.calls reference:
  1. Exact qualified_name match    → confidence 1.0
  2. Same class prefix match       → 0.95
  3. Same file, unique             → 0.90
  4. Same module, unique           → 0.85
  5. Unique suffix (.foo.bar)      → 0.85
  6. Best prefix overlap           → 0.65–0.75
  7. Ambiguous short name          → 0.60
  8. Unresolved                    → 0.50, type=UNRESOLVED
```

---

## Stage 5 — Execution Slice Construction

```
Input:   CodeNode[], CallEdge[]
Output:  Scenario[] — one per entry point

Entry point detection (identify_scenarios):
  ├── node_type in {endpoint, batch_job, cli_command, event_consumer}
  ├── function/method name contains: main, cli, cmd, command → CLI trigger
  └── function/method name contains: consumer, listener, subscriber, handler → EVENT trigger

Per entry point (bounded BFS, max_depth=5):
  ├── Create ExecutionNode for each reachable node
  │     ├── type = ENTRY | FUNCTION | DB | QUEUE | EXTERNAL_API | UNRESOLVED
  │     └── state_transition = StateTransition if framework_hints.transitions present
  ├── Score each node:
  │     ├── +5 – depth penalty (entry=5, each level -1)
  │     ├── +4 – state transition present
  │     ├── +3 – DB or QUEUE boundary
  │     ├── +2 – EXTERNAL_API boundary
  │     └── +3 – read-after-write (node name mentions upstream state field)
  └── primary_path = top-15 scored nodes (for BPMN readability)

Scenario output:
  ├── scenario_id, name, entry_point, trigger_type (HTTP/SCHEDULED/EVENT/CLI)
  ├── nodes[], edges[], primary_path[], external_interfaces[]
  └── domain (from entry CodeNode)
```

---

## Stage 6 — LLM Tier 1: Chunk Summarization

```
Input:   CodeNode[]
Output:  node_summaries { qualified_name, purpose, business_rules[], io_summary, tech_debt_signals[] }

Chunking:
  ├── Node ≤ 6000 chars → single chunk
  ├── Large class → split into per-method sub-chunks (with class header prefix)
  └── RAG chunks: further split to 1500 chars, 200-char overlap

Summarization (concurrent, Haiku/fast model):
  Prompt:
    "Summarize this code chunk. Return JSON:
     { purpose, business_rules[], io_summary, tech_debt_signals[] }"

  Result persisted to node_summaries table.
```

---

## Stage 7 — LLM Tier 2: Flow Analysis

```
Input:   Domain[], node_summaries
Output:  business_flows[], scenario_flows[]

Per domain (Sonnet/std model):
  Prompt:
    "Given these function summaries for domain X, identify the main business flows.
     Return a list of { name, type (CRUD/PROCESS/INTEGRATION), involved_nodes[] }"

ScenarioFlowInference (3-stage chain per Scenario):
  Stage A — Step extraction:
    "Convert this execution path [ExecutionNode.name list + summaries] into
     ordered business steps. Return { steps: [{step, name, type, description}] }"
  Stage B — IPO extraction:
    "From these business steps, extract:
     { inputs[], process_steps[], outputs[], data_flow }"
  Stage C — Interface detection:
    "Identify external systems from this flow:
     { interfaces: [{name, type (DB/API/QUEUE/FILE), direction (IN/OUT/INOUT)}] }"
```

---

## Stage 8 — LLM Tier 3: Document Rollup

```
Input:   Domain[], node_summaries, business_flows, RAG context
Output:  generated_docs (markdown per domain × doc_type)

Per domain × doc_type (Opus/deep model):
  ├── Retrieve doc-type-specific RAG context (top-5 code chunks)
  ├── Build prompt with:
  │     - domain name and tech stack
  │     - top summaries (≤20)
  │     - business flows
  │     - RAG context snippets
  │     - doc-type-specific instructions (what sections to generate)
  └── Result: full markdown (stored in generated_docs.content_md)

Doc types per domain (Phase 0):
  ├── as-is          — current architecture, APIs, technical debt, system boundaries
  ├── as-is-detail   — functional spec + API contracts + business rules
  └── as-is-schema   — entity model, key tables/fields, relationships
```

---

## Stage 9 — Self-Review

```
Input:   generated_docs, RAG embeddings
Output:  review_claims[], annotated/regenerated markdown

Per doc:
  ├── Extract factual claims (LLM: "list verifiable statements about code behavior")
  ├── For each claim: RAG search → retrieve supporting chunks
  ├── Verify claim against retrieved evidence (LLM: verified/unverified/contradicted)
  ├── If unverified/contradicted sections exist:
  │     └── Regenerate that section with RAG evidence as grounding
  └── Annotate doc with ⚠ markers on unverified claims

Persistence:
  ├── review_claims table — claim_text, status, evidence, source_file
  └── generated_docs.unverified_claims — count of bad claims
```

---

## Stage 10 — Artifact Generation & Markdown Rendering

```
Input:   scenario_flows, ScenarioFlow artifacts, rollups
Output:  .md files under data/{slug}/{PREFIX}/

BPMN Generator (per scenario_flow):
  ├── Mermaid sequence: participants + sequenceNumber flows
  ├── PlantUML activity: start → steps → decision points → stop
  ├── BPMN 2.0 XML: startEvent → serviceTask/userTask/gateway → endEvent
  └── IPO markdown table: inputs | process | outputs | data flow

Markdown render (Jinja2 templates):
  ├── Frontmatter: doc_id, title, status, scan_date, confidence, links_to
  ├── Cross-domain links: as-is docs link to called-domain as-is docs
  ├── Within-domain links: as-is-detail / as-is-schema → as-is
  └── Content from Tier 3 LLM output
```

---

## Trigger Type → BPMN Start Event Mapping

| Trigger | Detected By | BPMN Start Event |
|---------|-------------|-----------------|
| HTTP | `node_type == "endpoint"` | Message Start (HTTP request) |
| SCHEDULED | `node_type == "batch_job"` | Timer Start |
| EVENT | `node_type == "event_consumer"` or name contains `consumer/listener/subscriber/handler` | Message Start (event) |
| CLI | `node_type == "cli_command"` or name contains `main/cli/cmd` | None Start |

---

## Domain Adjacency Graph

The pipeline computes a cross-domain dependency graph from `call_edges`:

```sql
SELECT caller_domain, callee_domain, COUNT(*) AS edge_count
FROM call_edges
JOIN code_nodes ...
WHERE caller_domain != callee_domain
GROUP BY caller_domain, callee_domain
HAVING COUNT(*) >= 3
```

Only pairs with ≥ 3 cross-domain calls are retained (noise suppression). This graph drives:
- `links_to` in as-is frontmatter (Domain A → Domain B architecture links)
- Future: service topology views, dependency matrices
