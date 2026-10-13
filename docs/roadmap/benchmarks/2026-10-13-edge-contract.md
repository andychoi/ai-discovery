# Benchmark — M1.12 edge contract vs the M0.12 baseline (2026-10-13)

- **What changed:** ADR-0004 resolver (M1.12.a) plus M1.12.b guards (library imports, `super()`/`base`, dunders, no class targets for method calls) and following rules. Reference: [2026-10-11-baseline.md](2026-10-11-baseline.md).
- **Runs:** keyless `python -m bench run` on the active repos at their pinned SHAs; raw JSON in [`raw/m112-ai-discovery-keyless-*.json`](raw/).
- **Precision:** same protocol as M0.12 — seed 11, ≤30 edges per language × origin, graded **by Claude sub-agents reading the source at each call site** (not human-reviewed). Rows whose (caller, call, target) matched an M0.12-graded row reuse that verdict (marked `(M0.12)`); all other rows were graded fresh. Sheets: `bench/precision/sheets/*-seed11-m112.csv`; scores: [`raw/precision-seed11-m112.json`](raw/precision-seed11-m112.json).
- During grading, four resolver bugs were found and fixed before the final sample (library-imported names bound to repo symbols, `super().__init__` bound to a sibling class, method calls bound to repo classes, `base.X()` bound by name). The final sample was re-drawn on the fixed resolver.

## 1. Precision per language (edges with a target)

Per-language value = population-weighted mean of the per-stratum precision (stratified estimate). The old `unresolved` stratum (edges with no target) is excluded on both sides — those calls are now `unresolved_calls` rows, not edges.

| Repo | Language | M0.12 edges | M0.12 precision | M1.12 edges | M1.12 precision |
|---|---|---|---|---|---|
| spring-petclinic | java | 225 | 0.72 | 177 | 0.98 |
| eShop | csharp | 1,142 | 0.59 | 563 | 0.90 |
| microblog | python | 107 | 0.95 | 95 | 1.00 |
| node-express-realworld | javascript | 43 | 1.00 | 43 | 1.00 |

eShop also has a handful of JavaScript edges (1 now; the M0.12 sample had 1 graded `short_name` row): too few to estimate.

**Acceptance (precision ≥ M0.12 per language): met for all four languages.**

## 2. Precision per stratum (M1.12, Wilson 95% CI)

| Repo | Language × origin | n | Precision | 95% CI | Edges |
|---|---|---|---|---|---|
| petclinic | java · self_scope | 30 | 1.00 | 0.89–1.00 | 30 |
| petclinic | java · receiver_typed | 19 | 1.00 | 0.83–1.00 | 19 |
| petclinic | java · inherited | 8 | 1.00 | 0.68–1.00 | 8 |
| petclinic | java · global_unique | 30 | 0.97 | 0.83–0.99 | 120 |
| eShop | csharp · self_scope | 30 | 1.00 | 0.89–1.00 | 171 |
| eShop | csharp · dispatch | 30 | 1.00 | 0.89–1.00 | 58 |
| eShop | csharp · inherited | 13 | 1.00 | 0.77–1.00 | 13 |
| eShop | csharp · receiver_typed | 2 | 1.00 | 0.34–1.00 | 2 |
| eShop | csharp · receiver_global | 30 | 0.90 | 0.74–0.97 | 67 |
| eShop | csharp · global_unique | 30 | 0.83 | 0.66–0.93 | 169 |
| eShop | csharp · framework | 30 | 0.77 | 0.59–0.88 | 57 |
| eShop | csharp · external_client | 26 | 0.58 | 0.39–0.74 | 26 |
| microblog | python · import_exact | 30 | 1.00 | 0.89–1.00 | 36 |
| microblog | python · global_unique | 24 | 1.00 | 0.86–1.00 | 24 |
| microblog | python · external_client | 16 | 1.00 | 0.81–1.00 | 16 |
| microblog | python · self_scope / same_file / inherited / receiver_typed | 19 | 1.00 | — | 19 |
| node-express | javascript · global_unique | 30 | 1.00 | 0.89–1.00 | 37 |
| node-express | javascript · same_file | 6 | 1.00 | 0.61–1.00 | 6 |

Two strata did not improve:
- **csharp · framework 0.77** (old `interface_impl` 0.87, same 57 edges). Five of the seven wrong rows are calls in unit tests on NSubstitute mocks (`Substitute.For<IOrderRepository>()`), which the single-implementation rule binds to the real class. Test code is in the sample because the bench scans whole repos.
- **csharp · external_client 0.58** — unchanged extractor behaviour (`request`/`client` variable names matched as client libraries); not part of M1.12.

Remaining wrong `global_unique`/`receiver_global` rows in eShop mostly come from qualified-name collisions: the C# parser drops file-scoped namespaces, so `Extensions.AddApplicationServices` in two services share one qualified name and their call sites merge (parser follow-up). Java has the same effect for overloads (one qualified name per method name).

## 3. Recall and unresolved calls

| Repo | Edges M0.12 (resolved + bare 0.5) | Edges M1.12 | `unresolved_calls` rows | GT key-edge recall |
|---|---|---|---|---|
| spring-petclinic | 225 + 746 | 177 | 783 | 0.33 → 0.33 |
| spring-petclinic-local | 264 + 1,014 | 209 | 1,057 | — |
| node-express-realworld | 43 + 136 | 43 | 136 | 1.00 → 0.00 |
| microblog | 107 + 381 | 95 | 396 | 0.67 → 1.00 |
| eShop | 1,142 + 2,155 | 564 | 2,525 | — |

- Edge counts fall where fan-out and name-only guessing used to answer (eShop 1,142 → 564). The dropped calls are rows with a reason, not silence.
- **node-express-realworld recall 1.0 → 0.0 at the 0.7 bar:** both labelled edges are still produced and graded correct, but only as `global_unique` (0.50), because the JS parser gives arrow-function consts no imports or call sites. A parser fix (lane D) lifts them to `import_exact`; the relative-specifier resolution is already in place (M1.12.a).
- **Flows/slices** walk only edges ≥ 0.75 with static/framework/scip evidence, so `global_unique` edges (120 of 177 in petclinic) do not enter slices or entry-point links.

## 4. Caveats
- Graded by Claude, not by a human (owner decision); spot-check before treating as authoritative.
- Per-language weighting uses whole-repo origin counts; eShop's population is mixed C#/JS but JS has ≤1 edge.
- Reused M0.12 verdicts assume the same (caller, call, target) triple is equally right or wrong in both scans.
