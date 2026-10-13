# ADR-0004: Closed edge-origin vocabulary and evidence kinds

- **Status:** Accepted (2026-10-13, M1.12.b — measured against the M0.12 baseline in `benchmarks/2026-10-13-edge-contract.md`)
- **Date:** 2026-10-08
- **Related items:** M0.12, M1.12, M3.4, M5.2, M6.2–M6.13, M7.3, M8.1–M8.4

## Context
`call_edges.resolved_by` already exists (`src/ai_discovery/db.py:71`). Its values are set in `src/ai_discovery/graph/call_graph.py:107-202`: `index`, `exact`, `import_scope`, receiver reasons, `interface_impl`, `short_name`, `short_name_community`, `unresolved`. The problems:
- **Some origins map to several confidences** (`short_name` 0.95–0.6).
- **Ambiguous names fan out** to up to 8 candidates (`_MAX_FANOUT`, `call_graph.py:15`).
- **Unresolved calls become edges** to a bare name at 0.5 (`call_graph.py:196-202`), and these flow into slices and BFS.

repowise's changelog shows two recurring classes of fixes. One is false edges from guessing (stdlib shadowing; bare-name tiers answering for the stdlib). The other is mixing signal kinds: co-change was served as a dependency (0.42.0). Its fix was one closed vocabulary with exactly one confidence per origin, plus *no* edge for ambiguity. See `docs/roadmap/research/edge-case-catalog.md`.

## Decision
1. **`origin` is a closed enum** with exactly one confidence each. It is defined once in `graph/origins.py` and stored in `call_edges.resolved_by` (M1.12.a kept the column name: the reader API already exposes it as `origin`, and a rename would touch every consumer for no behaviour change). Edge kind and evidence kind are stored in `call_edges.edge_kind` / `call_edges.evidence_kind` (migration 0003).

   | origin | conf | meaning |
   |---|---|---|
   | `scip` | 1.00 | precise index (SCIP/LSP export, Stage 0, `graph/symbol_index.py`) |
   | `same_file` | 0.95 | callee defined in the same file, unique |
   | `self_scope` | 0.95 | `self.`/`this.` method on the enclosing class |
   | `import_exact` | 0.95 | imported symbol, fully qualified match |
   | `receiver_typed` | 0.93 | receiver type known (field, param, local, `new X()`), method found on type |
   | `same_package` | 0.90 | Java same package / C# same namespace (incl. `global using`) / Python package |
   | `inherited` | 0.90 | method found on an ancestor (≤4 levels, single defining branch) |
   | `import_module` | 0.88 | module/alias import, member resolved |
   | `framework` | 0.90 | framework binding (route→handler, DI interface→impl, markup event→code-behind) |
   | `dispatch` | 0.75 | interface/base method → each override (refused entirely above 24 impls) |
   | `receiver_global` | 0.75 | receiver type known, type found repo-wide by unique name |
   | `global_unique` | 0.50 | bare name unique repo-wide, same language, not a builtin, callable |
   | `sql_regex` | 0.60 | table read/write edge from regex fallback when sqlglot cannot parse the statement (M6.8) |
   | `external_client` | 0.90 | call through a known client library (HTTP/queue/cache/payment SDK) to a synthesized external-system node; edge kind `calls_external`, evidence `framework` (added in M1.12.a) |

   This is the **only** canonical origin table. Other docs link here instead of copying it. Origin `framework` yields edge kind `framework_binds`; origin `dispatch` yields edge kind `dispatches_to`.

   - **Ambiguous** (more than one candidate after all tiers): **no edge**. The call is recorded in `unresolved_calls(scan_id, caller_id, callee_name, reason, candidate_count, file, line)`, with `reason` in `ambiguous|not_found|dynamic|builtin|external|dispatch_cap|di_ambiguous`.
   - **Short-name guards:** same language; not in the per-language builtin/stdlib list; target must be callable. A stdlib module name shadowed by a repo file never binds (repowise 0.53.0 lesson).
2. **`evidence_kind` enum** on every edge. Kinds are never merged into one confidence:
   `static` (parser + resolver) · `framework` (adapters) · `dynamic_hint` (reflection/DI strings: `Class.forName`, `Activator.CreateInstance`, `importlib`, Spring XML bean refs) · `scip` · `co_change` (git layer, M8.1) · `coverage` (per-test coverage, M8.2) · `runtime` (OTLP spans / profiler stacks, M8.3–M8.4).
   - Consumers choose which kinds they follow. The default for flows and slices: `static|framework|scip` with origin ≥ 0.75, depth-capped and cycle-safe.
   - Impact (M5.2) adds `dispatches_to` edges and `dynamic_hint` evidence as "may" edges and lists `co_change` separately.
   - "Not observed at runtime" is never negative evidence.
3. **Edge kinds** stay distinct from origins. Canonical list: `calls`, `dispatches_to`, `framework_binds`, `type_use`, `markup_event` (markup event → code-behind handler), `calls_proc` (code → stored procedure), `reads_table`, `writes_table`, `dynamic_hint`, `executes` (test → function, from coverage), `co_change`. Dispatch edges carry `evidence_kind=static`; `--include` filters select by `evidence_kind` only.

## Implementation notes (M1.12.a, 2026-10-13)
- Old values map as: `index`→`scip`; `exact`→`import_exact` (a markup page naming its code-behind handler or bean → `framework`); `import_scope`→`import_exact` (module matches the file) or `import_module`; `receiver_type`→`receiver_typed`/`receiver_global`; `interface_impl`→`framework`; `short_name` → `self_scope`/`same_file`/`same_package`/`global_unique`, or no edge; `short_name_community` and `unresolved` → no edge.
- A bare type name (constructor call `new X()`, DI constructor parameter) resolves as `receiver_typed`/`receiver_global` by the same locality rule as receivers.
- `call_edges.edge_type` (legacy: `direct_call`, `dispatch`, `di_binding`, `external_call`) is kept for the UNIQUE key; `edge_kind` is the ADR-0004 value.

- M1.12.b following rule: `origins.followable` — evidence `static|framework|scip`, edge kind `calls|dispatches_to|framework_binds|calls_external`, origin confidence ≥ 0.75 — used by execution slices, alternate paths and the entry-point linker; slices record one stop reason per stop (`Scenario.stop_reasons`).
- Grading-driven guards added in M1.12.b: a name bound by an import from outside the repo (`from flask_babel import get_locale`, `current_app.x.index()`) is `external`, never a same-named repo symbol; `super()`/`base` calls bind only to an ancestor; Python dunder methods are builtins; a method call on an object never binds to a repo class by name.

## Consequences
- **Expect recall to drop.** No fan-out and no unresolved edges will lower call recall. Precision should rise. M1.12 must report both against the M0.12 baseline and publish unresolved counts per language, never hide them.
- **Benchmarks can now be sliced** per origin (M6.13: 30 rows per language × origin).
- **Existing consumers must change:** `ExecutionSliceBuilder`, the entry linker, flow BFS and the viewer node page. They change in the same PR as M1.12.
- **The confidence values are a starting point.** Per-origin values are tuned from audit data, never per edge.
