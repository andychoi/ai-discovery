# Architecture: AI-Discovery

## Purpose

AI-Discovery is a brownfield reverse-engineering engine. Given a source code repository (or a set of repositories), it automatically:

1. Parses code into a structured graph
2. Classifies business domains
3. Infers execution flows and scenarios
4. Builds a wiki of pages (ADR-0005): screens, flows, entities with their lifecycles and decision tables,
   tables, endpoints, batch jobs, domains. `--prose` adds a narrative to each page, grounded against the index.
5. Serves it locally (`discover serve`, MCP), exports it (`discover export`) and delivers it (`discover push`
   to DocHub or Gitea)

Business processes are shown as **evidence**: entity lifecycles from literal state assignments, plus what drives
each change (trigger, guard, flows, screens). No BPMN is inferred from code (ADR-0007).

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
  [2] Screen LLM Specs ──── menu/route detection → screen-centric specs (parallel to domain phases)
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
 [15] Visual Artifacts ──── scenario Mermaid (sequence, flowchart) + IPO; no BPMN (ADR-0007)
       │
       ▼
 [16] Flow statistics (optional) ──── per-scenario edge frequencies
       │
       ▼
 [17] Self-Review (quality: final) ──── screen-spec claim verification
       │
       ▼
 [19] Finalise ──── totals, scan_runs.status=completed
       │
       ▼
 Page build (post-scan hook) ──── pages/ builders (+ grounded prose with --prose) ──── pages table
       │
       ▼
       discover push (separate command) ──── DocHub API / Gitea
```

---

## L1–L7 Decomposition Framework

AI-Discovery reconstructs and documents business processes at multiple abstraction levels:

| Level | Name | Scope | Artifacts | Source |
|-------|------|-------|-----------|--------|
| L1 | Business Domain | Business capability (Order, Payment, Fulfillment) | Domain metadata, tech stack, entry points | Domain classifier |
| L2 | Business Process | High-level workflow (Create Order → Pay → Ship) | Entity lifecycle + what drives each change (no BPMN, ADR-0007) | Entity FSM + flow traces + screen mapping |
| L3 | Business Flow | Coherent user/system action sequence | Business flows (user_flow, batch_flow, integration_flow, cross_cutting) | Tier 2 Flow Analysis |
| L4 | Scenario / Use Case | Single entry point + bounded execution | Scenario Flow, IPO tables, where the trace stops | Execution Slice Builder + Tier 2 ScenarioFlowInference |
| L5 | Service / Component | Core business logic handler (Service, Repository, Manager) | As-Is-Detail, function summaries, public APIs | Tier 1 + Code structure |
| L6 | Function / Method | Individual operation (process, validate, save) | Docstring, summaries (purpose, business_rules), io_summary | Tier 1 Summarization |
| L7 | Code Statement | Individual lines of logic | Source code, comments, state transitions | AST parsing, framework_hints |

**Bridging**: Each level links to lower levels:
- L2 processes embed L3 flows
- L3 flows reference L4 scenarios
- L4 scenarios contain L5 service references
- L5 services list L6 function calls
- L6 functions map to L7 code

**Pages reflect this hierarchy** (drill down in the wiki: screen → flow → entity → table):
- `domain`, `onboarding` pages → L1–L3
- `screen`, `flow`, `entity` (lifecycle, DMN, EARS) pages → L2–L4
- `module`, `endpoint`, `batch_job`, `symbol` pages → L5–L6
- `file`, `table`, `stored_proc` pages and citations → L6–L7

---

## LLM Tier Model

Each tier maps to a model **slot** — `tier1` (fast summarization), `tier2` (flow
analysis), `tier3d` (dev doc rollup, fast/cheap), `tier3p` (prod doc rollup,
deeper). Tier 3 has two slots; pass `--prod` at scan time to swap in `tier3p`.

| Tier | Slot | Bedrock default | Ollama default | Role | Concurrency |
|------|------|-----------------|----------------|------|------------|
| Tier 1 | `tier1` | claude-haiku-4-5 | gemma4:e2b (2B) | Chunk summarization | High (`max_concurrent`) |
| Tier 2 | `tier2` | claude-sonnet-4-6 | gemma4:26b | Flow analysis | Per domain |
| Tier 3 dev | `tier3d` | claude-sonnet-4-6 | gemma4:26b | Doc rollup (fast/cheap) | Configurable |
| Tier 3 prod | `tier3p` | claude-opus-4-6 | gemma4:31b | Doc rollup (deeper) | Configurable |

### Multi-Provider Routing

The same four tier slots are defined per **provider**. Seven providers ship:
`bedrock`, `ollama`, `mlx-gemma`, `mlx-qwen`, `openai`, `gemini`, `anthropic`
(the cloud APIs are OpenAI-compatible except `anthropic`, which uses the
Anthropic SDK directly). Routing is resolved by `shared/llm_router.py` with this
precedence: **runtime overrides → `DiscoveryConfig` → env vars → hardcoded
defaults** (`shared/model_defaults.py`). Each provider has its own config block
in `config.py` (`BedrockConfig`, `OllamaConfig`, `MLXGemmaConfig`,
`MLXQwenConfig`, `OpenAIConfig`, `GeminiConfig`, `AnthropicConfig`), each exposing
`tier1` / `tier2` / `tier3d` / `tier3p` (and a `tier1_num_ctx` for local
runtimes).

Switch provider and override any slot in `discovery.yaml`:

```yaml
provider: openai        # bedrock | ollama | mlx-gemma | mlx-qwen | openai | gemini | anthropic
max_concurrent: 10
budget_limit_usd: 50.00

