# AI-Discovery

Brownfield codebase analysis engine. Parse code with Tree-sitter AST, infer business processes, 
and generate SDLC documentation using a multi-tier LLM pipeline.

## Quick Start (5 minutes)

### Installation

**Option A: Docker** (recommended — no local setup required)

```bash
git clone <repo>
cd ai-discovery
cp .env.sample .env
# Edit .env with your AWS credentials
docker compose build
docker compose run --rm discovery init
docker compose run --rm -v /path/to/repo:/repo discovery -C /repo scan
```

**Option B: Local** (Python 3.10+)

```bash
git clone <repo>
cd ai-discovery
pip install -e .
cp .env.sample .env
# Edit .env with your AWS credentials
discover init                                          # Generate discovery.yaml template
discover -C /path/to/repo scan
```

### Generate Config Template

When first installing, generate a `discovery.yaml` config template:

```bash
# Docker
docker compose run --rm discovery init

# Or local
discover init
```

This creates `discovery.yaml` with:
- LLM provider selection (bedrock, ollama, mlx-gemma, mlx-qwen, openai, gemini, anthropic)
- Model role defaults (`fast`, `standard`, `deep`) and `quality: draft|final`
- RAG chunk settings
- Optional process mining (Phase 16) config
- Optional advisor tool (beta) config

**Customize as needed, then:**

```bash
discover -C repo scan
```

### Your First Scan

```bash
# With config
git clone https://github.com/django/django && discover -C django scan

# Or with defaults (no config needed)
git clone https://github.com/django/django && discover -C django scan
```

Output: `<repo>/.discovery/discovery.db` (graph + wiki pages) — browse with `discover serve`

## Supported Languages

Two parser tiers: **full tree-sitter AST** (classes, calls, endpoints, entities) and a lightweight **import-map tier** (regex import/symbol extraction, no full AST) that turns "unsupported language" into a gradient.

**Full AST tier (tree-sitter):**

| Language | Parser | Extracts |
|----------|--------|----------|
| Python | tree-sitter-python | Classes, methods, FastAPI/Flask endpoints, call refs |
| C# / ASP.NET | tree-sitter-c-sharp | `[ApiController]` classes, `[HttpGet]` endpoints, EF Core models, DI |
| Java / Spring | tree-sitter-java | `@RestController`, `@Entity`, `@Scheduled` batch jobs, `@Autowired` DI |
| JS / TS / Node | tree-sitter-javascript/typescript | Express routes, React components, classes, arrow functions |
| JSP | `parsers/jsp.py` | `.jsp`/`.jspx`/`.tag`/`.tagx` pages → `ui_component` nodes linked to useBean classes; also a fallback screen source (folder hierarchy = menu) |
| WebForms | `parsers/webforms.py` | `.aspx`/`.ascx` pages → `ui_component` nodes linked to code-behind handlers; also a fallback screen source |

**Import-map tier (regex, lightweight):**

| Languages | Parser | Extracts |
|-----------|--------|----------|
| Go / Rust / Ruby / PHP | `parsers/import_map.py` | Imports with local bindings, top-level symbols (functions/types/methods) with qualified names + line spans, conservative receiver call sites. No endpoint/entity extraction — an on-ramp, not a full AST parser. |

## Architecture (30 seconds)

```
Code → Parse (Tree-sitter) → Build call graph → Classify domains 
→ Chunk intelligently → Embed for RAG → 3-tier LLM → Generate docs
```

### Pipeline Phases (2–19)

| Phase | Name                        | What it does                                               |
|-------|-----------------------------|------------------------------------------------------------|
| 2     | `screen_llm_specs`          | Screen-centric spec generation (detected menus/screens)    |
| 5     | `lang_detect`               | Detect languages (extensions + manifests)                  |
| 6     | `parse`                     | Tree-sitter AST extraction + raw-SQL entity mining         |
| 7     | `domain_classify`           | Namespace/path heuristics + entity-kind classification     |
| 8     | `execution_slices` *(opt.)* | BFS from entry points                                      |
| 9     | `chunk`                     | Method-level splits for RAG                                |
| 10    | `rag_embed`                 | Embeddings → sqlite-vec                                    |
| 11    | `tier1_summarize`           | Haiku: per-chunk summaries                                 |
| 12    | `tier2_flow_analysis`       | Sonnet: per-domain business flows                          |
| 13    | `scenario_flow_inference`   | Scenario + cross-entity transitions                        |
| 15    | `visual_artifacts`          | Scenario Mermaid (sequence, flowchart) + IPO; no BPMN (ADR-0007) |
| 16    | `process_mining` *(opt.)*   | Per-scenario flow statistics (edge frequencies)            |
| 17    | `self_review`               | `quality: final`: verify screen specs against source        |
| 19    | `finalise`                  | Totals and scan status; then the post-scan hook builds the wiki pages (+ page prose with `--prose`) |

