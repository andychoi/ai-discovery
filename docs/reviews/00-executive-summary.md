# Architecture Review — Executive Summary

**Date**: 2026-07-12
**Reviewed at**: commit `d9d997b` on branch `claude/architecture-review-roadmap-28vngf`
**Review scope**: full repository (~29k lines of Python), no code modified
**Companion documents** (this folder):

| Doc | Contents |
|---|---|
| `01-architecture-overview.md` | System architecture + end-to-end workflow |
| `02-complex-algorithms.md` | Deep analysis of the hard algorithms (call resolution, FSM identity, batching) |
| `03-ai-fable-integration-review.md` | Anthropic/Claude integration review + Fable-class adoption plan |
| `04-risks-and-technical-debt.md` | Smells, duplicated logic, missing abstractions, bugs, bottlenecks |
| `05-roadmap-and-refactoring-plan.md` | Prioritized roadmap (impact × effort) + refactoring sequencing |

---

## What this product is

AI-Discovery is a **brownfield reverse-engineering engine**: point it at a legacy repository and it parses the code (tree-sitter AST + regex fallback tiers), reconstructs the call graph with graded confidence, infers business domains, scenarios, entity state machines, and screens, then drives a **three-tier LLM pipeline** (Haiku-class → Sonnet-class → Opus-class) to generate a full SDLC documentation set — ASIS/ASD/ASSC domain docs, process flows, BPMN/DMN/EARS artifacts, screen-centric specs with drift detection, dependency catalogs, onboarding tours — all persisted in a single SQLite database with a local viewer, RAG chat, and self-review verification loop.

## Overall assessment

**This is a genuinely well-designed system with above-average engineering discipline for its size**, held back by accumulating structural debt in three specific places. The strengths are real and unusual:

- **The faithfulness architecture is the crown jewel.** Multi-signal graded confidence on every call edge (0.5–1.0, six resolution stages), a RAG-backed self-review loop that extracts claims from generated docs and verifies them against source with *abstention* on weak evidence, a deterministic prose validator that flags fabricated file/symbol citations, and confidence capping of unverified narrative at 0.5. Most LLM doc-generation tools have none of this.
- **The corpus test harness is exemplary**: real LLM-free extraction runs over 14 labeled fixtures with hard F1 floors (0.80/0.85) *and* a no-regression gate against a committed baseline.
- **Cost governance works**: tiered models, per-tier ledgers, a hard budget guard, semantic (Louvain) batching of Tier-1 calls, cross-scan summary reuse via file hashing, and local-model-first defaults.
- **Documentation is unusually honest** — reversals are recorded (e.g. pm4py removal because its metrics were "fabricated"), decisions carry rationale.

The debt clusters in three places:

1. **`pipeline.py` is a 1,466-line god function.** All 18 phases are hand-unrolled `if` blocks with copy-pasted resume/checkpoint/reload plumbing, inline SQL, and a large uncheckpointed "phase 8b" block. This is the single biggest tax on every future change.
2. **Two parallel extraction stacks.** The precise tree-sitter/`CodeNode` graph and a loose regex stack (`screen_mapper.py`, `entity_service_resolver.py`) that re-opens and re-greps files the parsers already parsed — Java-biased, duplicative, and the main reason screen mapping underperforms on non-Java backends.
3. **Systematic small-scale duplication with no shared primitives**: four copies of eight parser algorithms, three divergent stem-normalizers, two union-finds plus one mislabeled greedy grouping, two Jaccards with *opposite* empty-set semantics, two BFS implementations (one O(n²)), dual LLM cost ledgers, DMN/EARS sibling copy-paste, and a doc embedder that is a degraded fork of the excellent code embedder.

## Confirmed bugs found during review (not fixed, per review mandate)

