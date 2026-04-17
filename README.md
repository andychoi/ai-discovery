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
docker compose run --rm discovery scan https://github.com/org/repo -p myproject
```

**Option B: Local** (Python 3.10+)

```bash
git clone <repo>
cd ai-discovery
pip install -e .
cp .env.sample .env
# Edit .env with your AWS credentials
discover scan /path/to/repo -p myproject
```

### Your First Scan

```bash
# Docker
docker compose run --rm discovery scan https://github.com/django/django -p django

# Or local
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

### 13-Phase Pipeline

1. **Resolve repo** (clone or validate local)
2. **Detect languages** (extensions + manifests)
3. **Parse files** (Tree-sitter AST extraction)
4. **Build call graph** + classify domains
5. **Smart chunk** (AST-aware, not naive char split)
6. **Embed chunks** for RAG (sqlite-vec)
7. **Tier 1 (Haiku)**: Summarize chunks — cheap, concurrent
8. **Tier 2 (Sonnet)**: Analyze business flows per domain
9. **Tier 3 (Opus)**: Generate doc rollups
10. **Render markdown** with ai-docs frontmatter
11. **Self-review**: Verify claims against source via RAG
12. **Push to DocHub API** or Gitea (optional)

### Why 3 LLM Tiers?

| Tier | Model | Purpose | Cost |
|------|-------|---------|------|
| **Tier 1** | Haiku | Fast summaries for every chunk | ~$0.80/M tokens |
| **Tier 2** | Sonnet | Deeper reasoning per domain | ~$3/M tokens |
| **Tier 3** | Opus | High-quality final documents | ~$15/M tokens |

**Typical scan (2000 classes):** $5–15 end-to-end, depending on codebase complexity.

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
  tier1: us.anthropic.claude-haiku-4-5-20251001-v1:0
  tier2: us.anthropic.claude-sonnet-4-6-v1:0
  tier3: us.anthropic.claude-opus-4-6-v1:0

output_directory: ./data/discovery-output
```

Then use: `discover scan <repo> -p <project> -c config.yaml`

## CLI Reference

### Basic Usage

```bash
discover scan REPO -p PROJECT_SLUG [OPTIONS]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `REPO` | Path or URL (file path or `https://github.com/org/repo.git`) |
| `-p, --project-slug` | Project slug for outputs (e.g., `myproject`) |

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `-b, --branch` | Git branch to analyze | `main` |
| `-o, --output` | Output directory | `./data/discovery-output` |
| `-c, --config` | YAML config file | (uses env vars) |
| `--provider` | LLM provider: `bedrock` \| `ollama` | (from env) |
| `--budget` | Budget limit in USD | (from env) |
| `--resume` | Resume interrupted run | (disabled) |
| `--rescan` | Force full rescan, ignore cache | (disabled) |

### Push Options (Optional)

Push results to a remote system:

```bash
discover scan <repo> -p <project> \
  --push api \
  --api-url https://api.example.com \
  --api-token $DISCOVERY_API_TOKEN
```

| Option | Description |
|--------|-------------|
| `--push` | Mode: `api` \| `gitea` |
| `--api-url` | DocHub API base URL |
| `--api-token` | Bearer token (or env `DISCOVERY_API_TOKEN`) |
| `--gitea-url` | Gitea base URL |
| `--gitea-token` | Gitea API token (or env `DISCOVERY_GITEA_TOKEN`) |

### Query Database

```bash
discover query "SELECT name, node_type, domain FROM code_nodes LIMIT 20"
```

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
data/discovery-output/
├── discovery.db              # SQLite: code nodes, calls, flows, costs, summaries
└── docs/
    ├── as-is/                # Current state assessments
    ├── spec/                 # Functional specifications
    ├── interface/            # API contract documentation
    └── data-model/           # Entity and schema documentation
```

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
│   ├── pipeline.py                 # 13-phase orchestrator
│   ├── ai/                         # LLM operations (chunker, summarizer, etc.)
│   ├── graph/                      # Call graph & domain classification
│   ├── ingest/                     # Code ingestion & parsing
│   ├── output/                     # Doc generation & pushing
│   ├── parsers/                    # Language-specific parsing (Python, C#, Java, JS/TS)
│   ├── rag/                        # Embeddings & retrieval
│   ├── repo/                       # Git operations
│   └── tests/                      # Unit tests
├── docs/                           # Architecture & guides
├── examples/                       # Usage examples
└── pyproject.toml                  # Package configuration
```

## License

MIT

---

## Getting Help

- **First time?** Start with [Quick Start](#quick-start), then see [`examples/`](examples/)
- **Questions?** Check [`docs/INDEX.md`](docs/INDEX.md) for topic-based navigation
- **Want to contribute?** See [`CLAUDE.md`](CLAUDE.md) for development workflow
- **Found a bug?** Check existing issues or create a new one