Phases 8 and 16 are opt-in. Phases 14 (`tier3_doc_rollup`) and 18 (`render_markdown`) were removed with the legacy SDLC tree (M3.7.b): documents are wiki pages now. All phase numbers are integers — there are no decimal sub-phases. Phase numbers were re-issued (2026-05-07) so optional / inserted stages get their own integer slot instead of decimal half-steps; CLI flags accept the phase name (`--skip-phases=process_mining`) as a stable alternative.

### Why 3 Model Roles?

| Role         | Default model (Claude providers) | Purpose |
|--------------|----------------------------------|---------|
| **fast**     | Haiku 5.5                        | Tier-1 per-chunk summaries (concurrent), self-review |
| **standard** | Sonnet 5.5                       | Per-domain flow analysis, screens, onboarding — and documents when `quality: draft` |
| **deep**     | Opus 5.5                         | Documents when `quality: final` (also enables source verification of flows/screens) |

`quality: draft` (default) keeps document generation on the standard model; `quality: final` (or `discover scan --quality final`) switches it to the deep model. Set models per provider section (`bedrock.fast|standard|deep`, …) or for the active provider with top-level `models: {fast, standard, deep}`. (These replace the old `tier1/tier2/tier3d/tier3p` keys and `--prod`; old keys are rejected with the replacement named.)

Tier 1 groups related chunks via **Louvain semantic batching** (`ai/semantic_batching.py`) so each summarization call sees a coherent code community instead of arbitrary slices. Call resolution reuses those communities to **narrow candidates** to the caller's community before fanning out globally (`graph/call_graph.py`), reducing same-name false matches.

