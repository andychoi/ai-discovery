# Complex Algorithm Analysis

**Date**: 2026-07-12 · Reviewed at commit `d9d997b`

The intellectually hard parts of this system are concentrated in four algorithms. This document explains each as implemented (with file:line anchors), assesses correctness and complexity, and flags divergences from the documented design. Findings feed the risk register (doc 04) and roadmap (doc 05).

---

## 1. Call-graph resolution (`graph/call_graph.py`) — Area A

The core differentiator. A two-pass, first-match-wins cascade of **six stages (0–5)** producing graded-confidence edges:

**Pass 1 — per call-site** (`call_graph.py:77-172`), against four prebuilt indices (qualified names, short names, DI field types, interface→implementations):

| Stage | Mechanism | Confidence |
|---|---|---|
| 0 | `SymbolIndex.resolve()` — authoritative compiler-grade index when available | 1.00 (`resolved_by="index"`) |
| 1 | Exact qualified-name match | 1.00 (`exact`) |
| 2 | Import-scoped: receiver head matched against the caller's file imports | 0.95 (module⊆path) / 0.85 |
| 3 | Receiver-type / DI: declared field/param type of `receiver.method()` | 0.93 (`receiver_type`); single interface→impl 0.90 (`interface_impl`); multiple impls → defer (no guess) |

**Pass 2 — contextual, over deferred calls** (`_resolve_contextual_targets`, `call_graph.py:419-489`):
same-class 0.95 → same-file 0.90/0.75 → same-module 0.85/0.70 → **community narrowing** 0.80/0.70 → single global candidate 0.80 → prefix-overlap 0.75/0.65 → residual fan-out 0.60 → **stage 5 unresolved 0.50**.

Ties are not broken — they **fan out** as parallel edges capped at `_MAX_FANOUT = 8`; above the cap the call collapses to one 0.50 unresolved edge. Dedup keeps the max-confidence edge per `(caller, callee, edge_type)`.

**Assessment.** The graded-fan-out design is the right call for downstream consumers that filter by confidence (BPMN needs ≥0.65; tech specs need human review below that) — it preserves recall without lying about precision. The DI/receiver-type stage with an explicit *refuse-to-guess* rule for multi-impl interfaces is what makes the corpus `score_forbidden_edges` guarantee possible.

**Findings:**

1. **"Community narrowing" is misnamed.** `_file_communities` (`call_graph.py:381-416`) is union-find **connected components** over edges with confidence ≥ 0.93 — no Louvain, no modularity. (Louvain exists, but in Tier-1 semantic batching.) The docs and commit message (`6898e37`) imply community *detection*; rename or re-document.
2. **Comment/mechanism mismatch on the community feed set.** The comment says "stages 0–3 qualify" but the gate is numeric `≥ 0.93` (`call_graph.py:408`) — which silently *excludes* `interface_impl` (0.90) and cross-file `import_scope` (0.85) edges that are nominally stage-3/2. Either the threshold or the comment is wrong; pick one.
3. **O(n²) BFS.** `build_scenario` uses `queue.pop(0)` on a Python list with the visited-check on pop rather than on enqueue (`call_graph.py:623-656`); `_detect_alternate_paths` repeats it (`call_graph.py:862`). Meanwhile `entry_point_linker._bfs_entries` correctly uses `deque`. On dense graphs the queue grows super-linearly. Straightforward fix: `deque` + enqueue-time visited set, shared as one traversal helper.
4. **`_suffix_matches` scans every node per pending dotted call** (`call_graph.py:510`) — O(pending × nodes). A reversed-suffix index would remove the hotspot.
5. **The read-after-write signal is decorative.** `_score_node` claims to check whether "this node's source mentions a field" written earlier, but actually substring-matches field names against the node **name** (`call_graph.py:697-703`); the `file_path` gate is never used in the match. It fires on coincidences and misses real cases. Either implement it against chunk source or delete the signal and its documentation.
6. **Node ranking scores are raw sums (0–~17), not normalized 0–1** — `decisions.md` MED-1 admits the normalization "was never implemented" yet the same document's table footer and `resolution-heuristics.md:277-281` still document it as live. Since `ExecutionNode.confidence` is only used ordinally, fix the docs (cheaper) or normalize (cosmetic).

## 2. FSM identity consolidation (`graph/fsm_identity.py`) — the subtlest algorithm

Purpose: the same business entity appears as `Order`, `OrderDTO`, `OrderService`-observed transitions, `orders` (SQL) — consolidation must merge these without merging `Order` and `OrderLine`. The pipeline: mixin/generic pre-filter → fingerprint (field-set minus ORM-meta minus mixin-inherited fields + normalized stem) → pair scan within 3-char stem buckets (classful Jaccard ≥ 0.9 ∧ size-ratio ≥ 0.7; relaxed Jaccard ≥ 0.7 ∧ stem-sim ≥ 0.8; asymmetric subset → *projection link* not merge) → classful×classless cross pass → union-find collapse (rank + path compression) → canonical pick (unsuffixed real class > real class > shortest) → display-name disambiguation with module hints.

