# Implementation Summary — Reverse-Spec Review → Closure

**Date:** 2026-05-31
**Companion to:** `04-reverse-spec-solution-review.md` (the objective review; its §0 has the per-finding status matrix)
**Scope:** One session that reviewed the reverse-spec engine, then implemented essentially every finding.

---

## 1. The arc

This session ran as **review → scope → implement → verify**, repeated in waves:

1. **Objective review** (multi-agent, output-trustworthiness lens, adversarially verified against current code) → `04-…md`. The central finding: the engine had a genuinely best-in-class faithfulness control — *demote the LLM from author-of-facts to author-of-prose-around-facts* — but it was **risk-inverted**: that control covered the artifacts least prone to mislead (structured rollup tables) and was absent from the most visible, most narrative ones (process-flow docs, screen specs), which shipped at `confidence: 1.0`. Plus a flagship feature (screen-centric) that crashed on its target inputs.

2. **Implementation in waves**, each a focused branch → tests → merge → push. Roughly in order: CRIT-1 + the cascade it unmasked → faithfulness fixes (HIGH-1/2/4/5) → FK extraction (HIGH-6) → the **corpus accuracy harness** (HIGH-10) → and then *everything after was gauged against that harness*: DI resolution (HIGH-3, 4 languages), PF/screen grounding (CRIT-2/3), non-code ingestion (HIGH-7: OpenAPI, IaC, GraphQL, proto), external-system nodes + cross-repo correlator (HIGH-8), the dependency catalog, and finally the LSP/SCIP resolver tier.

**Outcome:** every CRITICAL and every HIGH finding from the review is closed, plus all MED/LOW and the original 2026-04-16 gap-assessment's external-dependency and integration-inventory findings.

---

## 2. The discipline that made it work: gauge-first

The corpus accuracy harness (`tests/corpus/`) was built **early and deliberately first**, because the review's harshest finding was that accuracy was *asserted, never measured* (`test-strategy.md` documented a `CallGraphResolver` API that didn't exist). Once the harness existed, the pattern for every subsequent capability was:

1. Write a **fixture that exposes the gap**, with human-labeled `ground_truth.json` (labeled *from source*, never regenerated from the extractor — the anti-self-confirmation rule).
2. Confirm the gap is real (the metric is low / the heuristic fans out).
3. Implement the fix.
4. Watch the metric flip, and lock it as a hard target + regression baseline.

This turned subjective "I think this is better" into **objective, regression-locked proof**. The clearest example: HIGH-3 (DI resolution) and the LSP tier each had a `*-di-collision` / `lsp-interface-dispatch` fixture whose `di_resolution` score was **0.0 before, 1.0 after** — the harness proved the fix and now prevents its regression.

The harness today: **12 fixtures × 6 dimensions** (endpoints / entities+fields / FK relationships / call-edge resolution / di-resolution / external-systems), every cell green.

---

## 3. The unifying theme: widen the faithfulness control's coverage

Almost every change extended the same idea — **separate verified facts from LLM narrative, and make the facts deterministic + cited** — to a place it hadn't reached:

| Where the control was extended | How |
|---|---|
| Endpoints (C#, Python) | Read verbs/routes/prefixes faithfully (HIGH-4/5) |
| Endpoints (contracts) | OpenAPI / GraphQL / proto as authoritative sources (HIGH-7) |
| Entities + relationships | FK extraction + `db_relationship` (HIGH-6) |
| Call edges | DI/receiver-type resolution (HIGH-3) + LSP index tier |
| External systems | First-class typed nodes from code + IaC (HIGH-8) |
| Cross-repo | provider→consumer integration edges (HIGH-8) |
| Process-flow docs | source-fed prompts + Source Coverage citations + `--prod` verification (CRIT-2) |
| Screen specs | structured output + source citations + Phase-17 verification (CRIT-3) |
| Confidence itself | `blend_confidence` no longer 1.0 for the unverifiable (HIGH-1); self-review abstains on weak evidence (HIGH-2) |

The screen-spec bug fixed first (CRIT-1) is emblematic: fixing the crash unmasked two more latent bugs (fence-parsing, token truncation) in a never-exercised path, which were then fixed *durably* via Bedrock tool-use structured output rather than patched — and the whole class of "LLM invents structured facts" was closed.

---

## 4. By the numbers

- **44 commits** (since pre-session `562185a`), ~**+5,557 / −150** lines across **101 files**, each wave its own branch merged with `--no-ff`.
- **838 tests** (from ~764), **74 test files**, all green.
- **12 corpus fixtures**, **7 extractors** (`sql`, `relationship`, `external_system`, `openapi`, `infra`, `graphql`, `proto`), all merged to `main` and pushed.

---

## 5. New capabilities added (beyond fixes)

- `tests/corpus/` — deterministic, no-LLM accuracy harness + baseline regression lock.
- `extractors/` — FK relationships, external-system promotion, and four non-code contract readers (OpenAPI, docker-compose/K8s, GraphQL, proto).
- `graph/integration_correlator.py` — cross-repo provider→consumer matching by normalized path; `federation` writes `integration_edges.json` + `integration_map.md`.
- `graph/symbol_index.py` — the LSP/SCIP consumer tier (Stage 0 resolution).
- `generators/dependency_catalog.py` — a human-readable dependency & interface inventory (closes gap-assessment #7).
- `db_relationship` table (schema v10); `interfaces.json` per-scan artifact.

---

## 6. Intentionally not done (and why)

Two items remain — both **external-tooling / infrastructure investments, not code gaps**:

1. **Producing** a real SCIP/LSIF index. The *consumer* tier is built, tested, and gauged (`lsp-interface-dispatch`); running an actual indexer needs external tooling outside this codebase.
2. **HIGH-10 Phase 3** — pinned *external* real repos in the corpus. The harness supports it; vendoring real third-party codebases (size, licensing) is a deliberate infra decision.

Neither was forced, on principle: implementing untestable/speculative infrastructure at the tail of the pass would reproduce the "under-tested sprawl" the review explicitly warned against. Everything that could be built and **verified with integrity** is done.

---

## 7. Verdict delta

The review graded the engine **"B− as engineering, C+ as a trustworthy product"** — held back almost entirely by the risk-inversion. That inversion is now reversed: the deterministic-facts-plus-cited-provenance control reaches every generated artifact, call resolution is type/index-aware across four languages, external and cross-repo dependencies are first-class, and an accuracy harness measures (rather than asserts) the load-bearing targets. The remaining frontier is real-world scale (external corpora) and external precise-indexing — genuinely next-phase, not backlog.
