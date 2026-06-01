# Scope: Corpus Accuracy Harness (HIGH-10)

**Date:** 2026-05-31
**Status:** Phases 1 + 2 IMPLEMENTED (`tests/corpus/`, 4 fixtures, baseline recorded) — Phase 3 (external repos) pending.

**Phase 1 result (baseline):** spring-boot & aspnet endpoints/entities/relationships = 1.0; express endpoints = 1.0, **entities = 0.0** (surfaced gap: JS parser doesn't extract Mongoose schemas), relationships = 1.0. HIGH-4/5/6 regression-locked.

**Phase 2 result (call-edge resolution + DI gauge):** `call_edges` recall = 1.0 on all real fixtures (controller/route→service chains resolve correctly at ≥0.8). A dedicated **`java-di-collision`** fixture makes HIGH-3 measurable: two services share a `process()` method, and `orderService.process()` currently fans out to *both* services — so its **`di_resolution = 0.0`** today. That score flips to 1.0 when DI/receiver-type resolution (HIGH-3) lands, with the harness proving the fix. It is baseline-guarded (can't regress) and will be promoted to a hard target once HIGH-3 ships.

**Dimensions now scored:** endpoints, entities (+fields), relationships (FK), call_edges (resolution recall), di_resolution (no-fan-out / HIGH-3 gauge).
**Relates to:** Assessment `04-reverse-spec-solution-review.md` → HIGH-10 (faithfulness untested; accuracy targets are fiction)
**Why first:** This harness is the precondition for the other deferred items. DI resolution (HIGH-3), external-system modeling (HIGH-8), and OpenAPI/IaC ingestion (HIGH-7) all change extraction accuracy — without a measured baseline they can't be validated, only asserted. Build the ruler before reshaping the thing it measures.

---

## 1. The problem

CLAUDE.md commits to **≥85% call-resolution accuracy** and **≥80% parser accuracy**, but nothing measures either:

- `docs/guides/call-graph/test-strategy.md` documents a `CallGraphResolver` class with `.from_code(...)` and per-fixture `expected_calls.json` files. **None of these exist** — the real API is `build_call_graph(nodes: list[CodeNode]) -> list[CallEdge]`. The documented harness is fiction.
- `tests/test_real_projects.py` runs on the committed fixtures but asserts **existence / non-error** ("≥1 screen detected", "framework == spring"), never **correctness** of extracted routes, entities, or edges.
- The integration test mocks the LLM, so end-to-end hallucination is structurally invisible (separate finding, HIGH-10 sibling).

Result: a parser regression that silently corrupts routes or entities would pass CI green.

---

## 2. Non-negotiable principle: ground truth is human-labeled from source

The single failure mode that makes an accuracy harness worthless is **deriving ground truth from the extractor's own output** — then the test only proves the extractor agrees with itself. Every `ground_truth.json` in this design is written by **reading the source files by hand** (see the worked example at `tests/fixtures/projects/spring-boot-app/ground_truth.json`, derived by grepping `@*Mapping` annotations and entity fields, not by running the parser). The harness must never regenerate ground truth from the pipeline; a CI check should flag if `ground_truth.json` changes in the same commit as an extractor change without human sign-off.

---

## 3. Ground-truth schema (one `ground_truth.json` per fixture)

```jsonc
{
  "project": "spring-boot-app",
  "language": "java",
  "framework": "spring",
  "endpoints":      [{"handler", "method", "path"}],          // full composed path
  "entities":       [{"table", "fields": [...]}],
  "relationships":  [{"from_entity", "to_entity", "cardinality"}],  // FK edges (HIGH-6)
  "key_call_edges": [{"caller", "callee_suffix", "min_confidence", "note"}]  // a curated few, not exhaustive
}
```

Curated, not exhaustive: label the endpoints and entities completely (they're small, finite, high-value), but only a representative set of call edges (full call-graph labeling is impractical and brittle).

---

## 4. Metrics

Per dimension, compute **precision / recall / F1** by matching extracted items against ground truth:

| Dimension | Match key | Target (from CLAUDE.md) | LLM needed? |
|-----------|-----------|--------------------------|-------------|
| Endpoints | (method, path) exact | parser ≥ 0.80 (aim 1.0 — deterministic) | No |
| Entities + fields | table name + field-set F1 | parser ≥ 0.80 | No |
| Relationships (FK) | (from, to) | aim 1.0 on declared FKs (HIGH-6) | No |
| Call edges | (caller, callee-suffix) ≥ min_conf | resolution ≥ 0.85 | No |

**Crucially, all four dimensions are deterministic — no LLM, no network.** The harness exercises `parse_file` → `build_call_graph` / `extract_relationships` only. That makes it cheap, fast, and CI-safe, and it targets exactly the layer the verified-facts machinery depends on.

A miss is reported with the specific item (e.g. "expected `POST /api/orders`, not found" or "extracted `/{id}` — missing controller prefix"), so failures are actionable, not just a number.

---

## 5. Harness design (built on the REAL API)

```
tests/corpus/
├── __init__.py
├── runner.py            # load fixture → detect_languages → parse_file(all) →
│                        #   build_call_graph + extract_relationships → CorpusResult
├── metrics.py           # precision/recall/F1 + per-dimension diff reports
└── test_corpus_accuracy.py
```

- `runner.run_fixture(path) -> ExtractionResult` — walks the fixture with the real `walk_repo` + per-language parsers, builds the graph and relationships. No mocks.
- `metrics.score(extracted, ground_truth) -> {dimension: PrecisionRecall, misses: [...]}`.
- `test_corpus_accuracy.py` — parametrized over fixtures with a `ground_truth.json`; asserts each dimension meets its target and prints the miss list on failure. Marked `@pytest.mark.accuracy` so it can run in the fast suite (it's deterministic) but is also selectable in isolation.

Baseline file `tests/corpus/baseline.json` records current scores; the test fails if a dimension **regresses** below its recorded baseline (catches silent degradation even above the absolute target).

---

## 6. Phasing

- **Phase 1 (this branch's implementation):** deterministic dimensions — endpoints, entities, FK — on the 3 committed fixtures (spring-boot, aspnet, express). Ground truth authored for all three. This alone converts CLAUDE.md's parser target from aspiration to a gate, and locks in the HIGH-4/5/6 fixes against regression.
- **Phase 2:** call-edge accuracy with curated `key_call_edges` per fixture; establishes the ≥85% resolution baseline and becomes the measuring stick for HIGH-3 (DI resolution).
- **Phase 3 (optional):** a few pinned *external* real repos (shallow-cloned at a fixed SHA, or vendored) per language, to validate on messy real input rather than tidy fixtures. Gated behind a marker so it doesn't slow the default suite.

---

## 7. Cleanup: fix the fiction

Rewrite `docs/guides/call-graph/test-strategy.md` §"Batch Validation" to describe the real harness (this design) and delete the `CallGraphResolver.from_code()` / `expected_calls.json` API it invents. Keep the conceptual guidance (what to measure, why), drop the non-existent code.

---

## 8. File plan

| File | Action |
|------|--------|
| `tests/fixtures/projects/*/ground_truth.json` | Author (spring-boot seeded here; add aspnet + express) |
| `tests/corpus/runner.py`, `metrics.py`, `test_corpus_accuracy.py` | New harness on the real API |
| `tests/corpus/baseline.json` | Recorded current scores (regression guard) |
| `docs/guides/call-graph/test-strategy.md` | Replace fictional corpus API with this design |
| `tests/test_real_projects.py` | Re-label its asserts as "smoke" (existence) vs the new "accuracy" suite |

**Estimate:** Phase 1 ≈ 1 day (mostly careful ground-truth authoring + the runner/metrics). Phases 2–3 incremental.