**Assessment.** Thoughtful and defensively designed (inheritance-ancestor pairs hard-excluded; projections distinguished from identity). Three findings:

1. **The cross pass is O(n²) over *all* fingerprints** (`fsm_identity.py:198-214`) while the module docstring advertises only bucketed O(n·k). On large entity counts this dominates; bucket the cross pass the same way.
2. **Stem similarity is `difflib.SequenceMatcher`** self-described as "close enough to Jaro-Winkler" (`fsm_identity.py:722`) — Gestalt and Jaro-Winkler diverge on transposition/prefix-heavy identifiers; the 0.8 thresholds were presumably tuned against one of them. Pin the choice deliberately and document it.
3. **FSM confidence is near-constant.** FSM confidence = mean of transition confidences (`fsm_rollup.py:158-162`), but per HIGH-9 every extracted transition is 1.0 — so the field carries almost no signal (SQL placeholders at 0.7 are the exception). Either derive FSM confidence from evidence breadth (distinct trigger sites, guard coverage) or stop displaying it as if it discriminates.

**Related divergence — `federation._merge_fsms_across_repos` is *not* union-find** despite its docstring (`federation.py:176-205`): it is greedy first-fit grouping, order-dependent and non-transitive (A~B, B~C, A≁C can split C). For 3+ repo federation this is a genuine correctness gap; reuse the `fsm_identity._UnionFind`.

## 3. Louvain semantic batching (`ai/semantic_batching.py`) — the best-engineered new algorithm

Groups Tier-1 chunks by call-graph community so one LLM call summarizes code that references itself: collapse confidence-weighted call edges into a weighted file graph → `networkx` Louvain (fixed seed 42 for reproducibility) → cap batches (10 chunks / 12k tokens) → pool sub-minimum communities into "misc" batches (avoids the 87-singleton problem observed upstream) → **loud deterministic fallback** to domain/path grouping on any clustering failure.

**Assessment: keep as the template.** Deterministic seeding, explicit caps, singleton consolidation, and a fail-loud-degrade-gracefully posture — this is what the other heuristic modules should look like. Two minor notes: token estimates are `len//4` (fine for capping), and batch quality is unmeasured — a cheap A/B (batched vs unbatched summary confidence) would validate the premise.

## 4. Screen detection & mapping (`menu_detector.py` + `screen_mapper.py`)

**Menu detection** (`HybridMenuDetector`, `menu_detector.py:419-455`): fixed-priority first-match-wins over five formats (JSON/YAML menu files → TS/JS menu constants → Vue/React/Angular routers → WebForms folder hierarchy → JSP folder hierarchy), with an explicit "no format matched, tried: …" diagnostic instead of a silent 0-screen run. Sound design; the priority order correctly prefers declared menus over inferred ones.

**Screen mapping** (`ScreenMapper.map_screen`, 11 steps, 1,240 lines) is the weak half:

- Steps 8–10 are entire sub-analyzers (orphan-table detection, ETL/batch-job detection, API injection points, a 130-line `_find_external_interfaces` of `if "KafkaTemplate" in content` checks) that **re-open and regex source files the tree-sitter parsers already parsed**, duplicating `sql_extractor`/`external_system_extractor`/endpoint logic with less rigor.
- It is **hard-wired to Java/Spring** (`src/**/*Controller.java` globs at `screen_mapper.py:349,803,1100`) — C#/Python/JS backends silently produce no controllers/services/tables regardless of what phase 6 extracted.
- Its own docstring admits steps 3–4 are "placeholder"/"skeleton" (`screen_mapper.py:583-608`).

**Recommendation** (roadmap R3): re-platform `map_screen` on the persisted graph — `code_nodes` (endpoints, db_models), `call_edges`, `db_relationship` — and keep regex only as a last-resort fallback tier like `import_map` is for parsing. Drift hashing (`_compute_source_hashes` + `drift_checker.py`) is clean and correct; note `DriftResult.new_files` is modeled but never populated.

## 5. Cross-cutting algorithmic observations

- **Confidence constants are scattered magic numbers** (0.93 community cutoff, 0.75 denorm links, 0.85 integration edges, per-branch entity-classifier confidences 0.4–0.95) with no central tuning table, while the guides imply `discovery.yaml`-driven weights that nothing reads. A single `graph/confidence.py` constants module would make tuning and documenting honest.
- **Duplicated primitives with divergent semantics** — the most dangerous kind of duplication found: two `_jaccard`s where empty∩empty = 0.0 in `fsm_identity.py:713` but = 1.0 in `federation.py:366`; three `normalize_stem` variants (fsm_identity/entity_correlator/federation) with different suffix lists; two union-finds + one greedy impostor; two BFS implementations. Any future "unify these" change is a correctness trap until semantics are reconciled.
- **The deterministic/statistical split is healthy**: entity classification, guard parsing, prose validation, and process statistics are all deliberately LLM-free and cheap to re-run, and the honest removal of pm4py (its fitness metrics were fabricated on synthetic single-trace logs) shows good judgment about what statistics are meaningful on statically inferred data.
