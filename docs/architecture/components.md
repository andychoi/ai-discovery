# Component Map

## Module Breakdown

```
src/ai_discovery/
├── pipeline.py          ← Orchestrator (phases 5–16)
├── cli/                 ← CLI v1: init, scan, status, doctor, serve, mcp, export, drift, push, ask, impact
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
│   ├── javascript.py    ← JS/TS (tree-sitter)
│   ├── import_map.py    ← Import-map tier (regex): Go/Rust/Ruby/PHP symbols + imports + call sites
│   ├── jsp.py           ← JSP (.jsp/.jspx/.tag) → ui_component nodes
│   └── webforms.py      ← WebForms (.aspx/.ascx) → ui_component nodes
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
│   ├── flow_steps.py    ← scenario step vocabulary; rewrites retired GATEWAY/USER_TASK steps (ADR-0007)
│   ├── self_review.py   ← Claim extraction + RAG-grounded verification
│   ├── llm_client.py    ← Unified Bedrock / Ollama / MLX client + cost tracking
│   ├── semantic_batching.py ← Louvain community clustering of Tier-1 chunks (one structured call per community)
│   ├── flow_clustering.py ← Intent clustering for scenario grouping
│   └── process_miner.py ← per-scenario edge-frequency flow statistics (pm4py removed, decisions.md)
│
├── rag/
│   ├── embedder.py      ← Embed chunks into sqlite-vec; resume-aware
│   ├── doc_embedder.py  ← Embed generated docs for doc-level search
│   └── retriever.py     ← KNN semantic search (used by `discover ask` / MCP search)
│
├── generators/
│   ├── flow_diagrams.py ← Mermaid (sequence + flowchart), IPO table, FSM / entity backbone, pseudo event log (no BPMN)
│   ├── onboarding_generator.py ← per-domain tour steps (rendered as onboarding pages, `pages/builders/flows.py`)
│   └── templates/       ← screen-spec templates (screen_doc_writer)
│
├── pages/               ← the wiki: builders/ (one module per page family), prose.py, grounding.py, build.py
├── export/              ← `discover export`: json.py (knowledge graph v2), html.py (file:// wiki), markdown.py
```

## Responsibility Map

### Parsing & Graph Construction
- **`parsers/`** — Language-specific AST parsing (tree-sitter)
  - Each parser implements `LanguageParser` interface
  - Converts AST to `CodeNode` + call references
  - **Hard problem**: Name resolution (what function is being called?)

- **`parsers/import_map.py`** — Import-map tier (regex, not tree-sitter)
  - Lightweight symbol + import + call-site extraction for Go/Rust/Ruby/PHP
  - Module separators normalized to dots so Stage-2 import-scoped resolution works unchanged
  - Turns "unsupported language" from a cliff into a gradient (the on-ramp, not the destination)

- **`parsers/jsp.py`, `parsers/webforms.py`** — Server-rendered page parsers
  - JSP (`.jsp`/`.jspx`/`.tag`) and WebForms (`.aspx`/`.ascx`) → `ui_component` nodes linked to their backing class
  - Feed folder-hierarchy-based screen detection when no JS menu/router exists

- **`graph/call_graph.py`** — Call resolution + execution slicing
  - Multi-strategy name resolution (7-level confidence scoring)
  - `ExecutionSliceBuilder`: BFS traversal from entry points
  - Confidence signals: exact match, prefix, suffix, external, unresolved
  - **Hard problem**: Balancing precision vs recall in resolution

- **`graph/domain_classifier.py`** — Business domain grouping
  - Namespace/path heuristics
  - Groups nodes into logical business domains

### LLM Pipeline
- **Tier 1: `ai/summarizer.py`** (Haiku/fast)
  - Per-chunk summaries (purpose, business_rules, io_summary)
  - High concurrency; cost-effective

- **`ai/semantic_batching.py`** — Louvain community batching for Tier 1
  - Clusters files by call-graph community (weighted file graph from Phase-7 edges) before summarization
  - One structured LLM call per community, so each batch carries chunks that actually reference each other
  - Degrades loudly to deterministic domain/path grouping (never drops chunks)
  
- **Tier 2: `ai/flow_analyzer.py`** (Sonnet/standard)
  - Per-domain flow analysis
  - Business process discovery, scenario flow inference
  
- **Page prose: `pages/prose.py` + `pages/grounding.py`** (standard role, `--prose`)
  - One grounded narrative per overview/domain/screen/flow/entity/onboarding page (M3.7.a, M3.8)
  - The Tier-3 SDLC rollup was removed in M3.7.b

