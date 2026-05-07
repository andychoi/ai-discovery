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
docker compose run --rm discovery scan https://github.com/org/repo -p myproject
```

**Option B: Local** (Python 3.10+)

```bash
git clone <repo>
cd ai-discovery
pip install -e .
cp .env.sample .env
# Edit .env with your AWS credentials
discover init                                          # Generate discovery.yaml template
discover scan /path/to/repo -p myproject --config discovery.yaml
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
- LLM provider selection (bedrock, ollama, mlx-gemma, mlx-qwen)
- Model tier defaults (tier1, tier2, tier3d/tier3p)
- RAG chunk settings
- Optional process mining (Phase 16) config
- Optional advisor tool (beta) config

**Customize as needed, then:**

```bash
discover scan repo --project-slug=myapp --config discovery.yaml
```

### Your First Scan

```bash
# With config
discover scan https://github.com/django/django -p django --config discovery.yaml

# Or with defaults (no config needed)
discover scan https://github.com/django/django -p django
```

Output: `./data/discovery-output/discovery.db` + markdown docs

## Supported Languages

| Language | Parser | Extracts |
|----------|--------|----------|
| Python | tree-sitter-python | Classes, methods, FastAPI/Flask endpoints, call refs |
| C# / ASP.NET | tree-sitter-c-sharp | `[ApiController]` classes, `[HttpGet]` endpoints, EF Core models, DI |
| Java / Spring | tree-sitter-java | `@RestController`, `@Entity`, `@Scheduled` batch jobs, `@Autowired` DI |
| JS / TS / Node | tree-sitter-javascript/typescript | Express routes, React components, classes, arrow functions |

## Architecture (30 seconds)

```
Code → Parse (Tree-sitter) → Build call graph → Classify domains 
→ Chunk intelligently → Embed for RAG → 3-tier LLM → Generate docs
```

### Pipeline Phases (5–19)

| Phase | Name                        | What it does                                               |
|-------|-----------------------------|------------------------------------------------------------|
| 5     | `lang_detect`               | Detect languages (extensions + manifests)                  |
| 6     | `parse`                     | Tree-sitter AST extraction + raw-SQL entity mining         |
| 7     | `domain_classify`           | Namespace/path heuristics + entity-kind classification     |
| 8     | `execution_slices` *(opt.)* | BFS from entry points                                      |
| 9     | `chunk`                     | Method-level splits for RAG                                |
| 10    | `rag_embed`                 | Embeddings → sqlite-vec                                    |
| 11    | `tier1_summarize`           | Haiku: per-chunk summaries                                 |
| 12    | `tier2_flow_analysis`       | Sonnet: per-domain business flows                          |
| 13    | `scenario_flow_inference`   | Scenario + cross-entity transitions                        |
| 14    | `tier3_doc_rollup`          | Sonnet/Opus: final docs                                    |
| 15    | `visual_artifacts`          | BPMN + DMN + EARS + entity-backbone Mermaid generation     |
| 16    | `process_mining` *(opt.)*   | Inductive miner + conformance (via `pm4py`)                |
| 17    | `self_review`               | Verify claims against source via RAG                       |
| 18    | `render_markdown`           | Render with ai-docs frontmatter                            |
| 19    | `finalise`                  | Write artifacts; optional push to DocHub/Gitea             |

Phases 8 and 16 are opt-in. All phase numbers are integers — there are no decimal sub-phases. Phase numbers were re-issued (2026-05-07) so optional / inserted stages get their own integer slot instead of decimal half-steps; CLI flags accept the phase name (`--skip-phases=process_mining`) as a stable alternative.

### Why 3 LLM Tiers?

| Tier       | Default model            | Purpose                              |
|------------|--------------------------|--------------------------------------|
| **Tier 1** | Haiku                    | Fast per-chunk summaries (concurrent)|
| **Tier 2** | Sonnet                   | Per-domain flow analysis             |
| **Tier 3** | Haiku *(dev)* / Sonnet *(prod)* | Final doc rollup; Opus is opt-in via config |