| Severity | Bug | Location |
|---|---|---|
| High | `discover ingest-docs` crashes with `NameError` — `_VALID_PUSH_MODES` never defined | `cli.py:756` |
| High | Doc-embedder `doc_id` collision across ASIS/ASD/ASSC (same `f.stem`) breaks resume guard and chat citations | `doc_embedder.py:111` vs `doc_generator.py:73-84` |
| High | Scan with **zero parsed nodes** finalises as `completed`, exit 0 | `pipeline.py:707-710` |
| Medium | Budget-exceeded runs return exit 0 (`status=budget_exceeded`, no non-zero exit) | `pipeline.py:1189,1240,1328` |
| Medium | `MODEL_RATES` would price a Fable-class model at the $3/$15 fallback (real: $10/$50), defeating the budget guard | `model_defaults.py:152-176` |
| Medium | Federation FSM merge is greedy first-fit, not union-find as documented — non-transitive grouping splits entities on 3+ repo merges | `federation.py:176-205` |
| Medium | `stop_reason` never inspected on any LLM response — truncation guessed heuristically; a refusal would silently produce an empty/partial doc | `llm_invoke.py`, `llm_router.py:460` |
| Low | BPMN lanes declared but `flowNodeRef` never assigned — swimlanes render empty | `bpmn_generator.py:252` |
| Low | Read-after-write scoring signal matches field names against node *names*, not source — effectively decorative | `call_graph.py:697-703` |
| Low | Resume-path scenario inference swallows all exceptions silently (`except Exception: pass`) | `pipeline.py:1315-1316` |

## Anthropic integration — headline findings

There is **no Fable/Mythos integration today** (assumption: the request refers to Claude-family integration generally; see `03-ai-fable-integration-review.md`). The current integration is solid on structure (forced-tool-choice structured outputs, multi-provider router, advisor-tool beta) but leaves the three biggest platform levers unused:

- **No prompt caching** — the pipeline re-sends identical instruction prefixes hundreds of times per scan (~up to 80–90% input-cost reduction available on cached prefixes).
- **No Batches API** — this is an offline batch workload; batching phases 11/17 halves their cost outright (Anthropic direct API; not available on Bedrock — a reason to rebalance provider strategy).
- **No thinking/effort configuration** — Opus-tier doc synthesis currently runs with reasoning effectively off on Opus 4.7/4.8 (omitted `thinking` = no thinking).

Fable 5 belongs in exactly one slot: **`tier3p` (the `--prod` deep-rollup tier)**, behind the existing budget guard, and only after refusal handling, pricing-table fixes, and thinking/effort support land (details and checklist in doc 03).

## Top five recommendations (full roadmap in doc 05)

1. **R1 — Extract a phase driver + DAO layer from `pipeline.py`** (M effort, very high leverage): a `Phase` protocol with `run()/load_from_db()`, one driver loop, persistence moved into `db.py`. Unblocks everything else.
2. **R2 — Prompt caching + Batches API + adaptive thinking/effort** in the LLM layer (M): the largest cost reduction (~50–70% of cloud spend) and the largest quality lift (tier-3 reasoning) available anywhere in this codebase, with no architectural risk.
3. **R3 — Kill the regex re-parsing stack**: make `screen_mapper`/`entity_service_resolver` consume the `CodeNode` graph instead of re-grepping files (M–L): fixes non-Java screen mapping, removes ~1,000 lines of brittle string matching.
4. **R4 — Shared primitives module** (`graph/util`): one `UnionFind`, one `jaccard`, one `normalize_stem`, one deque-BFS, one retry helper, one confidence-constants table (S–M): eliminates a whole class of divergence bugs.
5. **R5 — Fix the confirmed bugs above** (S): most are hours each; the exit-0-on-failure family matters for CI trust.

## Scorecard

| Dimension | Grade | One-line justification |
|---|---|---|
| Scalability | C+ | Single-process, all-in-memory node lists, O(n²) BFS hotspots, per-op SQLite connections; fine to ~50k nodes, strained beyond |
| Maintainability | C | Excellent docs and tests undermined by the pipeline god-function and pervasive micro-duplication |
| Extensibility | B− | Import-map tier and menu-detector plug-ins are good patterns; parser extension is 3-file wiring + 600-line copy-paste; the checklist doc no longer matches the code |
| Performance | B− | Threaded LLM fan-out, semantic batching, incremental re-scan are strong; phase 13 serial LLM loop and unbatched embedding on Bedrock drag |
| Cost efficiency | B | Tiering + budget guard + reuse are good; caching/batching absence leaves ~50%+ of cloud spend on the table |
| Correctness/faithfulness | A− | Best-in-class verification loop; a handful of confirmed edge bugs |
| Test discipline | B+ | Corpus harness is exemplary; consumption edge (chat, doc-embedder, HTTP server) untested |
