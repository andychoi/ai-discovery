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

Canonical phase numbers (5–19) match `_PHASE_SPECS` in `pipeline.py` and the rows persisted to `phase_checkpoints`. See `docs/guides/pipeline/phase-breakdown.md` for the full reference.

```
 5. lang_detect          — detect languages (extensions + manifests)
 6. parse                — tree-sitter AST extraction
 7. domain_classify      — namespace/path heuristics
 8. execution_slices     — BFS scenarios from entry points (optional)
 9. chunk                — method-level splits for RAG
10. rag_embed            — embeddings → sqlite-vec
11. tier1_summarize      — Haiku per-chunk summaries
12. tier2_flow_analysis  — Sonnet per-domain flows
13. scenario_flow_inference  — per-scenario LLM reconstruction
14. tier3_doc_rollup     — Sonnet/Opus final docs
15. visual_artifacts     — BPMN + DMN + EARS + Mermaid generation
16. process_mining       — flow statistics, edge frequencies (optional)
17. self_review          — RAG-verified claim checking
18. render_markdown      — Jinja2 render with ai-docs frontmatter
19. finalise             — write artifacts; optional DocHub/Gitea push
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
