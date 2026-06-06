# Docs Refresh + System-Logic Review Document — Design

**Date**: 2026-06-06
**Status**: Approved

## Goal

1. Bring README.md, CLAUDE.md, and docs/ up to date with the 2026-06-06 codebase
   (A-1…A-7 features, multi-provider LLM layer, JSP support, Phase 2 screen specs).
2. Produce a detailed system-logic document for IT developer team review,
   in English (canonical) and Korean (review copy), with inline Mermaid diagrams.

## Part 1 — Documentation updates (all audit findings)

| File | Fixes |
|------|-------|
| `README.md` | Add openai/gemini/anthropic providers; expand language table (JSP, WebForms, import-map tier Go/Rust/Ruby/PHP); document `view`/`test-llm`/`export-graph` CLI; add Phase 2 to phase table; fix `OLLAMA_BASE_URL`→`OLLAMA_URL`; reflect A-1…A-7 |
| `discovery.yaml.example` | Add openai/gemini/anthropic config blocks |
| `CLAUDE.md` | Brief mentions of new features (Louvain batching, incremental re-scan, export-graph, ONBOARD) |
| `docs/INDEX.md` | Link assessments/ folder (currently orphaned); link new features/specs |
| `docs/architecture/overview.md` | Phase 2, multi-provider routing, ONBOARD artifact |
| `docs/architecture/components.md` | Add import_map/jsp/webforms parsers, semantic_batching, onboarding_generator |
| `docs/guides/pipeline/phase-breakdown.md` | Resolve duplicate Phase 8 definition (execution_slices only); add Phase 2; fix "7-level"→4-stage confidence; add Louvain + fingerprint reuse |
| `docs/architecture/decisions.md` | Add Stage 0 (symbol index) note |
| `docs/guides/exploring-results/navigation.md` | Mention `discover export-graph` |

Execution: 3 parallel agents over the 3 independent file groups (root files /
architecture docs / guides), each verifying claims against code before editing.

## Part 2 — System-logic review document (new)

**Location**: `docs/review/system-logic.md` (English canonical) +
`docs/review/system-logic.ko.md` (Korean review copy).
**Diagrams**: inline Mermaid (project standard — Mermaid primary).

13 chapters, with file:line citations:

1. System overview (purpose, I/O, context diagram)
2. End-to-end pipeline (phases 2, 5–19; flow diagram with budget gates & resume points; per-phase I/O + DB-impact table)
3. Parsing layer (full-AST ×6 vs import-map tier ×4; contract extractors)
4. Call-graph resolution (5-stage flowchart; confidence table 1.0→0.5; community narrowing A-3)
5. Entity/FSM backbone (phases 8a–8d)
6. LLM layer (7 providers × 5 tier slots routing diagram; structured output; cost/budget)
7. RAG & self-review loop
8. Screen-centric track (5 menu formats; screen→backend mapping; drift detection)
9. Generators & artifacts (output tree)
10. Data model (ER diagram of key tables)
11. Confidence model (explicit formulas)
12. Operational characteristics (resume/checkpoints, incremental re-scan, memory, warm/unload)
13. Known limitations & review discussion points

Order: Part 1 edits → commit → Part 2 English → Korean → commit.
