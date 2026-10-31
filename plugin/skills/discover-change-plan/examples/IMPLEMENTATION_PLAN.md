# Implementation plan — Filter the Orders list by status (`GET /api/orders?status=NEW`)

## Pinned target files (edit only these without re-planning)
1. `src/main/java/com/example/repository/OrderRepository.java`
2. `src/main/java/com/example/service/OrderService.java`
3. `src/main/java/com/example/controller/OrderController.java`

## Style exemplars (files whose idiom to follow)
- `OrderController.get` (`OrderController.java:21-24`) — Spring MVC parameter binding; use `@RequestParam(required = false)` in the same style
- `OrderRepository` (`OrderRepository.java:6`) — Spring Data interface; add a derived query method rather than a custom `@Query`
- `OrderService.getOrder` (`OrderService.java:18-20`) — thin service delegating to the repository

## Steps (pseudocode per file)
1. `OrderRepository.java`: add `List<Order> findByStatus(String status);`
2. `OrderService.java`: add `public List<Order> getOrders(String status)` → `status == null ? orderRepository.findAll() : orderRepository.findByStatus(status)`; keep the no-arg `getOrders()` delegating to it
3. `OrderController.java` `list()`: add `@RequestParam(required = false) String status` and call `orderService.getOrders(status)`

## Backward compatibility
- `GET /api/orders` without `status` returns the same list as today (parameter optional)
- `OrderService.getOrders()` kept, so its existing caller (`OrderController.java:18`) and any caller outside the repo (unknown) still compile
- no schema change: `orders.status` already exists (`Order.java:21`); values are free-form strings — an unknown status returns an empty list, not an error

## Verification (exact commands)
- build: `mvn -q compile`
- tests: `mvn -q test` — the index reaches no tests for these symbols (`tests_reached` empty; coverage overlay not imported); add a `@WebMvcTest` for `OrderController` with and without `status`
- after editing: `get_change_impact {working_tree: true}` — changed symbols must be limited to the three pinned files

## Rollback
Revert the three files; no data or schema to undo.

## Open questions (before approval)
- Does the Orders screen call `GET /api/orders`? The index has no front-end mapping for it (see IMPACT_REPORT unknowns).
- Should `status` be validated against a fixed set (enum)? The entity stores a free-form string.
