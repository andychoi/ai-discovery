# Impact report — Filter the Orders list by status (`GET /api/orders?status=NEW`)
Index: 1326ff4f7836 (behind by 0) · generated 2026-10-31

## Targets
- `com.example.controller.OrderController.list` — `OrderController.java:16-19` (endpoint `GET /api/orders`)
- `com.example.service.OrderService.getOrders` — `OrderService.java:14-16`
- `com.example.repository.OrderRepository` — `OrderRepository.java:6-7` (new derived query)

## Confirmed impact (ranked)
| # | Node | Kind | Why (edge path) | Evidence | File:line | Confidence |
|---|---|---|---|---|---|---|
| 1 | OrderController.list | endpoint | calls OrderService.getOrders (`receiver_typed`, static) | `return orderService.getOrders();` | OrderController.java:18 | 0.93 |

## Entry points reached (screens / endpoints / jobs)
- endpoint `GET /api/orders` ← OrderController.list
- screen `orders-list` (Order Management › Orders): **not linked** — the index maps no API call from this screen (`get_screen` events and chain empty); whether the screen calls `GET /api/orders` is an open question

## Data impact (tables written / read)
- reads `orders` (`Order.java:7-25`, column `status` at line 21) via `orderRepository.findAll()` (OrderService.java:15) — not a graph edge (Spring Data); found by reading the code
- no writes

## Unknowns & unverified paths
| Kind | Where | What I searched | Result |
|---|---|---|---|
| unresolved_call | OrderService.java:15 `findAll` | Spring Data `JpaRepository` | resolved: framework method, reads `orders` |
| screen mapping | screen:orders-list | `get_screen`, grep `/api/orders` outside `.discovery/` | still unknown: no front-end source in the fixture |

## Co-change neighbours (history only — not dependencies)
- not available (git overlay M8.1 not imported)
