# Example — `discover-change-plan` dry run on the spring-boot fixture

Recorded 2026-10-31 (roadmap M5.6) by following SKILL.md against a keyless scan of
`tests/fixtures/projects/spring-boot-app`.

**Requirement:** "Let the Orders screen filter orders by status (`GET /api/orders?status=NEW`)."

| Step | Call / action | Result |
|---|---|---|
| 1 | `get_overview` | fresh (`behind_by: 0`), java 11 files, 9 endpoints, 11 screens, 4 tables |
| 1 | `search {query: "orders"}` | `screen:orders-list`, `table:orders`, `OrderService.getOrders`, `endpoint:GET-/api/orders` |
| 1 | `get_screen {id_or_route: "orders-list"}` | menu Order Management › Orders; no UI events or API calls mapped; reads orders, order_items, customers, products |
| 1 | `get_table {name: "orders"}` | columns include `status`; defined by `Order.java:7-25` |
| 1 | `get_context` on `OrderController.list` / `OrderService.getOrders` | list → getOrders → unresolved `findAll` (Spring Data) |
| 2 | `get_impact` on both | getOrders ← OrderController.list (`receiver_typed`, confirmed `OrderController.java:18`); entry point `endpoint:GET-/api/orders`; no unknowns |
| 2 | step-3 searches | `getOrders` only called at `OrderController.java:18`; no front-end code in the fixture calls `/api/orders` |
| 3 | exemplars | `OrderController.get` (`@PathVariable`, `OrderController.java:21-24`) for parameter style; `OrderRepository` (Spring Data, `OrderRepository.java:6`) for derived queries |
| 5 | stop | plan shown, no source edited |

Outputs: [`IMPACT_REPORT.md`](IMPACT_REPORT.md), [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

What the dry run showed: the screen has no mapped backend chain in this fixture, so the plan
records that gap as an open question instead of assuming the screen calls the endpoint.