### Output & Storage
- **`generators/flow_diagrams.py`** — Artifact generation
  - Mermaid sequence diagrams and flowchart activity diagrams of scenario flows (a guard is an `opt` block or
    edge label; no gateways)
  - IPO markdown tables, entity FSM / backbone Mermaid, pseudo event log for flow statistics
  - **No BPMN** (ADR-0007): a call slice cannot tell sequence from choice, or see people, waits or timers

- **`pages/`** — the wiki (ADR-0005): builders turn the scan into pages; with `--prose` one grounded narrative
  per overview/domain/screen/flow/entity/onboarding page (`prose.py`, `grounding.py`). The legacy
  `doc_generator.py` SDLC tree (ASIS/ASD/ASSC/PF/…) and the ONBOARD docs were removed in M3.7.b.

### Configuration & Execution
- **`pipeline.py`** — Orchestrator
  - Coordinates checkpoint phases 5–16 (plus pre-pipeline setup phases 1–4)
  - Manages database, LLM budget, resume logic
  - **Entry point**: Main execution engine

- **`config.py`** — YAML configuration
  - Model routing (Bedrock vs Ollama vs MLX)
  - Budget limits, concurrency settings
  - Output paths

---

## Data Models

Core types in `graph/models.py`:

```python
@dataclass
class CodeNode:
    id: str
    qualified_name: str
    node_type: str  # class, method, function, endpoint, db_model, etc.
    source_code: str
    domain: str
    framework_hints: List[str]
    confidence: float

@dataclass
class CallEdge:
    caller_id: str
    callee_id: str
    edge_type: str  # direct, indirect, external, unresolved
    confidence: float  # 0.5–1.0

@dataclass
class Scenario:
    id: str
    entry_point_id: str
    nodes: List[CodeNode]
    edges: List[CallEdge]
    primary_path: List[str]  # BFS order, top-15 confidence
    stop_reasons: dict  # where the trace stops and why (no alternate paths: fan-out is not branching, ADR-0007)

@dataclass
class ExecutionNode:
    id: str
    node_type: str  # ENTRY, FUNCTION, DB, QUEUE, EXTERNAL_API, MANUAL, UNRESOLVED
    confidence: float
    step_order: int
    state_transitions: List[dict]

@dataclass
class ScenarioFlow:
    scenario_id: str
    steps: List[ExecutionNode]
    ipo_table: dict  # inputs, process, outputs
    mermaid_diagram: str
    state_machine: str
```

---

## Hard Problem Areas

### 1. Call Graph Resolution (Area A)
**File**: `src/ai_discovery/graph/call_graph.py`

**Why it's hard**:
- Function names are context-dependent (same name, different modules)
- Dynamic calls (reflection, higher-order functions) can't be resolved statically
- Confidence scoring must balance precision (false negatives) vs recall (false positives)

**Approach**:
- 7-level heuristic scoring (exact → prefix → suffix → external → unresolved)
- Confidence range: 0.5–1.0 depending on match quality
- **See**: `docs/guides/call-graph/resolution-heuristics.md`

### 2. Language Parser Extension (Area D)
**File**: `src/ai_discovery/parsers/*.py`

**Why it's hard**:
- Each language has different AST structure, naming conventions, call semantics
- Tree-sitter grammars vary in coverage & accuracy
- Hard to test: need real codebases in target language

**Approach**:
- Templated parser interface in `src/ai_discovery/parsers/base.py`
- Language-specific heuristics for name resolution
- Test corpus collection + validation checklist
- **See**: `docs/guides/parsers/extension-checklist.md`

---

## Integration Points

- **Parser → Call Graph**: Parsers emit call references; call_graph resolves them
- **Call Graph → Execution Slices**: ExecutionSliceBuilder traverses edges BFS
- **Execution Slices → LLM Pipeline**: Scenarios fed to Tier 1, 2, 3 for summarization & doc generation
- **Scenario flows → Diagrams**: rendered as Mermaid and IPO (MCP `get_flow`)
- **Scan → Pages**: builders turn the stored graph, screens and entity FSMs into wiki pages; `--prose` adds grounded narratives

---

**See also**:
- `docs/architecture/decisions.md` — Why we chose these heuristics
- `docs/guides/call-graph/` — Call resolution deep-dive
- `docs/guides/parsers/` — Parser extension guide
