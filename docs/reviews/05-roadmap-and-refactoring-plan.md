# Prioritized Enhancement Roadmap & Refactoring Plan

**Date**: 2026-07-12 · Reviewed at commit `d9d997b`

This is the implementation roadmap requested before any code changes. It sequences the findings from docs 02–04 by **impact ÷ effort**, groups them into workstreams with explicit dependencies, and defines the refactoring order so structural work lands before feature work that would otherwise be built on sand.

Effort scale: **S** = hours–1 day · **M** = 2–5 days · **L** = 1–3 weeks. Impact: cost / quality / velocity / trust.

---

## Guiding decisions (recommendations, recorded for the record)

1. **Fix trust first.** Exit codes, stop_reason handling, and the pricing table are cheap and protect everything downstream (CI, budget guard, generated-doc integrity).
2. **Refactor the pipeline before adding phases.** Every roadmap item below phase-level (batch mode, checkpointing 8b, parallel phase 13) is 3× cheaper after the phase-driver extraction.
3. **The graph is the single source of truth for extraction.** New analyzers consume `code_nodes`/`call_edges`/`db_relationship`; regex-over-files is only ever a fallback tier (the import-map precedent).
4. **Anthropic-direct (or Claude Platform on AWS) becomes the recommended cloud path.** Bedrock's InvokeModel path lacks Batches and lags features; keep it supported, stop treating it as primary.
5. **Docs are load-bearing (CLAUDE.md principle 2).** Every heuristic change in this roadmap carries a paired doc update; the drift found in review (doc 04, P1-7) gets fixed as its own item, not "later".

---

## Wave 0 — Trust & correctness fixes (1–2 days total, do immediately)

| ID | Item | Fixes | Effort |
|---|---|---|---|
| W0-1 | Define `_VALID_PUSH_MODES` (or reuse shared set); add a CLI smoke test that imports & `--help`s every subcommand | P0-1 | S |
| W0-2 | Non-zero exit + `status="failed"` when phase 6 yields zero nodes; non-zero exit on `budget_exceeded` (flag-gated if needed for compat) | P0-3, P0-4 | S |
| W0-3 | Read `stop_reason` in all transports; replace the 0.95-heuristic truncation guess; surface `refusal` explicitly | P0-5 | S |
| W0-4 | `MODEL_RATES`: add `fable`/`mythos` entries; prefer longest/exact match; log a warning when the default cloud rate is used | P0-6 | S |
| W0-5 | Replace `except Exception: pass` at `pipeline.py:1315` with logged warning; sweep sibling swallow sites for log-level sanity | P0-9 | S |
| W0-6 | Doc-embedder `doc_id`: include doc-type folder in the id (`asis/orders`); fix resume-count guard | P0-2 | S |
| W0-7 | Fix `_jaccard` empty-set divergence (pick one semantic, document it) | P0-8 | S |

## Implementation status log

Recorded as the roadmap is executed on branch `claude/architecture-review-roadmap-28vngf`.

- **Wave 0 — DONE** (commit `fix(wave0)`): W0-1…W0-7 all landed with 23 new tests. `discover ingest-docs` no longer crashes; zero-node and budget-exceeded scans exit non-zero (`ScanIncompleteError`); Bedrock + Anthropic transports read `stop_reason` (refusal → `LLMRefusalError`, truncation → warning); Fable/Mythos priced at $10/$50; doc-embedder `doc_id` folder-qualified; `_jaccard` empty-set convention aligned; resume-path swallow logged.
- **Wave 1 — PARTIAL** (this branch): W1-2 (adaptive thinking + per-tier `effort`, capability-gated so Haiku/OpenAI/Gemini/local are never sent effort; Fable omits `thinking`) and W1-6 (`extract_claims` → `invoke_structured`; `strip_thinking` scoped to local providers) are **done** with tests. The `system=` transport plumbing for W1-1 is **in place**. The remaining Wave 1 items — applying `cache_control` to tier prompts (W1-1), the Batches execution mode (W1-3), the unified cost ledger (W1-4), the retry-helper/config-freeze (W1-5), the live Fable A/B pilot (W1-7), and tier-3 streaming (W1-8) — are **deferred**: each needs a live Anthropic key to validate meaningfully (cache-hit metrics, batch submission, refusal-fallback behavior), which this environment lacks. They are specced below and ready to implement against a keyed environment.
- **Wave 2 — IN PROGRESS** (this branch): W2-3 (shared primitives) **done** — new `graph/util.py` holds one `UnionFind`, one `jaccard` (textbook empty-set convention), and a deque `bfs_layers`; `call_graph._file_communities`, `fsm_identity` (its int `_UnionFind` + `_jaccard`), and `federation` all migrated to them. Federation's greedy first-fit grouping is replaced with real union-find, fixing the non-transitive 3+-repo merge (P0-7). W2-6 (O(n²) BFS) **done** — `build_scenario` and `_detect_alternate_paths` now use `deque` with enqueue-time visited marking (P2-2). W2-5 (generator dedup, partial) **done** — DMN/EARS share `generators/fsm_rules.py`. W2-4 (parser dedup) **done** — the four byte-identical tree-sitter helpers (`matches`/`captures`/`extract_call_sites`/`inside_class`) now live in `parsers/_ts.py`; all four AST parsers compose them instead of carrying private copies. Scope note: this is a **shared-helpers module, not a base-class hierarchy** — a lower-risk realization of the same de-duplication. `_extract_state_transitions` and `_detect_boundaries` are deliberately **left per-parser**: on close inspection they vary on real per-language *semantics* (self-receiver keywords, absent-receiver defaults, a Python-only class guard and `or ""` fallback, JS's backtick strip; boundary case-folding, parent node type, exact-vs-substring client match), so consolidating them safely needs a dedicated equivalence-tested pass rather than a many-parameter "generic". +21 tests; corpus-accuracy regression gate still green. Remaining Wave 2 — the phase-driver extraction (W2-1), DAO layer (W2-2), `embed_docs` upgrade (W2-5 remainder), parallel phase 13 (W2-7) — sequenced next; W2-1 is large enough to warrant its own focused, incrementally-shippable pass.

