# Brownfield Discovery CLI

Standalone CLI tool that analyzes existing codebases using Tree-sitter AST + multi-tier GenAI to produce draft SDLC documents for the ai-docs platform.

## Supported Languages

| Language | Parser | Extracts |
|----------|--------|----------|
| Python | tree-sitter-python | Classes, methods, FastAPI/Flask endpoints, call refs |
| C# / ASP.NET | tree-sitter-c-sharp | `[ApiController]` classes, `[HttpGet]` endpoints, EF Core models, DI |
| Java / Spring | tree-sitter-java | `@RestController`, `@Entity`, `@Scheduled` batch jobs, `@Autowired` DI |
| JS / TS / Node | tree-sitter-javascript/typescript | Express routes, React components (JSX), classes, arrow functions |

## Quick Start

### Local (Python)

```bash
pip install -r app/requirements.txt

# Scan a local repo
python -m app scan /path/to/repo -p my-project

# Scan a remote repo
python -m app scan https://github.com/org/repo.git -p my-project -b main

# Query results
python -m app query "SELECT name, node_type, domain FROM code_nodes LIMIT 20"
```

### Docker

```bash
docker compose build discovery

# Scan (reads AWS creds from .env automatically)
docker compose run --rm discovery scan https://github.com/org/repo.git -p my-project

# Mount a local repo
docker compose run --rm -v /path/to/repo:/repo:ro discovery scan /repo -p my-project

# Query
docker compose run --rm discovery query "SELECT * FROM business_flows"
```

## CLI Options

```
python -m app scan REPO [OPTIONS]

Required:
  REPO                          Path or URL of the repository
  -p, --project-slug TEXT       Target project slug

Options:
  -b, --branch TEXT             Git branch to analyse [default: main]
  -o, --output PATH             Output directory [default: ./data/discovery-output]
  --provider TEXT                LLM provider: bedrock | ollama
  -c, --config PATH             Path to YAML config file
  --budget FLOAT                Budget limit in USD
  --resume                      Resume a previous interrupted run
  --rescan                      Force full rescan (ignore cache)

Push options:
  --push TEXT                   Push mode: api | gitea
  --api-url TEXT                DocHub API base URL
  --api-token TEXT              Bearer token [env: DISCOVERY_API_TOKEN]
  --gitea-url TEXT              Gitea base URL
  --gitea-token TEXT            Gitea API token [env: DISCOVERY_GITEA_TOKEN]
```

## Pipeline Phases

```
1. Resolve repo (clone or validate local)
2. Detect languages (extensions + manifests)
3. Parse files (Tree-sitter AST extraction)
4. Build call graph + classify domains
5. Smart chunk (AST-aware, not naive char split)
6. Embed chunks for RAG (sqlite-vec)
7. Tier 1: Summarize chunks (Haiku — cheap, concurrent)
8. Tier 2: Analyze business flows per domain (Sonnet)
9. Tier 3: Generate doc rollups per domain (Opus)
10. Render markdown with ai-docs frontmatter (Jinja2)
11. Self-review: verify claims against source via RAG
12. Push to DocHub API or Gitea (optional)
```

## Output

```
data/discovery-output/
├── discovery.db              # SQLite — all extracted data, summaries, flows, costs
└── docs/
    ├── as-is/                # Current state assessments
    ├── spec/                 # Functional specifications
    ├── interface/            # API contract docs
    └── data-model/           # Entity/schema docs
```

Each markdown file includes ai-docs-compatible frontmatter:

```yaml
---
doc_id: myproject-orders-as-is
title: "Orders — As-Is Current State"
status: Draft
tags: [discovery-scan]
source: discovery_scan
scan_date: 2026-03-11T...
discovery_confidence: 0.82
unverified_claims: 3
---
```

## Configuration

Copy `discovery.yaml.example` and customize:

```yaml
provider: bedrock               # bedrock | ollama
budget_limit_usd: 50.0

bedrock:
  region: us-east-1
  tier1: us.anthropic.claude-haiku-4-5-20251001-v1:0
  tier2: us.anthropic.claude-sonnet-4-6-v1:0
  tier3: us.anthropic.claude-opus-4-6-v1:0

ollama:
  base_url: http://localhost:11434
  tier1: llama3.1:8b
  tier2: llama3.1:70b
  tier3: llama3.1:70b
```

Environment variables: `DISCOVERY_LLM_PROVIDER`, `DISCOVERY_API_TOKEN`, `DISCOVERY_GITEA_TOKEN`.

## 3-Tier LLM Strategy

| Tier | Model (Bedrock) | Used For | Cost |
|------|-----------------|----------|------|
| Tier 1 | Haiku | Chunk summaries (bulk, concurrent) | ~$0.80/1M tokens |
| Tier 2 | Sonnet | Business flow analysis per domain | ~$3/1M tokens |
| Tier 3 | Opus | Final doc generation per domain | ~$15/1M tokens |

Typical scan of 2000 classes: ~$5-15 depending on codebase complexity.

## Package Structure

```
app/
├── cli.py              # Typer CLI entry point
├── config.py           # YAML config loader + dataclasses
├── pipeline.py         # Full pipeline orchestrator
├── db.py               # SQLite WAL + schema
├── repo/               # Git clone, language detection, file walking
├── parsers/            # Tree-sitter parsers (Python, C#, Java, JS/TS)
├── graph/              # CodeNode models, call graph, domain classifier
├── ai/                 # Chunker, LLM client, summarizer, flow analyzer, rollup, self-review
├── rag/                # sqlite-vec embedder + semantic retriever
├── output/             # Jinja2 doc generator + push (API/Gitea)
└── tests/              # 138 unit tests + 1 integration test
```

## Design Reference

Full design document: [`docs/brown-field-discovery.md`](../../docs/brown-field-discovery.md)
