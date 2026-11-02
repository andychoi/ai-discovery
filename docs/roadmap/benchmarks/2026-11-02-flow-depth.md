# Flow tracer bounds: depth 5 → 8, 50 flows per entry point (M6.17)

- **Date:** 2026-11-02
- **Item:** M6.17 (design/graph-accuracy.md §8)
- **Change:** execution slices trace up to 8 calls deep (was 5). At most 50 flows (entry-to-stop paths) per entry
  point are traced to their end. The frontier left after that stops with the new reason `flow_cap` and is not
  expanded.
- **Repos:** the four pinned bench repos at their `bench/repos.toml` SHAs: microblog `a975ef6`,
  node-express-realworld `30b68e1`, spring-petclinic `753d35c`, eShop `dc7ea49`. Each is a keyless scan
  (`discover scan --full --no-prose`).
- **Reproduce:** `python -m bench.flow_depth <dir>`. Raw numbers: `raw/m617-flow-depth.json`.

## Results

"Tier-2 input" is the estimated input tokens of the three scenario-flow prompts (steps, IPO, interfaces) over all
entry points: 4 chars per token, plus 40 tokens of Tier-1 summary per node and prompt. It is an estimate, not a
billed number.

| Repo | Entry points | Bounds | Slice nodes (total / median / max) | Most flows from one entry | Depth-capped | Flow-capped | Tier-2 input |
|---|---|---|---|---|---|---|---|
| microblog | 40 | depth 5 | 89 / 2 / 5 | 3 | 0 | 0 | 40,008 |
| | | depth 8 + cap 50 | 89 / 2 / 5 | 3 | 0 | 0 | 40,008 |
| node-express | 21 | depth 5 | 92 / 4 / 12 | 5 | 0 | 0 | 32,674 |
| | | depth 8 + cap 50 | 92 / 4 / 12 | 5 | 0 | 0 | 32,674 |
| petclinic | 18 | depth 5 | 64 / 2 / 13 | 10 | 0 | 0 | 29,090 |
| | | depth 8 + cap 50 | 64 / 2 / 13 | 10 | 0 | 0 | 29,090 |
| eShop | 94 | depth 5 | 161 / 1 / 6 | 4 | 0 | 0 | 100,839 |
| | | depth 8 + cap 50 | 161 / 1 / 6 | 4 | 0 | 0 | 100,839 |

Depth 8 without the cap gives the same numbers on all four repos.

**Tier-2 scenario cost: unchanged (0%) on all four repos.** No slice reached even the old depth limit of 5, and no
entry point produced more than 10 flows.

## Where the traces stop (depth 5 and depth 8 alike)

| Repo | Stops | `unresolved_call` | `leaf_no_callees` | `external_boundary` | `db_boundary` | `cycle` |
|---|---|---|---|---|---|---|
| microblog | 56 | 18 (32%) | 21 (37%) | 14 (25%) | 3 (5%) | 0 |
| node-express | 42 | 5 (11%) | 13 (30%) | 20 (47%) | 0 | 4 (9%) |
| petclinic | 45 | 3 (6%) | 40 (88%) | 0 | 0 | 2 (4%) |
| eShop | 113 | 74 (65%) | 27 (23%) | 2 (1%) | 9 (7%) | 1 (0%) |

## Reading

- Depth was not what kept flows short on these repos. Traces end at real boundaries, or at calls the graph could
  not resolve. **Resolution, not depth, limits flow traces on real code.** The new bounds matter for deeper
  layered code (the synthetic tests cover depth 8 and the cap) and keep a pathological fan-out from flooding a
  Tier-2 prompt.
- **eShop:** 65% of stops are `unresolved_call`, mostly mediator and DI dispatch. That is where flow pages lose
  their tail.
- **petclinic:** 88% end at `leaf_no_callees`, mostly Spring Data repository interface methods. They behave as
  database boundaries but are not recognised as such.
- **Cost estimate:** the measured scenario input (about 1,000–1,600 tokens per entry point for all three
  prompts) is well under the estimate's fixed 4,500 (`llm/estimate.py`, 3 × 1,500), so the estimate is
  conservative here.

## Follow-ups

- Recognise Spring Data repository interface methods as `db_boundary` (petclinic's leaf stops). This belongs
  with M6.15 / lane C.
- eShop's unresolved mediator calls (MediatR `Send` → handler) are a DI/dispatch item (catalog row; M6.7.b
  covered registrations, not mediator dispatch).
- Calibrate `_SCENARIO_CALL` in `llm/estimate.py` against prose runs once real scenario token counts are
  recorded.