Tier 3 has two slots — `tier3d` (dev-default, fast/cheap) and `tier3p` (prod-default, deeper). Pass `--prod` at scan time to switch in `tier3p`. Override any slot via `discovery.yaml` to pin Opus where you want it.

**Typical scan (2000 classes):** $5–15 end-to-end on Bedrock, depending on codebase complexity and tier3 selection.

## Configuration

### Environment Variables

Copy `.env.sample` → `.env` and set your credentials:

```bash
# AWS Bedrock (primary)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
DISCOVERY_LLM_PROVIDER=bedrock

# Or use local Ollama
DISCOVERY_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434

# Budget limit
DISCOVERY_BUDGET_LIMIT_USD=50.0
```

**All options:** See [`.env.sample`](.env.sample)

### YAML Configuration (Optional)

For fine-grained control, create a config file:

```yaml
provider: bedrock
budget_limit_usd: 50.0

bedrock:
  region: us-east-1
  tier1: us.anthropic.claude-haiku-4-5-20251001-v1:0   # cheap: chunk summaries
  tier2: us.anthropic.claude-sonnet-4-6                # mid: flow analysis
  tier3d: us.anthropic.claude-haiku-4-5-20251001-v1:0  # dev doc generation (fast/cheap)
  tier3p: us.anthropic.claude-sonnet-4-6               # prod doc generation (--prod)

output_directory: ./data/discovery-output
```

Then use: `discover scan <repo> -p <project> -c config.yaml`

## CLI Reference

### Commands

#### `discover init` — Generate Config Template

Create a `discovery.yaml` template with all configurable options:

```bash
discover init [--config discovery.yaml] [--provider ollama]
```

**Options:**
- `--config` — Output path for config file (default: `discovery.yaml`)
- `--provider` — Default provider: `bedrock`, `ollama`, `mlx-gemma`, `mlx-qwen`

**Output:** Generates `discovery.yaml` with comments explaining each setting.

#### `discover scan` — Scan Codebase

```bash
discover scan REPO -p PROJECT_SLUG [OPTIONS]
```

**Required Arguments:**

| Argument | Description |
|----------|-------------|
| `REPO` | Path or URL (file path or `https://github.com/org/repo.git`) |
| `-p, --project-slug` | Project slug for outputs (e.g., `myproject`) |

**Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `-b, --branch` | Git branch to analyze | `main` |
| `-o, --output` | Output directory | `./data/discovery-output` |
| `-c, --config` | YAML config file | (uses env vars) |
| `--provider` | LLM provider: `bedrock` \| `ollama` | (from env) |
| `--budget` | Budget limit in USD | (from env) |
| `--resume` | Resume from last complete phase | (disabled) |
| `--resume-from` | Jump to specific phase (e.g., `17`, `self_review`) | (resume from last) |
| `--skip-phases` | Skip phases (comma-separated, e.g., `16,10`) | (none) |
| `--rescan` | Force full rescan, ignore cache | (disabled) |

**Resume Examples:**

```bash
# Resume from where it left off
discover scan repo -p myapp --resume

# Jump to phase 17 (self-review)
discover scan repo -p myapp --resume --resume-from=17

# Skip optional phase 16 (process mining)
discover scan repo -p myapp --skip-phases=16

# Resume and skip process mining
discover scan repo -p myapp --resume --skip-phases=16
```

**Phase Numbers:**
- 5: Language detection
- 6: Parse files
- 7: Domain classification
- 8: Execution slices (optional)
- 9: Chunk code
- 10: RAG embedding
- 11: Tier 1 summarization
- 12: Tier 2 flow analysis
- 13: Scenario flow inference
- 14: Tier 3 doc rollup
- 15: Visual artifacts (BPMN/Mermaid/DMN/EARS)
- 16: Process mining (optional)
- 17: Self-review
- 18: Render markdown
- 19: Finalise