## Wave 1 — LLM cost & quality (the biggest ROI in the codebase)

*Independent of the pipeline refactor; can run in parallel with Wave 2.*

| ID | Item | Impact | Effort | Depends |
|---|---|---|---|---|
| W1-1 | **Prompt caching**: add `system=` to transports; split every tier's prompt into stable system prefix (instructions/schema, `cache_control`) + volatile user content; keep prefixes byte-stable | Cost ↓↓ (up to ~80% on cached input) | M | — |
| W1-2 | **Adaptive thinking + per-tier `effort`** on Anthropic/Bedrock transports (tier1 `low`, tier2 `high`, tier3 `high/xhigh`); Fable special-case (omit `thinking`) | Quality ↑↑ on tier-3 synthesis; tier-1 latency ↓ | S | W0-3 |
| W1-3 | **Batches API mode** (`--batch`) for phase 11 and phase 17 claim verification on the anthropic provider; poll-and-resume via existing checkpoints | Cost ↓ 50% on batched phases | M | W0-3; easier after W2-1 |
| W1-4 | Unify cost accounting: router usage hook becomes the single ledger; `invoke_structured` routes through it; derive advisor cost checks from `rates_for_model` | Trust/velocity | M | — |
| W1-5 | Single retry helper (botocore error codes + httpx status, not string matching); freeze router config per run (kill per-call `configure()`) | Robustness; latent race removed | S | — |
| W1-6 | Port `extract_claims` to `invoke_structured`; scope `strip_thinking` to Ollama-like providers | Correctness hygiene | S | — |
| W1-7 | **Fable 5 pilot on `anthropic.tier3p`** with server-side fallbacks to Opus 4.8; A/B vs Opus 4.8 on 2–3 corpus repos using `generated_docs.confidence`, triage-report deltas, and human spot-checks; adopt only if the delta justifies 2× pricing | Quality ceiling | S (after prereqs) | W0-3, W0-4, W1-2 |
| W1-8 | Streaming + `max_tokens` ≥ 16–32K for tier-3; `count_tokens` for budget-guard inputs on Anthropic | Robustness | S | W1-2 |

## Wave 2 — Structural refactoring (velocity multiplier)