> ⚠ **Bedrock: use model ids your account can invoke.** The Bedrock defaults are Global cross-region inference profiles: `global.anthropic.claude-haiku-5-5` (from AWS's Haiku 5.5 launch post) and `global.anthropic.claude-sonnet-5-5` / `global.anthropic.claude-opus-5-5` (same pattern, not yet confirmed). If a model id isn't enabled in your account/region, **every call on that role fails** with `ValidationException: The provided model identifier is invalid` while the scan still completes. Check with `discover doctor --llm`, and set `bedrock.fast|standard|deep` (or `models.*`) to ids you have access to.

**Typical scan (2000 classes):** $5–15 end-to-end on Bedrock, depending on codebase complexity and tier3 selection.

## Production-Grade Discovery Workflow

Real-world codebases need more than a one-shot scan. Recommended workflow for production runs:

```
┌─────────────────────┐    ┌───────────────────────┐    ┌────────────────────────┐
│ 1. Batch pipeline   │ →  │ 2. /discover-triage*  │ →  │ 3. /discover-consistency*│
│    discover scan    │    │    (low-conf rows)    │    │    (cross-doc + gaps)  │
└─────────────────────┘    └───────────────────────┘    └────────────────────────┘
   deterministic            reactive, bounded             reactive, bounded
   tables + LLM prose       agent verifies vs source       agent checks links
   per-row confidence       emits triage-report.md         emits consistency-report.md
```

\* Steps 2 and 3 are **planned skills, not in the repo yet** (roadmap item M5.4). Until then, triage by hand with `sqlite3 .discovery/discovery.db` and the source.

### Step 1 — Batch pipeline (deterministic)

Produces verified-fact tables (endpoints, entities, schema) directly from parser output, with LLM prose synthesis around them. Each row carries per-claim confidence and a source citation (`file:line`).

```bash
discover -C /path/to/repo scan
```

Everything lands in `<repo>/.discovery/` (`discovery.db` holds the graph and the wiki pages). Browse it with
`discover serve`, export it with `discover export --format markdown|html|json`, deliver it with `discover push`.

Each row in API and schema tables links back to source via `file:line`, so trust signals are at the row level, not just the doc level.

### Step 2 — Triage low-confidence rows (`/discover-triage`)

After the scan, run the triage skill on rows the pipeline flagged as low-confidence. The skill:

1. Prompts you for the source repo path and discovery output path.
2. Pulls flagged rows via `sqlite3 .discovery/discovery.db` (rows with `confidence < 0.65`).
3. For each flagged row, reads source at the cited `file:line`, verifies the claim, and proposes accept / correct / human-review.
4. Emits `triage-report.md`. You confirm before any artifact is updated.

Cost-bounded: an agent runs only over flagged rows (typically ~5–15% of all rows), not over the full corpus. CI runs stay cheap; deep review happens on demand.

### Step 3 — Cross-artifact consistency check (`/discover-consistency`)

Once individual rows are triaged, run the consistency skill across the full output. The skill:

1. Reads the page link graph (`page_links`) across overview, domain, flow, entity, table, screen and endpoint pages.
2. Verifies links resolve, every endpoint is reached by a flow, every table is touched by something, entities have transitions, etc.
3. For domain-tagged repos (e.g., `tags=[ecommerce]`), flags missing flows expected for that domain (logout, password-reset, refund, payment-webhook) — and **distinguishes code gaps from doc gaps**: if there's no source evidence, it's a code gap to track, not a discovery failure.
4. Emits `consistency-report.md`. Read-only; no in-place edits.

### When the batch pipeline alone is sufficient

If your repo is **single-language, framework-conventional, no metaprogramming** (e.g., a typical Spring Boot REST app), the batch pipeline alone reaches ~95% accuracy. Steps 2 and 3 are **optional polish**, not required steps.

Use steps 2 and 3 when:
- Multi-language repos (LLM has more room to misinfer cross-language calls)
- Repos with reflection / annotations / DSLs the parser doesn't fully model
- Multi-repo federation runs where cross-service consistency matters
- Output destined for stakeholder reviews where reader trust must be high

For details, see [`docs/assessments/03-discovery-output-quality-assessment.md`](docs/assessments/03-discovery-output-quality-assessment.md).

## Configuration

### Environment Variables

Copy `.env.sample` → `.env` and set your credentials:

```bash
# Local ai-gateway (default provider)
DISCOVERY_LLM_PROVIDER=aigw
AIGW_BASE_URL=http://localhost:11433/v1
AIGW_API_KEY=...

# Or AWS Bedrock
DISCOVERY_LLM_PROVIDER=bedrock
AWS_REGION_NAME=us-west-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Or local Ollama
DISCOVERY_LLM_PROVIDER=ollama
OLLAMA_URL=http://localhost:11434
```

None of these are needed for a keyless (structure-only) scan: `discover init --yes --no-prose && discover scan`.

The spend limit is a config setting, not an environment variable: `budget_limit_usd` in `discovery.yaml` / `.discovery/config.yaml` (default 50.0).

**All options:** See [`.env.sample`](.env.sample)

### YAML Configuration (Optional)

For fine-grained control, create a config file:

```yaml
provider: bedrock
budget_limit_usd: 50.0

bedrock:
  region: us-east-1
  fast: global.anthropic.claude-haiku-5-5        # chunk summaries, self-review
  standard: global.anthropic.claude-sonnet-5-5   # flows, screens; documents when quality: draft
  deep: global.anthropic.claude-opus-5-5         # documents when quality: final — must be enabled in your account
quality: draft                                   # draft | final

output_directory: ./data/discovery-output
```

Then run `discover scan` in the repository (it reads `.discovery/config.yaml`).

## CLI Reference

<!-- cli-reference:start — generated by scripts/gen_cli_reference.py; do not edit by hand -->

Every command takes the global options before or after its name; `--json` prints one JSON document (errors included) and exit codes follow `docs/roadmap/design/cli-v1.md` §4.

**Global options**

| Option | Description |
|---|---|
| `-C`, `--repo` | Run as if started in PATH. |
| `--state-dir` | Use DIR instead of <repo>/.discovery/. |
| `--json` | Emit the result as one JSON document on stdout. |
| `--progress` | Progress output: rich \| json (NDJSON) \| none. (default: `rich`) |
| `-v`, `--verbose` | More logging (-vv for debug). |
| `-q`, `--quiet` | Only errors. |
| `--no-color` | Disable colour (also NO_COLOR). |
| `--version` | Print the version and exit. |

### `discover init`

Detect the stack and write .discovery/config.yaml (first-run menu on a terminal).

| Option | Description |
|---|---|
| `PATH` | Repository to set up (default: -C / current dir). |
| `--yes`, `-y` | Accept defaults (structure only unless --prose). |
| `--prose`, `--no-prose` | Spend mode: add LLM prose, or structure only. |
| `--provider` | LLM provider (aigw \| omlx \| openrouter \| bedrock \| openai \| gemini \| anthropic \| ollama \| mlx-gemma \| mlx-qwen). (default: `aigw`) |
| `--mcp`, `--no-mcp` | Add an MCP server entry to .mcp.json. |
| `--force` | Overwrite an existing .discovery/config.yaml. |
| `--git-hook`, `--no-git-hook` | Install (or remove) the post-commit hook that re-indexes in the background after each commit. |

### `discover scan`

Scan this repository into <repo>/.discovery/ (structure only unless --prose).

| Option | Description |
|---|---|
| `--full` | Ignore caches and rescan even an unchanged commit. |
| `--prose`, `--no-prose` | Enable LLM stages (default: config 'prose'). |
| `--only` | Run only these stages (comma-separated). |
| `--resume` | Continue the interrupted run for this commit. |
| `--yes`, `-y` | Pre-approve the cost gate. |
| `--dry-run` | Print the prose cost estimate per stage; write nothing, take no lock. |
| `--budget` | Budget limit in USD (overrides config). |
| `--quality` | draft \| final (final = deeper model for docs). |
| `--branch`, `-b` | Branch label for git repos. (default: `main`) |
| `--watch` | Keep running: rescan incrementally whenever files change (Ctrl-C stops). |
| `--debounce` | --watch: seconds without further changes before a rescan. (default: `2.0`) |

### `discover status`

Show what is indexed for this repo, how fresh it is, and what it cost.

| Option | Description |
|---|---|
| `--agent` | Also summarise MCP tool use from the local call ledger. |

### `discover doctor`

Check the environment and this repo's index; --repair fixes what it safely can.

| Option | Description |
|---|---|
| `--repair` | Fix what is safe to fix (locks, migrations). |
| `--yes`, `-y` | With --repair: also remove another host's lock. |
| `--llm` | Also probe the LLM provider (network; replaces test-llm). |

### `discover serve`

Serve the project wiki (API v1 + web UI) on one local port.

| Option | Description |
|---|---|
| `--host` | Address to bind (non-loopback needs --allow-remote). (default: `127.0.0.1`) |
| `--port` | Port for API + UI (0 = any free). (default: `7341`) |
| `--open`, `--no-open` | Open the wiki in a browser. |
| `--allow-remote` | Allow binding a non-loopback address and remote clients. |
| `--token` | Bearer token for note writes with --allow-remote (default: random). |

### `discover export`

Export the current scan's wiki and knowledge graph (no LLM, no network).

| Option | Description |
|---|---|
| `--format`, `-f` | markdown \| html \| json |
| `--out`, `-o` | Output directory (default <state>/exports/<format>). |

### `discover drift`

Report pages whose source files changed since the scan (exit 6 on drift; CI-friendly).

| Option | Description |
|---|---|
| `--type` | all \| screen — which pages to check. (default: `all`) |
| `--limit` | Drifted pages listed at most (all are counted). (default: `50`) |

### `discover push`

Deliver the wiki (markdown export, one document per page) to DocHub or Gitea.

| Option | Description |
|---|---|
| `--target` | dochub \| gitea |
| `--only-failed` | Retry only pages whose last delivery failed. |
| `--dry-run` | List what would be sent; send nothing. |
| `--batch-size` | Pages per DocHub request. |

### `discover ask`

Search the wiki and the code graph; every hit cites its page and source lines.

| Option | Description |
|---|---|
| `QUESTION` | What to look for, in words or identifiers. |
| `--kinds` | Page types and/or 'symbol', comma-separated. |
| `--limit` | Hits to show. (default: `10`) |

### `discover impact`

Rank what may break if TARGET changes, with the paths and the unknowns.

| Option | Description |
|---|---|
| `TARGET` | symbol, file:PATH, table:NAME, screen:ID, entity:NAME, endpoint:… |
| `--depth` | How many call levels to walk. (default: `3`) |
| `--include` | Extra edge kinds, comma-separated. |

### `discover mcp`

MCP server for coding agents (Claude Code, Codex, Cursor, VS Code, …).


### `discover mcp serve`

Serve MCP tools over stdio (launched by the agent), or over HTTP with --http.

| Option | Description |
|---|---|
| `--tools` | Tool selection: a profile (default \| lean \| all), tool names (allowlist), and +name / -name edits, comma-separated. Also $DISCOVER_MCP_TOOLS. |
| `--max-tokens` | Per-response token cap (default: each tool's own budget). |
| `--http` | Serve streamable HTTP instead of stdio. |
| `--host` | HTTP bind address (non-loopback needs a token). (default: `127.0.0.1`) |
| `--port` | HTTP port. (default: `7342`) |
| `--token` | Bearer token (≥ 16 chars); required off loopback. |
| `--allow-host` | Accepted Host header (repeatable), e.g. mcp.internal:7342. |

### `discover mcp install`

Wire the ai-discovery server into one or more MCP clients (other servers are kept).

| Option | Description |
|---|---|
| `--target`, `-t` | Client(s): claude, codex, cursor, vscode, comma-separated, or all. (default: `claude`) |
| `--scope` | project (portable file in the repo, safe to commit) or user (your home config, absolute repo path). (default: `project`) |
| `--tools` | Tool selection: a profile (default \| lean \| all), tool names (allowlist), and +name / -name edits, comma-separated. Also $DISCOVER_MCP_TOOLS. |
| `--command` | Executable the client runs (default: `discover` if on PATH, else the absolute path of this executable). |
| `--force` | Write even if another discovery server is configured. |
| `--instructions` | Also write the generated agent instructions block into AGENTS.md (agents), CLAUDE.md (claude), both, or none (default). |

### `discover mcp uninstall`

Remove exactly the entries `install` wrote.

| Option | Description |
|---|---|
| `--target`, `-t` | Client(s): claude, codex, cursor, vscode, comma-separated, or all. (default: `claude`) |
| `--scope` | project (portable file in the repo, safe to commit) or user (your home config, absolute repo path). (default: `project`) |
| `--instructions` | Also remove the instructions block from AGENTS.md (agents), CLAUDE.md (claude) or both. |

### `discover mcp print-config`

Print what `install` would write (per client), the tools exposed and their schema-token cost.

| Option | Description |
|---|---|
| `--target`, `-t` | Client(s): claude, codex, cursor, vscode, comma-separated, or all. |
| `--scope` | project (portable file in the repo, safe to commit) or user (your home config, absolute repo path). (default: `project`) |
| `--tools` | Tool selection: a profile (default \| lean \| all), tool names (allowlist), and +name / -name edits, comma-separated. Also $DISCOVER_MCP_TOOLS. |
| `--command` | Executable the client runs. |
| `--instructions` | Also print the generated agent instructions block. |

<!-- cli-reference:end -->

The pre-v1 commands were removed in M2.7: `chat` → `ask`, `ingest` → `push`, `impact ENTITY` → `impact entity:NAME`,
`export-graph` → `export --format json`, `verify-drift` → `drift`, `detect-screens` → `scan --only screens`,
`test-llm` → `doctor --llm`, `view` → `serve`; `query` (open `.discovery/discovery.db` with `sqlite3`), `federate`
and `ingest-docs` are gone.

## Screen-Centric Documentation (v0.3+)

**New in v0.3**: Generate user-facing documentation organized by application screens instead of business domains.

### What is Screen-Centric Mode?

Traditional discovery generates domain-centric docs ("Customer Domain", "Order Domain"). Screen-centric mode generates docs organized by **screens users see** (menus), providing a complete end-to-end view from UI click → backend logic → database → external systems.

**Why?** Users navigate apps by menu, not by domain. A single screen often spans multiple domains. One screen spec answers: "What can I do here?" and "What happens when I submit?"

### How It Works

#### 1. Auto-Detect Menus

Discovers menu structure from:
- **JSON/YAML menu files** (`menu.json`, `navigation.yaml`) — fully supported

```bash
discover scan --only screens
```

> ⚠ **Scope (current state).** Today, only **static JSON/YAML menu files** produce
> screens, and only **leaf menu entries** become screens. Detectors for
> framework routing (Vue Router, React Router, Angular) and TypeScript
> `export const MENU` constants are scaffolded but **not yet functional**
> (they return no screens — see `menu_detector.py`). Apps with **no static menu
> file** — server-rendered or permission-built menus, and screens reached only
> via popups, wizards, modals, or deep links — are **not detected**; screen
> generation is silently skipped for them. These are tracked as roadmap items.

**Output**: `screen_map.yaml` with detected screens and `menu_tree.json` with hierarchy.

#### 2. Link to Backend

For each screen, automatically maps:
- Frontend API calls (from Vue/React components)
- Backend controllers/services (Java Spring, ASP.NET, Express)
- Database tables accessed
- Batch jobs triggered (ETL, scheduled tasks)
- External system integrations (REST APIs, message brokers, FTP imports) — see the lineage caveat below
- Data injection points (orphaned tables, views)

```yaml
screens:
  - screen_id: customer-search
    menu_path: [Customers, Search]
    fe_component: src/pages/Customer/SearchPage.vue
    fe_api_calls:
      - GET /api/customers/search
    be_controllers:
      - CustomerController
    be_services:
      - CustomerService
    db_tables:
      - CUSTOMER
      - CUSTOMER_ADDRESS
    batch_jobs:
      - CustomerExportJob
    external_interfaces:
      - REST: https://crm.partner.com/sync
    data_injection_points:
      - CUSTOMER_AUDIT (orphaned table - external injection)
```

#### 3. Generate Screen Specs

Creates one markdown spec per screen with:
- **Purpose** — What the screen does
- **When Used** — Who sees it, workflow context
- **User Actions & System Responses** — Interaction table
- **Important Rules** — Business rules, validations
- **Data & Fields** — Fields displayed, data sources
- **Downstream Effects** — What happens (screens, batches, external syncs)
- **Permissions & Roles** — Access control
- **Technical Reference** — Collapsed details linking to backend specs

Output structure:
```
docs/
├── screens/
│   ├── customer-search.md
│   ├── customer-edit.md
│   └── customer-detail.md
├── screen-index.md              # Menu tree with links
└── INTERFACES/                  # External dependency catalog
```

Table, batch-job and backend-service pages are not written as files here; the v1 wiki (`discover serve`) has table, endpoint and entity pages.

#### 4. Detect Drift

Specs include source file hashes in frontmatter. Detect stale specs:

```bash
discover drift --type screen
```

**Output**:
```
Drifted screens: 3 of 20

  ⚠️  customer-search
      src/pages/Customer/SearchPage.vue changed
      src/main/java/.../CustomerController.java changed

  ⚠️  customer-edit
      migration_001_customer.sql changed

  ✓  customer-detail (no drift)
```

### Multi-Framework Support

Framework detection (`framework_detector.py`) recognizes:
- **Java/Spring** — @RestController, @Service, @Entity, Spring Batch, @Scheduled
- **.NET/ASP.NET** — [ApiController], [Service], DbSet<>, BackgroundService
- **Node.js/Express** — Express routes, Mongoose models, node-schedule

> ⚠ **Mapping depth (current state).** The deep screen→backend mapping
> (controller → `@Autowired` service → `@Table` entity, plus ETL/interface/
> injection-point detection) is implemented for **Java/Spring only** — it globs
> `*.java` and matches Spring annotations regardless of the detected framework.
> .NET and Node screens get framework *detection* but **shallow mapping**.
> Backend mapping uses filename/regex heuristics (not call-graph data flow), and
> HTTP methods are inferred from nearby text. Full .NET/Node parity is a roadmap
> item.

### Data Lineage Visualization

Complete data flow from external sources to screens:

```
External System (EAI/ETL)
    ↓ (RestTemplate, WebClient, FTP, Kafka)
ETL Batch Job (DataSyncJob)
    ↓ (writes)
Orphaned Table (PARTNER_ORDERS)
    ↓ (read by service)
Backend Service (PartnerService)
    ↓ (API endpoint)
REST Controller (PartnerController)
    ↓ (called by)
Frontend Screen (Partner Search)
```

> ⚠ **Lineage caveat (current state).** This view is assembled from outbound
> boundary detection + table read/write heuristics, not full event tracing:
> - **Message brokers (Kafka/MQ/SQS/JMS)** are detected as **outbound producer
>   boundaries only** (e.g. `KafkaTemplate.send`). There is **no
>   producer↔consumer (topic) correlation** and no consumer-side call-graph
>   node — `@KafkaListener`/`@RabbitListener` are surfaced only as string
>   labels in screen specs, not as lineage edges.
> - **SQL analysis is inline DML/DDL only** (CREATE TABLE / INSERT / UPDATE /
>   SELECT / ALTER / CREATE VIEW). **Stored procedures, PL/SQL packages, and
>   triggers are not parsed** — business logic inside the database is not yet
>   captured (roadmap).
> - **Cross-service HTTP correlation** works only for **string-literal URLs**;
>   URLs built at runtime (`` `${baseURL}/${path}` ``) are not matched.

### Example Command Sequence

```bash
# 1. Detect screens from menu
discover scan --only screens

# 2. Map screens to backend + external systems
# (Integrated into standard scan, runs automatically)
discover -C ./my-app scan

# 3. Check if screens are out of sync with source
discover drift --type screen

# 4. Regenerate only drifted screens (cost-saving)
discover -C ./my-app scan --resume
```

### Integration with Existing Docs

Screen specs do **not replace** domain docs. They **link to** them:
- Each screen links to its table pages
- Each screen links to its backend (endpoint and symbol pages)
- Each screen links to batch job docs
- Each screen links to external interface specs

Users can navigate:
- **By menu** → start at screen spec
- **By domain** → start at domain-centric docs
- **By component** → start at backend/batch/interface specs

### Performance & Cost

- Menu detection: < 100ms
- Screen mapping: ~50ms per screen (parallel)
- Spec generation: ~3s per screen (LLM latency dominant)
- **Drift detection**: < 1s (no LLM calls needed)

**Cost optimization**: Only regenerate drifted screens on subsequent runs → 60–80% savings.

## Installation Methods

### Docker Compose (Recommended)

```bash
# Build image
docker compose build

# Scan a repo
docker compose run --rm -v /path/to/repo:/repo discovery -C /repo scan

# Mount a local repo
docker compose run --rm -v /path/to/repo:/repo discovery -C /repo scan

# Query results
docker compose run --rm discovery query "SELECT * FROM business_flows"
```

### Local Development

```bash
# Clone and install
git clone <repo>
cd ai-discovery
pip install -e .

# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run CLI
discover -C /path/to/repo scan
```

### Local Development

Install in editable mode (`pip install -e ".[dev]"`) and run `discover` / `pytest` from the venv. The old `run-local.sh` helper is archived (`docs/archived/root/`).

### PyPI (Coming Soon)

```bash
pip install ai-discovery
discover -C /path/to/repo scan
```

## Examples

See [`examples/`](examples/) for:
- Scanning local repositories
- Scanning GitHub repositories
- Docker Compose usage
- Configuration templates
- Programmatic API usage

Quick start:

```bash
bash examples/03_docker_scan.sh https://github.com/pallets/flask flask
python examples/01_local_scan.py
```

## Output

```
<repo>/.discovery/
├── config.yaml                      # project config (commit it)
├── discovery.db                     # SQLite: code nodes, edges, pages, flows, costs, summaries, embeddings
├── cache/                           # parse cache, LLM response cache
├── exports/{json,html,markdown}/    # `discover export` output
└── logs/                            # e.g. hook.log from the post-commit hook
```

The legacy ASIS/ASD/ASSC/PF/BPMN/DMN/EARS/ONBOARD markdown tree was removed in M3.7.b — every one of those
views is a wiki page now (domain, flow, onboarding, entity with FSM + DMN + EARS, table, interface, …).

For tooling outside the wiki, `discover export --format json` writes the portable knowledge graph (`ai-discovery/knowledge-graph` v2); `.discovery/discovery.db` is plain SQLite.

Each markdown file includes **ai-docs frontmatter** for easy ingestion:

```yaml
---
doc_id: myproject-orders-as-is
title: "Orders — As-Is Current State"
status: Draft
tags: [discovery-scan]
source: discovery_scan
scan_date: 2026-04-17T...
discovery_confidence: 0.82
unverified_claims: 3
---
```

## Documentation

- **Start here**: [`docs/INDEX.md`](docs/INDEX.md) — Navigation guide
- **Architecture**: [`docs/architecture/overview.md`](docs/architecture/overview.md) — System design
- **Call Graph Resolution**: [`docs/guides/call-graph/`](docs/guides/call-graph/) — Debugging & confidence scoring
- **Adding Language Support**: [`docs/guides/parsers/extension-checklist.md`](docs/guides/parsers/extension-checklist.md) — Add Go, Rust, etc.
- **Performance & Cost**: [`docs/guides/pipeline/profiling.md`](docs/guides/pipeline/profiling.md) — Optimization strategies
- **Development**: [`CLAUDE.md`](CLAUDE.md) — Project principles & workflows

## Development

### Local Setup

```bash
git clone <repo>
cd ai-discovery
pip install -e ".[dev]"
pytest
```

### Running Tests

```bash
pytest                              # All tests
pytest tests/test_call_graph.py     # Specific test file
pytest -v                           # Verbose output
pytest --cov                        # Coverage report
```

### Contributing

1. Read [`CLAUDE.md`](CLAUDE.md) for development principles
2. Check [`docs/INDEX.md`](docs/INDEX.md) for architecture overview
3. Follow [`docs/guides/`](docs/guides/) for your area of work
4. For roadmap work, start at [`docs/roadmap/README.md`](docs/roadmap/README.md) and use the `/roadmap-next` skill (the only skill in the repo today). The other skills CLAUDE.md describes (`/parser-extension`, `/call-graph-debug`, `/pipeline-analyze`, `/discover-triage`, `/discover-consistency`) are not in the repo yet; triage/consistency are scheduled in M5.4.

## Package Structure

```
ai-discovery/
├── src/ai_discovery/               # Main package
│   ├── cli.py                      # Typer CLI entry point
│   ├── pipeline.py                 # Pipeline orchestrator (phases 2–19)
│   ├── config.py                   # Loader for discovery.yaml + env overrides
│   ├── db.py                       # SQLite schema + helpers
│   ├── ai/                         # LLM ops (chunker, summarizer, advisor, process_miner)
│   ├── extractors/                 # Raw-SQL entity extraction (Phase 2e)
│   ├── graph/                      # Call graph, domain/entity classifier, FSM, impact, federation
│   ├── generators/                 # Mermaid flow diagrams, DMN / EARS, screen-spec templates
│   │   └── templates/              # Jinja2 templates for as-is, spec, interface, data-model
│   ├── ingest/                     # Code ingestion + markdown classifier/runner
│   ├── parsers/                    # Tree-sitter parsing (Python, C#, Java, JS/TS, JSP, WebForms) + import-map tier (Go/Rust/Ruby/PHP)
│   ├── rag/                        # Embeddings, retrieval, chat REPL
│   ├── repo/                       # Git operations
│   ├── shared/                     # LLM routing, invoke, model defaults
│   └── tests/                      # Unit tests
├── docs/                           # Architecture & guides
├── examples/                       # Usage examples
└── pyproject.toml                  # Package configuration
```

> Note: the former `output/` package was renamed to `generators/` (commit `76eabfd`) — update any external imports from `ai_discovery.output.*` to `ai_discovery.generators.*`.

## License

AGPL-3.0 (GNU Affero General Public License v3.0) — see [`LICENSE`](LICENSE).

---

## Getting Help

- **First time?** Start with [Quick Start](#quick-start), then see [`examples/`](examples/)
- **Questions?** Check [`docs/INDEX.md`](docs/INDEX.md) for topic-based navigation
- **Want to contribute?** See [`CLAUDE.md`](CLAUDE.md) for development workflow
- **Found a bug?** Check existing issues or create a new one
