# Design: Call-Graph & Source Memory Scaling (P0-3)

**Date:** 2026-05-31
**Status:** Lever 1 implemented; Levers 2–3 specified (not yet built)
**Origin:** `docs/assessments/06-architecture-production-readiness-audit.md` — P0-3 ("100k-class / 500k-method in-memory OOM + quadratic short-name fan-out").

---

## Problem

On a large enterprise corpus (target: 100k classes, 500k methods, 20 microservices) the pipeline holds the entire parsed graph — every `CodeNode` carrying its full `source_code` string — in one in-memory `all_nodes` list for the whole run, and the call-graph resolver can emit an edge to *every* candidate on a short-name collision. Two failure modes:

1. **Memory (OOM).** `all_nodes` with full source is the dominant sustained term. It is held from phase 6 (parse) through phase 19 (finalise), alongside chunks, embeddings, and LLM buffers that accumulate in the later phases. Evidence: `CodeNode.source_code` set by every parser (`java.py:193,251`, etc.); `all_nodes` built at `pipeline.py:549-594` and still referenced at `pipeline.py:1605`.
2. **Quadratic edges.** On a colliding short name (`save`/`execute`/`handle`), the resolver fanned out a 0.6-confidence edge to every candidate (`call_graph.py` `_resolve_contextual_targets`, final branch) — O(k) edges per call site, quadratic across the codebase.

The call-graph *build* itself never reads `source_code` (verified by grep across `call_graph.py`), so source is pure dead weight to graph construction — it matters only to parsing-adjacent consumers.

---

## Source-code lifecycle (evidence)

Every in-memory consumer of `node.source_code`, in pipeline order:

| Phase | Consumer | Reads source? | Cite |
|-------|----------|---------------|------|
| 6 parse | parsers produce nodes | writes | `parsers/*.py` |
| 7 domain_classify | `classify_domains(all_nodes)` | no | `pipeline.py:672` |
| 7 | persist to `code_nodes` (incl. `source_code`) | writes DB | `pipeline.py:679-705` |
| 7 | `build_call_graph(all_nodes)` | **no** | `graph/call_graph.py` |
| 7 | `extract_external_systems(all_nodes)` | yes | `external_system_extractor.py:134` |
| 8 (inline) | `extract_sql_entities` / `extract_relationships` | yes | `sql_extractor.py:126-195` |
| 9 chunk | `chunk_code_nodes(all_nodes)` | yes (**last reader**) | `chunker.py:114` |
| 10–19 | embed / tier1-3 / visual / self-review / render | no (use chunks, summaries, DB) | — |

**Key fact:** the chunker (phase 9) is the *terminal* in-memory consumer of `source_code`. After phase 9, no `all_nodes` consumer reads it, and it is durably persisted in `code_nodes`.

---

## Phased design

### Lever 1 — Release source after chunking *(implemented)*

After phase 9, set `node.source_code = ""` across `all_nodes`. Source remains in `code_nodes`; chunks already captured their text (Python strings are immutable, so a chunk's `.text` is independent of the node after slicing/concat).

- **Impact:** removes the source bulk for phases 10–19 — the LLM-heavy long tail where embeddings + LLM buffers pile up, i.e. where OOM most often bites.
- **Risk:** ~none. No downstream `all_nodes` consumer reads source; idempotent, so `--resume` (which rebuilds chunks then releases) stays consistent.
- **Code:** `pipeline._release_node_source()`, called after the phase-9 chunk block. Tests: `tests/test_pipeline_memory.py`.

### Lever 2 — Stream the chunker from the DB; release source after phase 8 *(specified)*

The chunker both reads all source *and* duplicates source slices into chunk objects — a transient doubling at phase 9. Replace `chunk_code_nodes(all_nodes)` with a DB-streaming variant: iterate `code_nodes` via a server-side cursor (one file/batch at a time), chunk, persist chunks, discard. Then move the source release to the end of phase 8 (after the SQL/external extractors, which still need source).

- **Impact:** removes the chunk-time source duplication and frees source one phase earlier (9→8). Caps phase-9 peak at one batch of source + its chunks.
- **Risk:** moderate. Changes the chunker's input contract (nodes-with-source → DB cursor); must preserve chunk identity/ordering for embedding resume (`embedder.py` content-hash keys are stable, so order-independence holds).
- **Touch points:** `ai/chunker.py` (add `chunk_code_nodes_from_db(db_path, scan_id)`), `pipeline.py` phase 9, move `_release_node_source` to phase 8.

### Lever 3 — Persist-and-strip per file during parse; DB-back the extractors *(specified)*

Bound the phase 6–8 peak too. As each parse future completes: run the per-file SQL/external extraction, persist the file's nodes (with source) to `code_nodes`, then strip source before extending `all_nodes`. The SQL/external extractors move from "operate on `all_nodes` with source" to either (a) per-file during parse, or (b) DB-backed batch reads.

- **Impact:** `all_nodes` never holds more than one in-flight batch of source at any time → peak memory bounded by `max_concurrent` batches, not corpus size. This is the only lever that makes the *parse* peak independent of corpus size.
- **Risk:** larger. Reorders work currently split across phases 6/7/8 into phase 6; must keep phase checkpoints meaningful and the inline "8a/8b" FSM work (see audit) coherent. Best done together with promoting 8a/8b to real phases.
- **Touch points:** `pipeline.py` phases 6–8, `extractors/sql_extractor.py`, `extractors/external_system_extractor.py`.

### Companion — Edge blow-up cap *(implemented under P0-3)*

`_MAX_FANOUT = 8` in `call_graph.py`: ambiguous short-name / suffix sets above the cap collapse to a single Stage-5 *unresolved* edge instead of N×0.6 edges. Removes the quadratic edge term; preserves the "ambiguous call" signal at the raw call name. Tests: `tests/test_call_graph.py::test_short_name_fanout_is_capped_*`.

---

## Recommendation & sequencing

1. **Ship Lever 1 + the fan-out cap now** (done) — they are safe and remove the two cheapest-to-fix dominant terms (sustained source, quadratic edges).
2. **Lever 2 next** when a corpus large enough to OOM at phase 9 appears — it is self-contained (chunker + one release-point move).
3. **Lever 3 only if the parse peak itself OOMs** — it is the largest change and is best bundled with promoting the inline 8a/8b stages to formal, checkpointed phases (a separate audit finding).

## What is explicitly *not* solved here

- **SQLite-backed call-graph traversal.** For 500k methods the `edges` list + resolution indexes are themselves large (millions of `CallEdge`). The fan-out cap shrinks this, but a truly graph-scale design would persist edges and resolve in SQL. Out of scope for P0; revisit if edge memory dominates after Levers 1–3.
- **Cross-service / cross-language resolution.** Tracked separately (audit §2 missing-requirements).

---

## Validation

- Lever 1: `tests/test_pipeline_memory.py` (release empties source, is idempotent, chunks retain text after release).
- Fan-out cap: `tests/test_call_graph.py` (cap collapses to one unresolved edge; small collisions still resolve).
- Full suite green after changes.
