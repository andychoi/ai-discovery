# AI-Discovery Documentation Index

Navigation guide for the AI-Discovery reverse-engineering engine documentation.

---

## Start Here

**First time?** Read these in order:
1. `../CLAUDE.md` — Project overview, principles, skills
2. `architecture/overview.md` — High-level system design
3. `architecture/components.md` — Module breakdown and responsibilities

---

## Architecture (System Design)

Learn how the system is built:

- **`architecture/overview.md`** — Purpose, data flow, L1–L7 framework, LLM tiers, storage layout
- **`architecture/components.md`** — Module map, data models, hard problem areas (A & D)
- **`architecture/decisions.md`** — Design rationale: why 7-level confidence? Why 3 LLM tiers? Trade-offs explained.

---

## Guides (How-To Reference)

Practical guides for working on specific areas:

### Call Graph Resolution (Area A)
*"I'm debugging call resolution. Why did function X resolve to function Y?"*

- **`guides/call-graph/resolution-heuristics.md`** — 7-level confidence scoring with detailed examples
- **`guides/call-graph/debugging-workflow.md`** — Step-by-step: trace call → analyze signals → validate → decide
- **`guides/call-graph/test-strategy.md`** — Unit tests, integration tests, corpus validation, metrics

### Language Parsers (Area D)
*"I want to add Go (or another language) support."*

- **`guides/parsers/architecture.md`** — How parsers work, tree-sitter, AST traversal, framework detection
- **`guides/parsers/extension-checklist.md`** — Step-by-step checklist: pre-flight → AST mapping → integration → validation
- **`guides/parsers/language-patterns.md`** — Python vs Java vs C# vs JavaScript vs Go: AST differences, naming conventions, framework patterns

> Beyond the tree-sitter parsers, an **import-map tier** (`parsers/import_map.py`) gives lightweight symbol + import + call-site extraction for Go/Rust/Ruby/PHP, and dedicated **JSP** (`parsers/jsp.py`) and **WebForms** (`parsers/webforms.py`) parsers feed server-rendered screen detection. See `architecture/components.md`.

### Pipeline & Performance
*"The pipeline is slow. Where's the bottleneck? How do I optimize?"*

- **`guides/pipeline/phase-breakdown.md`** — Details of all pipeline phases (pre-pipeline 1–4, checkpoint phases 5–19), cost per phase, typical timing
- **`guides/pipeline/cost-tracking.md`** — Budget configuration, cost monitoring, optimization strategies (reduce tier 2/3, skip phases, batch chunks)
- **`guides/pipeline/profiling.md`** — Identifying bottlenecks, experiments, benchmarks for small/medium/large codebases

### Entity Identity Consolidation (Phase 2d+)

> Phase numbers in this section refer to the state-first backbone spec (`specs/2026-04-20-state-first-backbone-plan.md`), not pipeline phases. See `guides/pipeline/phase-breakdown.md` for pipeline numbering.

*"Why were `Order` and `OrderEntity` merged into one FSM? Why wasn't `OrderDto`?"*

- **`guides/entity-identity/consolidation-algorithm.md`** — Dual-pass fingerprint algorithm with inheritance awareness, mixin detection, and asymmetric projection handling
- **`guides/entity-identity/edge-cases.md`** — Survey of programming-style, MVC, and dynamic-DB patterns that stress the fingerprint — with frequency / severity ratings and phase assignments

### Exploring Scan Results
*"I ran `discover scan` — how do I navigate the output and check quality?"*

- **`guides/exploring-results/navigation.md`** — `discover view` web dashboard (quality targets, histogram, weakest-docs, diagrams), plus `chat`/`impact`/`query` commands and first-pass audit recipe