openai:                 # block name matches the selected provider
  tier1: gpt-5-nano
  tier2: gpt-5-mini
  tier3d: gpt-5-mini
  tier3p: gpt-5.1
```

Override any slot via `discovery.yaml` to pin a deeper model where you want it.

> ⚠ **Use model ids your Bedrock account can invoke.** If a role's model id isn't enabled, every call on that role fails with `ValidationException: The provided model identifier is invalid` while the scan still completes. Set `bedrock.fast|standard|deep` (or `models.*`) and check with `discover doctor --llm`.

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
| `scenario_flows` | Execution scenario; steps, IPO, mermaid sequence/flowchart (`bpmn_xml` no longer written, M3.11) |
| `generated_docs` | Tier 3 output; content_md, confidence, push_status, unverified_claims |
| `review_claims` | Self-review; claim_text, status (verified/unverified/contradicted) |
| `llm_costs` | Per-tier token counts and estimated USD cost |
| `schema_version` | Migration tracking |

### Wiki pages (`pages` table)

Every document is a page (ADR-0005): `overview`, `domain`, `module`, `file`, `symbol`, `endpoint`, `screen`,
`entity` (FSM + DMN + EARS), `table`, `stored_proc`, `batch_job`, `interface`, `flow`, `onboarding`. Read them
with `discover serve`, `discover export --format markdown|html|json`, MCP, or `discover push`. The legacy
`data/{slug}/{PREFIX}/` tree (ASIS/ASD/ASSC/PF/SPEC/ONBOARD) was removed in M3.7.b.


---

## CLI Commands

The v1 command set (M2): `init`, `scan`, `status`, `doctor`, `serve`, `export`, `drift`, `push`, `ask`, `impact`,
`mcp`. The generated reference with every option is README.md → "CLI Reference" (`scripts/gen_cli_reference.py`).

```
discover init [--yes] [--prose] [--git-hook]
discover scan [--prose --yes] [--full] [--only STAGES] [--dry-run] [--watch]
discover serve                      # wiki + API v1 on one local port
discover ask "where are orders paid?"
discover impact table:orders        # what may break
discover drift                      # exit 6 when sources changed since the scan
discover export --format json|html|markdown
discover push --target dochub|gitea [--only-failed] [--dry-run]
discover mcp install --target claude
```

---

**See also**:
- `docs/architecture/components.md` — Module breakdown
- `docs/architecture/decisions.md` — Design rationale
- `docs/guides/pipeline/phase-breakdown.md` — Detailed phase information
