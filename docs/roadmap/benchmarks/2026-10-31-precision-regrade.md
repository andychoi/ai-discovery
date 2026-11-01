# Re-graded precision samples after M6.2/M6.3 (M6.14)

- **Date:** 2026-10-31 · **Item:** M6.14 · **Repos:** spring-petclinic `753d35c2f844`, eShop `dc7ea499cd35` (pins in `bench/repos.toml`)
- **Why:**
  - The M6.2 (C#) and M6.3 (Java) resolver work moved several origins' edge counts by more than 20% since the M1.12 sample.
  - On those moves the gate warned that the sample no longer represented the stratum.
- **Sheets:** `bench/precision/sheets/<repo>-<sha12>-seed12-m614.csv`, named with their recorded runs (`raw/m614-ai-discovery-keyless-<repo>.json`) in `bench/precision/sheets/CURRENT.json`, which the gate now reads.
  - The M1.12 sheets stay as history.
  - microblog and node-express-realworld did not drift, so they keep their seed-11 sheets.
- **Verify:** `python -m bench gate --external` → `gate: ok`, with no drift warnings on any repo.

## Method

1. Fresh keyless scan of each repo at its pin. These are the new recorded runs.
2. **Which strata were re-sampled:** those whose emitted-edge count moved more than 20% from the recorded M1.12 run.
   - **spring-petclinic:** framework 0→2, global_unique 120→20, inherited 8→62, receiver_typed 19→113.
   - **eShop:** external_client 26→33, receiver_global 67→22, receiver_typed 2→63.
   - Each re-sampled stratum got a new seeded sample (seed 12, ≤ 30 edges per stratum).
   - Every other stratum kept its graded rows.
3. **Grading:** every new row was graded from source, with a reason (prefix `(M6.14)`).
   - The source at the caller's line, the receiver's declaration and the target definition were read for each row.
   - If an edge was already graded in the seed-11 sheet (same caller file:line and same target file:line), that verdict was carried over, and its reason says so. This applies to 31 petclinic rows and 42 eShop rows.
4. **Graded by:** the coding agent (Claude, session of 2026-10-31), from source, with the same rules as M1.12. No human spot-check yet. Treat the after-numbers as **(agent-graded)** until an owner re-grades a 10% subsample.

## Results: per-stratum graded precision (Wilson 95% CI)

The *Before* column is the M1.12 sample, graded on the pre-M6.2/M6.3 graph. The *After* column is the new sample, graded on the current graph.

### spring-petclinic (Java)

| Origin | Before (seed 11) | After (seed 12) | Sample |
|---|---|---|---|
| framework | — (0 edges) | 0/2 = 0.000 [0.00, 0.66] | re-sampled |
| global_unique | 29/30 = 0.967 [0.83, 0.99] | 18/20 = 0.900 [0.70, 0.97] | re-sampled |
| inherited | 8/8 = 1.000 [0.68, 1.00] | 30/30 = 1.000 [0.89, 1.00] | re-sampled |
| receiver_typed | 19/19 = 1.000 [0.83, 1.00] | 30/30 = 1.000 [0.89, 1.00] | re-sampled |
| self_scope | 30/30 = 1.000 [0.89, 1.00] | 30/30 = 1.000 [0.89, 1.00] | kept |

### eShop (C#)

| Origin | Before (seed 11) | After (seed 12) | Sample |
|---|---|---|---|
| dispatch | 30/30 = 1.000 [0.89, 1.00] | 30/30 = 1.000 [0.89, 1.00] | kept |
| external_client | 15/26 = 0.577 [0.39, 0.74] | 20/30 = 0.667 [0.49, 0.81] | re-sampled |
| framework | 23/30 = 0.767 [0.59, 0.88] | 23/30 = 0.767 [0.59, 0.88] | kept |
| global_unique | 25/30 = 0.833 [0.66, 0.93] | 25/30 = 0.833 [0.66, 0.93] | kept (0.926 among graded edges still emitted) |
| inherited | 13/13 = 1.000 [0.77, 1.00] | 13/13 = 1.000 [0.77, 1.00] | kept |
| receiver_global | 27/30 = 0.900 [0.74, 0.96] | 19/22 = 0.864 [0.67, 0.95] | re-sampled |
| receiver_typed | 2/2 = 1.000 [0.34, 1.00] | 30/30 = 1.000 [0.89, 1.00] | re-sampled |
| self_scope | 30/30 = 1.000 [0.89, 1.00] | 30/30 = 1.000 [0.89, 1.00] | kept |

**Reading:**
- The typed origins that M6.2/M6.3 grew (receiver_typed, inherited) are now measured on 30-edge samples, and both are 30/30.
- eShop receiver_typed previously had only 2 edges.
- Every stratum overlaps its before-CI. Nothing regressed beyond sampling noise.

## Wrong edges found (to-fix list)

| Repo / origin | Edges | Cause | Follow-up |
|---|---|---|---|
| petclinic / framework | 2 of 2 | The test's `validator` is `jakarta.validation.Validator`. `PetValidator` implements Spring's *different* `Validator`, but implementations were matched by the short interface name. | Match implementations by the resolved interface (import-qualified), not by the short base name. |
| petclinic / global_unique | 2 of 20 | `PropertySource.getName()` (a Spring type) was linked to `NamedEntity.getName`: a global-unique guess on a receiver whose declared type is foreign. | Apply the C#/Java foreign-declared-receiver rule to local variables typed by a foreign import, too. |
| eShop / external_client | 10 of 30 | A variable or parameter named `request` (MediatR command, DTO, incoming `HttpContext.Request`) was read as the `request` HTTP library. These are the same edges graded wrong in M1.12. | External-client detection for C# should require a client type (`HttpClient`, `GrpcChannel`…) on the receiver, not a name. |
| eShop / receiver_global | 3 of 22 | `BeginAnimation` linked to an unrelated `Triggers` type. ClientApp's own generated copy of basket.proto was linked to the Basket.API copy. These were already graded wrong in M1.12. | Prefer the caller's project for duplicate generated types. |

These edges stay listed under `known_wrong_still_emitted` in the gate report. The gate does not fail on them; it tracks them.

## Follow-up: M6.15 (same day)

Every to-fix row above was addressed in M6.15. The drifted strata were re-sampled with seed 13: eShop external_client, and petclinic global_unique and framework. The sheets are `*-seed13-m615.csv` and are named in `CURRENT.json`. All 33 re-sampled edges had already been graded.

| Repo / origin | Before M6.15 | After |
|---|---|---|
| eShop / external_client | 20/30 = 0.667 | 21/21 = 1.000 (the 10 wrong `request` edges are no longer emitted) |
| eShop / receiver_global | 19/22 = 0.864 | 0.95 among graded edges still emitted (the proto copies bind to the caller's own file) |
| petclinic / global_unique | 18/20 = 0.900 | 12/12 = 1.000 |
| petclinic / framework | 0/2 | no edges emitted |

**Still open:** `BeginAnimation` (eShop receiver_global, 1 edge) and the `framework` rows graded wrong for NSubstitute mocks (7). The mock rows belong to M6.7, the DI adapters item.