> Related outputs: `discover export-graph` emits a canonical knowledge-graph JSON (`generators/graph_export.py`), and the **ONBOARD/** tour guides (`generators/onboarding_generator.py`) give a per-domain pedagogical learning path through the high-confidence call chain.

---

## Recently Shipped Capabilities

Cross-cutting features layered onto the core pipeline (most trace to assessment 07; see `assessments/07-understand-anything-comparison.md`):

- **Louvain semantic batching** (`ai/semantic_batching.py`, A-1) — clusters Tier-1 chunks by call-graph community so each batched LLM call carries chunks that reference each other.
- **Community narrowing in call resolution** (A-3) — file communities from high-confidence edges narrow stage-4 short-name candidates to the caller's community. See `architecture/decisions.md`.
- **Import-map parser tier** (`parsers/import_map.py`, A-6) — Go/Rust/Ruby/PHP lightweight symbol + import extraction.
- **ONBOARD tour-guide generator** (`generators/onboarding_generator.py`, A-7) — per-domain tours from the call graph.
- **Multi-provider LLM layer** — bedrock / ollama / mlx-gemma / mlx-qwen / openai / gemini / anthropic, routed via `shared/llm_router.py`. See `architecture/overview.md` (LLM Tier Model).
- **Fingerprint-based incremental re-scan** (`repo/resolver.py`) — non-git folder scans get a stable content fingerprint for change detection.
- **JSP parsing & menu detection** — server-rendered (`.jsp`/`.aspx`) screen detection feeding screen-centric specs.
- **`discover export-graph`** — canonical knowledge-graph JSON export (`generators/graph_export.py`).
- **Hierarchical `structured_steps`** — see `specs/2026-06-01-processing-logic-level-structuring.md`.

---

## Specifications (Design Records)

Design decisions recorded per session:

- **`specs/2026-04-17-dev-infrastructure-design.md`** — Documentation reorganization, three custom skills, modular approach
- **`specs/2026-06-01-processing-logic-level-structuring.md`** — Assessment of how control flow (loops/conditionals) rolls up into spec prose; hierarchical `ScenarioFlow.structured_steps` + leveled IPO/flowchart/sequence rendering

---

## Assessments (Reviews & Gap Analyses)

Point-in-time reviews, gap assessments, and implementation closures (chronological):

- **`assessments/02-reverse-engineering-gap-assessment.md`** — Reverse-engineering gap assessment (2026-04-16): where output fell short of true reverse-engineering
- **`assessments/02-reverse-engineering-implementation-plan.md`** — Implementation plan answering the gap assessment
- **`assessments/03-discovery-output-quality-assessment.md`** — Discovery output quality improvement plan (2026-05-07), evaluated on the Java Spring Boot e-commerce corpus
- **`assessments/04-reverse-spec-solution-review.md`** — Multi-agent objective review of architecture, design & implementation (2026-05-31), with per-finding status matrix
- **`assessments/05-implementation-summary.md`** — Closure summary for the reverse-spec review findings (2026-05-31)
- **`assessments/06-architecture-production-readiness-audit.md`** — Principal-architect production-readiness audit (2026-05-31)
- **`assessments/07-understand-anything-comparison.md`** — Comparative assessment vs Understand-Anything (2026-06-06); source of the A-1/A-3/A-6/A-7 items (semantic batching, community narrowing, import-map tier, ONBOARD tours)

---

## Integrations & Features

Advanced integrations and optional features:

- **`integrations/PM4PY_INDEX.md`** — Process mining integration overview
- **`integrations/PM4PY_IMPLEMENTATION_COMPLETE.md`** — Full implementation details
- **`integrations/PM4PY_INTEGRATION_WIRED.md`** — Integration architecture

---

## Deployment & Operations

Production deployment and operational guides:

- **`deployment/INTEGRATION_CHECKLIST.md`** — Pre-deployment checklist, configuration, verification steps

---

## Demos & Examples

Demo applications and example usage:

- **`demos/README.md`** — Running demos, test setup, example outputs

---

## Quick Reference

### By Use Case

| I want to... | Start here |
|---|---|
| Understand the system | `architecture/overview.md` |
| Add language support (Go, Rust, etc.) | `guides/parsers/extension-checklist.md` |
| Debug call resolution | `guides/call-graph/debugging-workflow.md` |
| Optimize pipeline speed/cost | `guides/pipeline/profiling.md` + `cost-tracking.md` |
| Understand design decisions | `architecture/decisions.md` |
| Set up testing | `guides/call-graph/test-strategy.md` |

### By Problem Area

| Area | Guides | Key Documents |
|---|---|---|
| **A: Call Graph Resolution** | `guides/call-graph/*` | `architecture/decisions.md` (7-level scoring) |
| **D: Language Parsers** | `guides/parsers/*` | `architecture/components.md` (hard problems) |
| **Pipeline Performance** | `guides/pipeline/*` | `architecture/overview.md` (phases 5–19) |

### By Development Phase

| Phase | Documentation |
|---|---|
| **Plan** (design-forward) | `architecture/` + `specs/` |
| **Implement** | `guides/` (step-by-step checklists) |
| **Debug** | `guides/call-graph/debugging-workflow.md` + `guides/pipeline/profiling.md` |
| **Test** | `guides/call-graph/test-strategy.md` |
| **Understand Trade-offs** | `architecture/decisions.md` |

---

## File Structure

```
docs/
├── INDEX.md ← You are here
│
├── architecture/ ── System design & rationale
│   ├── overview.md ─── High-level design, L1–L7, LLM tiers
│   ├── components.md ─ Module breakdown, responsibilities
│   └── decisions.md ─── Design rationale, heuristics, trade-offs
│
├── guides/ ──────── How-to & reference (for developers)
│   ├── call-graph/ ─ Call resolution (Area A)
│   │   ├── resolution-heuristics.md
│   │   ├── debugging-workflow.md
│   │   └── test-strategy.md
│   ├── parsers/ ──── Language support (Area D)
│   │   ├── architecture.md
│   │   ├── extension-checklist.md
│   │   └── language-patterns.md
│   └── pipeline/ ──── Performance & cost
│       ├── phase-breakdown.md
│       ├── cost-tracking.md
│       └── profiling.md
│
├── specs/ ──────── Design records per session
│   └── 2026-04-17-dev-infrastructure-design.md
│
├── integrations/ ─── Advanced features & integrations
│   ├── PM4PY_INDEX.md
│   ├── PM4PY_IMPLEMENTATION_COMPLETE.md
│   └── PM4PY_INTEGRATION_WIRED.md
│
├── deployment/ ───── Production deployment guides
│   └── INTEGRATION_CHECKLIST.md
│
├── demos/ ───────── Example usage & demos
│   └── README.md
│
└── archived/ ────── Legacy documentation
    ├── architecture.md (migrated → architecture/)
    ├── flow.md (migrated → guides/pipeline/)
    ├── IMPLEMENTATION_SUMMARY.md
    └── PM4PY_*.md (migrated → integrations/)
```

---

## Key Concepts

### Confidence Scoring
The system uses **7-level heuristic scoring** (0.5–1.0) instead of binary match/no-match. This allows nuanced decisions: a call might be good for BPMN (confidence >= 0.9) but needs review in tech specs (confidence >= 0.85).

**Reference**: `guides/call-graph/resolution-heuristics.md`

---

### Multi-Signal Scoring
Confidence combines multiple signals: depth (how far from entry point?), state transitions (does it change state?), data boundaries (DB/queue?), external calls, read-after-write patterns.

**Reference**: `architecture/decisions.md` (Confidence Scoring section)

---

### Pipeline (Logical View)
From code to docs: resolve repo → parse → classify domains → build call graph → execution slices → chunk & embed → Tier 1/2/3 LLM → visual artifacts → self-review → render markdown → ingest. CLI checkpoint phases are numbered 5–19 (all integers, no decimal sub-phases); see `guides/pipeline/phase-breakdown.md` for the full mapping.

**Reference**: `guides/pipeline/phase-breakdown.md`

---

### L1–L7 Decomposition
Code is documented at 7 levels of abstraction: business domain → process → flow → scenario → service → function → statement.

**Reference**: `architecture/overview.md` (L1–L7 Framework section)

---

## Development Skills

Three custom skills guide development:

| Skill | Use When | Reference |
|---|---|---|
| `/parser-extension` | Adding language support | `guides/parsers/extension-checklist.md` |
| `/call-graph-debug` | Debugging call resolution | `guides/call-graph/debugging-workflow.md` |
| `/pipeline-analyze` | Optimizing performance/cost | `guides/pipeline/profiling.md` |

---

## Project CLAUDE.md

For quick reference while coding: `../CLAUDE.md`

Contains:
- Development principles
- Common tasks (with references)
- Hard problem areas
- Skills overview
- Key metrics & targets

---

## Archived Docs

Legacy documentation kept for historical reference:

- `archived/architecture.md` — Old architecture overview (migrated to `architecture/overview.md` + `architecture/components.md`)
- `archived/flow.md` — Old flow documentation (migrated to `guides/pipeline/phase-breakdown.md`)
- `archived/IMPLEMENTATION_SUMMARY.md` — Old implementation summary
- `archived/PM4PY_INTEGRATION.md` — Old PM4Py docs (migrated to `integrations/`)
- `archived/PM4PY_MODULES_SUMMARY.md` — Old PM4Py modules reference
- `archived/todo-production.md` — Old production TODO list
- `archived/01-enhance-plan.md` — Pre-implementation "Tier 2.5 / Tier 3.5" sketch that diverged from what shipped (see `specs/2026-04-20-state-first-backbone-plan.md` for the implemented design)

---

## How to Use This Documentation

### Scenario 1: "I want to add Go support"
1. Read `../CLAUDE.md` (overview, principles)
2. Skim `guides/parsers/architecture.md` (background)
3. Follow `guides/parsers/extension-checklist.md` (step-by-step)
4. Reference `guides/parsers/language-patterns.md` (Go-specific patterns)

### Scenario 2: "Call resolution is wrong for this function"
1. Read `guides/call-graph/resolution-heuristics.md` (theory)
2. Use `/call-graph-debug` skill (guided debugging)
3. Reference `guides/call-graph/test-strategy.md` (validation)
4. Check `architecture/decisions.md` (why this heuristic?)

### Scenario 3: "Pipeline is slow"
1. Read `guides/pipeline/phase-breakdown.md` (phase overview)
2. Use `/pipeline-analyze` skill (profile & identify bottleneck)
3. Reference `guides/pipeline/profiling.md` (optimization strategies)
4. Check `guides/pipeline/cost-tracking.md` (cost/benefit trade-offs)

---

## Updating Documentation

When you learn something new:

1. **Design decision?** Add to `architecture/decisions.md`
2. **New heuristic?** Update relevant guide (e.g., `guides/call-graph/resolution-heuristics.md`)
3. **New pattern?** Add to `guides/parsers/language-patterns.md`
4. **Implementation complete?** Record in `specs/YYYY-MM-DD-topic-design.md`
5. **Update this INDEX** if structure changes

---

## See Also

- `../CLAUDE.md` — Project overview and quick reference
- `../README.md` — Installation, CLI usage, examples
- `../examples/` — Practical usage examples (local, GitHub, Docker)
- Code comments in `../src/ai_discovery/` — Implementation details