### Monitoring & Debugging

Check phase completion status:

```bash
discover scan repo -p myapp --resume
```

Output shows phase progress:
```
Phase progress:
  ✓ Phase  5 (lang_detect)
  ✓ Phase  6 (parse)
  ✓ Phase  7 (domain_classify)
  ⊘ Phase 17 (self_review)    [interrupted]
  ⊘ Phase 18 (render_markdown)
```

### Process Mining (Optional — Phase 16)

Process mining runs an inductive miner + token-replay conformance over inferred scenarios, via [`pm4py`](https://pm4py.fit.fraunhofer.de/). Disabled by default; enable in `discovery.yaml`:

```yaml
process_mining:
  enabled: true           # Set to true to enable
  miner_variant: inductive
  fitness_threshold: 0.90
```

Then run normally — Phase 16 runs after Tier 3 doc rollup. Skip without uninstalling via `--skip-phases=16`.

> **Install note:** `pm4py` is a required dependency (it pulls in `pandas`, `numpy`, `graphviz` bindings) — `pip install -e .` will download it even if you never enable Phase 16. If install size is a concern, the miner's imports are module-level today; consider pinning pm4py out of your image until the team extracts it to an optional extra.

### `discover ingest` — Push Scan Results

Push scan results (code graph + generated docs) to a remote system:

```bash
discover ingest -p myproject --target dochub \
  --api-url https://api.example.com \
  --api-key $DISCOVERY_API_KEY
```

| Option | Description |
|--------|-------------|
| `--target` | Ingest target: `dochub` \| `gitea` |
| `--api-url` | DocHub API base URL |
| `--api-key` | API key for DocHub (or env `DISCOVERY_API_KEY`) |
| `--gitea-url` | Gitea base URL |
| `--gitea-token` | Gitea API token (or env `DISCOVERY_GITEA_TOKEN`) |

### `discover query` — Run SQL

Query the discovery database directly:

```bash
# Use discovered.db from latest scan
discover query "SELECT name, node_type, domain FROM code_nodes LIMIT 20"

# Or specify the database
sqlite3 ./data/discovery-output/myapp/discovery-myapp.db "SELECT COUNT(*) FROM code_nodes"
```

### `discover chat` — RAG Chat REPL

Ask questions against the scanned code + generated docs:

```bash
discover chat -p myproject            # uses tier1 by default
discover chat -p myproject --tier2    # deeper answers, higher cost
```

First run re-indexes generated docs into the RAG store (skip with `--no-index`).

### `discover impact` — Entity Impact Query (Phase 3)

Show every code path and cross-entity interaction that touches a given entity:

```bash
discover impact Order -p myproject
discover impact Order -p myproject --output-file impact.md
```

Reads the canonical backbone artifacts produced by `scan` (`entity_state_machines.json`, `cross_entity_transitions.json`, `entity_conditions.json`). If any are missing, re-run `scan` — Phase 3 didn't complete.

### `discover federate` — Workspace Federation (Phase 4)

Merge per-repo backbone artifacts into a federated view that spans services:

```bash
discover scan billing-svc/     -p billing     -o ./out
discover scan fulfillment-svc/ -p fulfillment -o ./out
discover federate ./out/billing ./out/fulfillment -o ./out/federated
```

FSMs are merged across repos when their normalized names + field sets exceed `--jaccard` (default `0.7`). The output directory is consumable by `discover impact` directly.

### `discover ingest-docs` — Bulk Markdown Ingestion

Ingest an existing directory of markdown analysis docs into DocHub — auto-classifies each file into one of 15 SDLC doc types (as-is, brd, design, spec, …), generates frontmatter with incrementing doc IDs, extracts cross-refs, and dedupes via TF-IDF cosine similarity:

```bash
discover ingest-docs ./docs -p myproject --dry-run          # preview
discover ingest-docs ./docs -p myproject \
  --push api --api-url http://localhost:8000 --api-key sk-xxx
```

Stale/duplicate files are ingested with `status=Deprecated` (not skipped). Subsequent runs fetch the next free doc ID to avoid collisions.

## Installation Methods

### Docker Compose (Recommended)

```bash
# Build image
docker compose build

# Scan a repo
docker compose run --rm discovery scan https://github.com/org/repo -p myproject

# Mount a local repo
docker compose run --rm -v /path/to/repo:/repo:ro discovery scan /repo -p myproject

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
discover scan /path/to/repo -p myproject
```

### Local Development (Without pip install)

If you prefer to test without installing, use the `run-local.sh` helper script:

```bash
# Run CLI without pip install
./run-local.sh discover scan /path/to/repo -p myproject
./run-local.sh discover --help

# Run tests
./run-local.sh pytest tests/
./run-local.sh pytest tests/test_call_graph.py -v

# Run Python directly
./run-local.sh python -c "from ai_discovery.cli import app; print('OK')"
```

The script automatically sets `PYTHONPATH=./src` so imports work correctly.

### PyPI (Coming Soon)

```bash
pip install ai-discovery
discover scan /path/to/repo -p myproject
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
data/discovery-output/<project-slug>/
├── discovery-<slug>.db              # SQLite: code nodes, calls, flows, costs, summaries, embeddings
├── entity_state_machines.json       # Per-entity FSMs (Phase 3 backbone)
├── cross_entity_transitions.json    # Cross-entity links (Phase 3b/3.1c)
├── entity_conditions.json           # Entity-guard correlations (Phase 3d)
├── bpmn/                            # BPMN 2.0 XML per scenario (Phase 15)
├── dmn/                             # DMN decision tables (Phase 15)
├── ears/                            # EARS-formatted requirements (Phase 15)
├── mermaid/                         # Entity backbone L1/L2 diagrams
└── docs/
    ├── as-is/                       # Current state assessments
    ├── spec/                        # Functional specifications
    ├── interface/                   # API contract documentation
    └── data-model/                  # Entity and schema documentation
```

The JSON backbone artifacts are the canonical output — `discover impact` and `discover federate` read them directly, and downstream tooling (DocHub, custom scripts) should prefer them over the SQLite DB for cross-tool portability.

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
4. Use the three custom skills:
   - `/parser-extension` — Add language support
   - `/call-graph-debug` — Debug call resolution
   - `/pipeline-analyze` — Optimize performance/cost

## Package Structure

```
ai-discovery/
├── src/ai_discovery/               # Main package
│   ├── cli.py                      # Typer CLI entry point
│   ├── pipeline.py                 # Pipeline orchestrator (phases 5–16)
│   ├── config.py                   # Loader for discovery.yaml + env overrides
│   ├── db.py                       # SQLite schema + helpers
│   ├── ai/                         # LLM ops (chunker, summarizer, advisor, process_miner)
│   ├── extractors/                 # Raw-SQL entity extraction (Phase 2e)
│   ├── graph/                      # Call graph, domain/entity classifier, FSM, impact, federation
│   ├── generators/                 # BPMN / DMN / EARS / doc generators + push (formerly output/)
│   │   └── templates/              # Jinja2 templates for as-is, spec, interface, data-model
│   ├── ingest/                     # Code ingestion + markdown classifier/runner
│   ├── parsers/                    # Language-specific parsing (Python, C#, Java, JS/TS)
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

MIT

---

## Getting Help

- **First time?** Start with [Quick Start](#quick-start), then see [`examples/`](examples/)
- **Questions?** Check [`docs/INDEX.md`](docs/INDEX.md) for topic-based navigation
- **Want to contribute?** See [`CLAUDE.md`](CLAUDE.md) for development workflow
- **Found a bug?** Check existing issues or create a new one
