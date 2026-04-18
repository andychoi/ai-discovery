# Component Map

## Module Breakdown

```
src/ai_discovery/
├── pipeline.py          ← Orchestrator (phases 5–16)
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
│   ├── llm_client.py    ← Unified Bedrock / Ollama / MLX client + cost tracking
│   ├── flow_clustering.py ← Intent clustering for scenario grouping
│   └── process_miner.py ← PM4Py integration for process discovery
│
├── rag/
│   ├── embedder.py      ← Embed chunks into sqlite-vec; resume-aware
│   ├── doc_embedder.py  ← Embed generated docs for doc-level search
│   ├── retriever.py     ← KNN semantic search
│   └── chat.py          ← Interactive RAG REPL
│
├── output/
│   ├── doc_generator.py ← Jinja2 render; slug doc_ids; PREFIX folder layout
│   ├── bpmn_generator.py← Mermaid, PlantUML, BPMN 2.0 XML, IPO table, FSM, pseudo event log
│   ├── push.py          ← Ingest to DocHub API / Gitea / offline copy
│   └── templates/       ← *.md.j2 per doc type
│
└── ingest/
    ├── frontmatter.py   ← Parse/generate doc frontmatter
    ├── classifier.py    ← Classify existing docs by keyword
    └── reference_extractor.py ← Extract doc cross-references
```

## Responsibility Map

### Parsing & Graph Construction
- **`parsers/`** — Language-specific AST parsing (tree-sitter)
  - Each parser implements `LanguageParser` interface
  - Converts AST to `CodeNode` + call references
  - **Hard problem**: Name resolution (what function is being called?)

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
  
- **Tier 2: `ai/flow_analyzer.py`** (Sonnet/standard)
  - Per-domain flow analysis
  - Business process discovery, scenario flow inference
  
- **Tier 3: `ai/rollup.py`** (Opus/deep)
  - Full SDLC document generation
  - Self-review + claim verification via RAG

### Output & Storage
- **`output/bpmn_generator.py`** — Artifact generation
  - BPMN 2.0 XML with swimlanes
  - Mermaid sequence/state diagrams
  - PlantUML activity diagrams
  - IPO markdown tables
  - Pseudo event logs for process mining

- **`output/doc_generator.py`** — Jinja2 rendering
  - Converts generated content to markdown
  - Folder layout: `data/{slug}/{PREFIX}/{doc_id}.md`

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
    alternate_paths: List[dict]  # { condition, path }

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
    bpmn_xml: str
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
- **LLM Output → BPMN Artifacts**: Generated flows rendered as BPMN, IPO, state machines
- **Artifacts → Markdown**: Jinja2 templates combine LLM output + artifacts into final docs

---

**See also**:
- `docs/architecture/decisions.md` — Why we chose these heuristics
- `docs/guides/call-graph/` — Call resolution deep-dive
- `docs/guides/parsers/` — Parser extension guide
