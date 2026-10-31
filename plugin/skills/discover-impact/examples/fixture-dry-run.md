# Dry run — `discover-impact` on the spring-boot fixture

Recorded 2026-10-31 (roadmap M5.5) by following SKILL.md step by step against a
keyless scan of `tests/fixtures/projects/spring-boot-app`. Tool calls went through
the MCP middleware (`call_tool`), exactly as an agent's calls would.

**Request:** "What may break if I change `OrderService.createOrder`?"

## Transcript (condensed)

| Step | Call / action | Result |
|---|---|---|
| 0 | `get_overview` | `ok`, `behind_by: 0`, `dirty: false` → index current |
| 1 | `search {query: "createOrder", limit: 3}` | `symbol:com.example.service.OrderService.createOrder` first |
| 1 | `get_context {target: "createOrder"}` | 1 caller `OrderController.create`; `unresolved_calls_here: 1` |
| 1 | `get_symbol_source {target: "…OrderService.createOrder"}` | `verified: true`, `OrderService.java:22-24` → `return orderRepository.save(order);` |
| 2 | `get_impact {target: "…OrderService.createOrder"}` | 1 item, not truncated; entry points `endpoint:POST-/api/orders`; unknowns `[]`; overlays missing: co_change, coverage, runtime |
| 2 | Read `OrderController.java:26-29` | line 28 `return orderService.createOrder(order);` on an `@Autowired` field (line 13-14) → **confirmed**; kind endpoint → **branch ends (entry point)** |
| 3 | grep `createOrder` outside the service | only `OrderController.java:28` → no missed callers |
| 3 | grep route `/api/orders` outside `.discovery/` | `OrderController.java:11` and `ground_truth.json` (test fixture metadata) → no front-end caller |
| 3 | seed's own unresolved call `save` (reason `not_found`) | Spring Data repository method → **data write to `orders`** the graph does not model (until M6.8 SQL/ORM edges) — added to Data touched |

## Report

```markdown
# Impact — com.example.service.OrderService.createOrder
Index: 1326ff4f7836 (behind by 0) · depth reviewed: 1 (all branches ended) · confirmed 1 / refuted 0 / unclear 0 / not reviewed 0

## Confirmed impact
| # | Node | Kind | Why | Evidence | Verdict |
| 1 | OrderController.create | endpoint | calls createOrder (receiver_typed, static) | OrderController.java:28 | confirmed |

## Entry points reached
- endpoint POST /api/orders ← OrderController.create

## Data touched
- table `orders` via `orderRepository.save(order)` (OrderService.java:23) — found by reading the seed; not a graph edge

## Unknowns
| Kind | Where | What I searched | Result |
| (graph reported none) | — | createOrder callers, `/api/orders` route string | resolved: no further references |

## Verify after the change
`mvn -q compile` then `mvn -q test` (no tests reach this method in the graph; `tests_to_run` unknown until coverage is imported)
```

## What the dry run showed

- The protocol reached a terminal state on every branch (one, an entry point) without stopping early.
- The extra searches in step 3 found a real dependency the graph lacks (repository write), which is the point of that step.
- No candidate disambiguation was needed; the `status: unknown` + `candidates` path is covered by `tests/test_query_service.py`.
