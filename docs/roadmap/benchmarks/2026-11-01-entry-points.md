# Entry-point precision and recall: substring hints vs evidence score (M6.12)

- **Date:** 2026-11-01 · **Item:** M6.12 · **Repos:** spring-petclinic `753d35c2f844`, microblog `a975ef648643`, node-express-realworld `30b68e1e8814`, eShop `dc7ea499cd35` (pins in `bench/repos.toml`)
- **Graded sheet:** `raw/m612-entry-points.csv`, with one row per entry point that either rule picks plus known misses. Columns: repo, qualified name, file:line, picked by old/new, kind, score, evidence, verdict, reason.
- **What counts as an entry point:** a callable where control enters application code from *outside* it and starts a unit of work: a request, job, message, command, UI event or application lifecycle hook.
  - Methods reached from application code are not entry points, and that includes MediatR handlers reached through a mediator.
  - Container wiring is not an entry point.
  - A spec-only endpoint is not one either: an OpenAPI path or `.proto` rpc with no code is a contract, and its handler is the entry point.

## Method

1. A fresh keyless scan of each repo at its pin. Nodes and edges were captured in-process at the slices stage, so they include parse-time `bases` exactly as the pipeline sees them.
2. Both rules ran on the same graph:
   - **old:** node type, or a name containing `main`/`cli`/`command`/`cmd`/`consumer`/`listener`/`subscriber`/`handler`;
   - **new:** `score_entry_points`, threshold 0.5.
3. Every picked entry point was graded from source.
   - Groups with uniform evidence were graded by checking every member against its registration: 24 `[RelayCommand]` attributes, 22 `MapGet`/`MapPost(…, Handler)` method groups, 18 `AddSubscription<E, H>` lines.
   - All other rows were graded one by one.
4. **Recall** is measured against *known* entry points. That set is the correct picks of either rule, plus misses found by reading the repos (listed in the sheet with verdict `missed`). It is an upper bound: an entry point that neither rule finds and nobody has spotted is not counted.

## Results

| repo | old precision | new precision | old recall (known) | new recall (known) |
|---|---|---|---|---|
| spring-petclinic | 18/22 = 0.82 | 18/18 = **1.00** | 18/18 = 1.00 | 18/18 = 1.00 |
| microblog | 26/26 = 1.00 | 40/40 = **1.00** | 26/42 = 0.62 | 40/42 = **0.95** |
| node-express-realworld | 21/21 = 1.00 | 21/21 = 1.00 | 21/21 = 1.00 | 21/21 = 1.00 |
| eShop | 16/67 = 0.24 | 91/96 = **0.95** | 16/93 = 0.17 | 91/93 = **0.98** |

### What changed, per repo
- **spring-petclinic.** The old rule dropped:
  - `petclinicCacheConfigurationCustomizer`, a `@Bean` that matched the substring `cli` in *petclinic*;
  - three test classes' `main` methods (`MysqlTestApplication`, `PetClinicIntegrationTests`, `PostgresIntegrationTests`).
  - `@InitBinder` and `@ModelAttribute` helpers on controllers are not counted.
- **microblog.** The new rule adds:
  - the `flask translate` CLI group and its 3 commands;
  - 10 lifecycle hooks: `before_app_request`, 3 error handlers, the 2 HTTPAuth `verify_*` callbacks and their 2 `error_handler`s, `login.user_loader` and `shell_context_processor`.
- **node-express-realworld.** Unchanged: 20 routes plus the Prisma `seed.main`.
- **eShop.**
  - **Removed from the old list:**
    - 27 spec-only endpoints (`Catalog.API.json`, `basket.proto`);
    - 16 test methods;
    - 14 name-substring helpers (`GetClient`, `Entity.AddDomainEvent`, `MediatorExtension.DispatchDomainEventsAsync`, …).
  - **Added:**
    - 22 minimal-API handlers (`MapGet("/", GetOrdersAsync)`);
    - 3 gRPC `BasketService` overrides;
    - 18 integration-event handlers (`AddSubscription<E, H>`);
    - 3 background services (`AddHostedService<T>`, `BackgroundService`, `IHostedService`);
    - 24 MAUI `[RelayCommand]` commands;
    - 5 .NET event handlers (`(object sender, …EventArgs e)`, including the RabbitMQ `OnMessageReceived` consumer);
    - conventional MVC actions on the IdentityServer quickstart controllers.
  - MediatR command and domain-event handlers (`IRequestHandler`, `INotificationHandler`) are excluded. Application code sends to them, so they continue an endpoint's flow.

### Wrong picks that remain (5, eShop)
- `ConsentController.CreateScopeViewModel` and `DeviceController.CreateScopeViewModel` are public helpers on a `Controller`. Without `[NonAction]` they are routable by convention, but they are not meant as actions.
- `catalog-sort.GET name-asc`, `chat-dock.GET chat` and `chat-dock.DELETE chat` come from a **JS parser bug**. `LABELS.get('name-asc')` and `params.get('chat')` are read as Express routes even though they have no handler argument and the path doesn't start with `/`. The old rule picked them too. Follow-up for lane D (`parsers/javascript.py`).

### Known misses (4)
- **microblog `tasks.export_posts`:** an RQ job enqueued by the string `app.tasks.export_posts`.
- **microblog `SearchableMixin.after_commit`:** registered with `db.event.listen(…, 'after_commit', …)`.
- **eShop `AppShell.OnHandlerChanged` and `BadgeView.OnHandlerChanging`:** MAUI lifecycle overrides. The old rule found these only through the substring `handler`.

## Effect on the pipeline

- **Scenario counts** (one slice per entry point): petclinic 22 → 18, microblog 26 → 40, node 21 → 21, eShop 67 → 96. Of eShop's 67 old picks, 51 were wrong; 27 of those were spec endpoints, which give empty slices.
- **Prose cost estimate.** Tier-2 scenario inference makes about 3 LLM calls per scenario. `llm/estimate.py` still estimates the scenario count as endpoint + batch_job nodes: 32 on eShop, against 67 real scenarios before this change and 96 after. Follow-up: estimate the count from `score_entry_points`.