| ID | Item | Impact | Effort | Depends |
|---|---|---|---|---|
| W2-1 | **Phase driver**: `Phase` protocol (`run()`, `load_from_db()`, optional `rebuild()`), driver loop owning skip/resume/checkpoint; convert phases mechanically one at a time (each conversion independently shippable); make 8b its own checkpointed phase; phase numbers → `int` | Velocity ↑↑; resume cost ↓ | M–L | W0 |
| W2-2 | **DAO layer**: move all inline SQL from `pipeline.py` into `db.py` functions; `executemany` for node persist; apply `retry_on_locked`; single node-persist function (kills the divergent duplicate INSERT) | Velocity; DB perf | M | W2-1 (natural pairing) |
| W2-3 | **Shared primitives**: `graph/util.py` with `UnionFind`, `jaccard`, `normalize_stem` (+ one suffix list), deque-BFS helper; `graph/confidence.py` constants table; migrate the five call sites; federation merge switched to real union-find | Correctness (P0-7/8), tunability | M | W0-7 |
| W2-4 | **`TreeSitterParser` base**: hoist `_matches/_captures`, state-transition, boundary, call-site, field-type, class-fields/bases algorithms behind a per-language node-type/field-name map; parsers shrink ~40–50%; single parser registry consumed by lang_detector/file_walker/pipeline | Extensibility ↑↑ | L | — |
| W2-5 | Generator dedup: shared `transition_rules()` for DMN/EARS; shared `cap_unverified()`; shared frontmatter builder; port `embed_docs` onto the `embed_chunks` machinery (hashing/retry/batch) | Maintainability; embed cost ↓ | M | — |
| W2-6 | O(n²) hotspot fixes: deque-BFS in `build_scenario`/`_detect_alternate_paths`; suffix index for `_suffix_matches`; bucket the FSM cross pass | Scale ceiling ↑ | S–M | W2-3 |
| W2-7 | Parallelize phase 13 with the same ThreadPool pattern as 11/17 (per-future timeouts, cancel-on-shutdown, copied from self_review's good pattern) | Wall-clock ↓ on scenario-heavy repos | S | W2-1 helps |

## Wave 3 — Product capability (after the floor is solid)

| ID | Item | Impact | Effort | Depends |
|---|---|---|---|---|
| W3-1 | **Re-platform `screen_mapper` on the graph** (P1-2): controllers/services/tables/jobs from `code_nodes` + `call_edges` + `db_relationship`; delete the regex sub-analyzers or demote to fallback; makes screen mapping language-neutral (C#/Python/JS backends) | Screen-spec quality ↑↑ on non-Java stacks | L | W2-2/W2-3 |
| W3-2 | Same treatment for `entity_service_resolver` | Consistency | M | W3-1 |
| W3-3 | Import-map tier upgrades: populate `calls`, add opt-in endpoint/entity extraction for Go (the most-requested next language), or promote Go to tree-sitter via the new base (cheap after W2-4) | Language coverage | M | W2-4 |
| W3-4 | FSM confidence redesign: derive from evidence breadth (trigger-site count, guard coverage) instead of the constant-1.0 mean; populate `DriftResult.new_files` | Trust signal becomes real | S–M | — |
| W3-5 | BPMN lane binding (`flowNodeRef`), dashboard/generator artifact-path reconciliation, offline-push naming alignment | Output polish | S each | — |
| W3-6 | Test-gap closure: `chat` pure functions, `doc_embedder`, `dual_search`, viewer HTTP path-traversal tests, root `conftest.py` | Regression safety | M | — |
| W3-7 | **Doc-drift repair sprint** (P1-7): reconcile stage counts (pick "6-stage" everywhere), delete the phantom normalization, rewrite `extension-checklist.md` against the real API (after W2-4 so it documents the new base), fix `domain_classifier` docs *or* implement the documented behavior — an explicit decision, recorded in `decisions.md` | Onboarding trust | M | ideally after W2-4 |
| W3-8 | Scale beyond one process: only if real corpora demand it — streaming node iteration per phase, connection reuse, optional per-domain sharding. Do **not** pre-build this | Scale | L | measure first |

## Suggested sequencing (single maintainer, ~1 quarter)

```
Weeks 1     : Wave 0 (all)                        ← trust floor
Weeks 2–4   : W1-1..W1-6 (LLM cost/quality)       ← pays for the rest
Weeks 4–7   : W2-1..W2-3 (pipeline + primitives)  ← velocity multiplier
Week  7     : W1-7 Fable pilot (prereqs now met), W1-8
Weeks 8–10  : W2-4 parser base, W2-5..W2-7
Weeks 11–13 : W3-1 screen mapper re-platform, W3-4..W3-7 as filler
```

Rationale for the order: Wave 0 is risk-free and restores trust in exit codes and budgets; Wave 1 changes the economics of every subsequent test run (cheaper scans = faster iteration on everything else); Wave 2 then makes each later change smaller. The Fable pilot deliberately sits *after* thinking/effort support so the comparison is fair. W3-1 is the largest single quality lever for the product's newest feature (screen-centric specs) but is sequenced late because doing it against the current pipeline structure would double its cost.

## What I deliberately did **not** recommend

- **A web framework for the viewer** — the stdlib server is small, careful, and local-only; a framework adds surface without need. (Add tests instead.)
- **An external vector DB** — sqlite-vec fits the single-file, portable posture; the embedder is already incremental.
- **Async/await rewrite of the LLM layer** — ThreadPool + Batches API covers the concurrency need at far lower risk; revisit only if provider fan-out becomes per-call heterogeneous.
- **Replacing heuristics with LLM calls** (e.g. domain classification, entity identity) — the deterministic passes are the cheap, testable backbone; the corpus harness can regression-gate them. Keep LLMs for prose and inference, not for what string algorithms do reliably.
- **Reintroducing process mining** — the removal rationale stands until real runtime event logs exist.
