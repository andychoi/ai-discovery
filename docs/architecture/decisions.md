# Design Decisions

This document explains *why* we chose specific heuristics and trade-offs in AI-Discovery.

---

## Call Graph Resolution: Closed Origin Vocabulary (ADR-0004)

> **Superseded 2026-10-13 (M1.12.a).** The "4-stage graded confidence" cascade
> below (one origin spanning 0.95–0.6, fan-out to ≤ 8 candidates, a 0.5 edge to
> the bare name for every unresolved call, A-3 community narrowing) is retired.
> The resolver now follows [ADR-0004](../roadmap/adr/0004-edge-origin-vocabulary.md);
> the origin → confidence table lives **only** there and in
> `src/ai_discovery/graph/origins.py`.

### Decision
- **One confidence per origin.** Each edge has exactly one origin from a closed
  enum (`scip`, `import_exact`, `import_module`, `self_scope`, `inherited`,
  `receiver_typed`, `receiver_global`, `framework`, `dispatch`, `same_file`,
  `same_package`, `global_unique`, `external_client`, `sql_regex`). The origin
  fixes the confidence, the edge kind and the evidence kind. Stored in
  `call_edges.resolved_by` / `edge_kind` / `evidence_kind`.
- **Ambiguity → no edge.** Two or more candidates in a tier: no edge, a row in
  `unresolved_calls` with `reason=ambiguous` and `candidate_count`.
- **Unresolved → no edge.** `not_found`, `builtin`, `dynamic`, `external`,
  `dispatch_cap`, `di_ambiguous` rows instead of 0.5 edges to bare names.
- **Name-only guards** (`global_unique`, 0.50): same language family, callable
  target, name not in the per-language builtin/stdlib-member list, and in
  Python/JS a receiver-less call never binds to a class method. Java/C#
  receiver-less calls are `this` calls: they bind only on the enclosing class
  or its ancestors (`self_scope` / `inherited`), else `not_found`/`external`.
- **Types:** a receiver whose type is known (field/ctor-param type, or a type
  name used as a static receiver / constructor call) resolves on that type —
  `receiver_typed` when the type is visible locally (same file, same package,
  imported/`using`), `receiver_global` when it is known only by a unique
  repo-wide name. An interface-typed receiver with one implementing method is
  `framework` (DI binding); 2–24 implementations give one `dispatch` edge each;
  more is refused (`dispatch_cap`). A graph-wide pass also adds base-method →
  override `dispatch` edges.

### Why
- One origin with several confidences made confidence meaningless as a filter;
  fan-out and bare-name edges flowed into slices and BFS as if they were calls
  (ADR-0004 context; repowise changelog lessons in the edge-case catalog).
- **A-3 community narrowing is retired:** being wired to a module elsewhere in
  the graph is not evidence of which same-named method a call reaches. The
  `python-community-collision` fixture now forbids both candidate edges.

### Trade-offs
- **Recall drops, precision rises.** Calls that used to fan out or bind by
  graph locality are now `unresolved_calls` rows. They are reported (scan
  summary, overview page, bench) — never hidden. The M1.12.b bench run
  measures the deltas against the M0.12 baseline.
- Constructor-DI references whose type has no visible import (e.g. a missing
  `using`, or a CommonJS `require` the JS parser does not record) drop from
  0.8 to `receiver_global` 0.75; the corpus bars were lowered to 0.75 with a
  note per fixture.

### SQL via sqlglot (M6.8)
- **Every function that runs SQL gets table edges**: `reads_table` / `writes_table` per statement, to a `sql_table`
  node. The write target of `UPDATE o … FROM dbo.Orders o` is resolved through its alias; CTE names are not
  tables; `MERGE` writes its target and reads its source. Origin `sql_parsed` (0.90) when sqlglot parsed the
  statement, `sql_regex` (0.60) from a keyword fallback otherwise (degradation `sql_regex_fallback`). Writes outrank
  reads in impact.
- **Only real SQL**: a literal needs a statement shape plus upper-case keywords or SQL punctuation; JPQL/HQL is
  skipped; an interpolated table name (`$"… {table}"`, `" + table + "`) is skipped and counted
  (`sql_dynamic_table`).
- **Foreign keys** also come from `ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY` and from bracket/back-tick quoted
  table constraints, which the regex missed.

### Markup and descriptor bindings (M6.11)
- **WebForms directives** Page, Control, Master, WebService (`.asmx`), WebHandler (`.ashx`) and Application
  (`Global.asax`) each name a code class (`Inherits` or `Class`). Handlers and services become `endpoint` nodes
  (route = path under the web application root); pages, controls and master pages stay `ui_component`.
- **Three markup bindings, all `framework` edges:** `markup_event` (`OnClick="Save_Click"`; a missing handler is
  `not_found`), `markup_auto` (lifecycle names ASP.NET calls by convention — `Page_Load` & co. when
  `AutoEventWireup` is on, `Application_*`/`Session_*` in Global.asax, `ProcessRequest` — bound only when the
  method exists on the class or an ancestor, otherwise dropped silently because it is not a call), and
  `markup_register` (`<%@ Register Src="~/Controls/Cart.ascx" %>` → the control, `~` = the nearest folder with a
  project file or web.config). `SqlDataSource` commands are kept on the page as `sql` hints.
- **Descriptors** (`extractors/webconfig.py`): `web.xml` servlet mappings, `web.config` HTTP handlers and Struts 1/2
  action mappings become endpoints (method `ANY` unless a verb is declared) whose `xml_mapping` call sites bind,
  like `markup_auto`, only to methods that exist (`doGet`/`doPost`/`service`, `ProcessRequest`, `execute` or the
  Struts 2 `method`).

### Walker v2: which files a scan reads (M6.1)
- **Git decides first.** Inside a git work tree the file set is `git ls-files -co --exclude-standard`; elsewhere a
  filesystem walk honours every `.gitignore` (pathspec). `.discoveryignore` and `scan.exclude` add gitignore-syntax
  excludes. Symlinked directories are never followed; nested repositories and submodules are opt-in.
- **Bare directory names are not evidence.** Only always-tooling names are skipped by name; `packages`, `bin`, `obj`,
  `target`, `build`, `out`, `dist`, `env`, `publish`, `artifacts` are skipped only when their surroundings show they
  are output (see `design/graph-accuracy.md` §3, "As built"). A yarn workspace's `packages/` and a Python package
  named `build` are source; `assets/` is no longer skipped.
- **Every skip is a degradation** of stage `walk`, with up to five sample paths; generated files are kept and their
  nodes tagged `generated`.

### C# scope rules (M6.2)
- **Namespaces.** A type's qualified name uses its enclosing block namespaces, else the file-scoped
  `namespace X;`, else the file stem. Before M6.2 the file-scoped form fell back to the stem, so two
  `Calculator` classes in different namespaces collided.
- **Same namespace and `global using`.** A type in the caller's namespace (or a parent namespace) is
  local (`receiver_typed`). `global using` lines (any `.cs` file, including type-less `GlobalUsings.cs`)
  and `.csproj` `<Using Include>` items apply to every file of that project (nearest `.csproj` up the tree).
- **Foreign declared receiver → no guess.** When a receiver's *declared* type (field, property, constructor
  parameter) is not a repo type and nothing in the repo implements it — `ILogger<T> _logger` — the call is
  recorded `unresolved_calls reason=external`; the name-only tier never binds it to a same-named repo method.
  C# only for now: the C# parser emits interface/enum nodes, so "no repo type" is reliable; Java joins with
  M6.3. Effect on eShop: `global_unique` precision 0.833 → 0.926 on the graded M1.12 sample; unresolved
  rows 2,525 → 2,851 (honest misses instead of guesses).
- **Partial classes** merge field types and bases by full name, so a code-behind file sees fields declared
  in its designer file.
- Interfaces and enums are type nodes but not class types for receiver typing: a call through an interface
  still resolves to the implementations (`framework` for one, `dispatch` for 2–24).

### Java scope rules (M6.3)
- **Type kinds.** Interfaces (with their declared methods), enums (with methods) and records are nodes;
  interface bases include `extends` lists. Single-segment packages (`package a;`) are recognised.
- **Receiver type ≠ entity type.** Receiver typing keeps the outer declared type (`List<Item> items` →
  `List`, a library type → `external`); the JPA/table view keeps the element type (`Item`). Two maps:
  `receiver_types` (resolution) and `field_types` (pages, relationships).
- **Locals and parameters** type receivers (`Owner owner`, `var pet = new Pet()`), shadowing fields.
- **Repo interfaces without implementations** (Spring Data repositories): a call reaches the interface's
  *declared* method (`receiver_typed`); a method it only inherits from a library interface (`save` from
  `JpaRepository`) is `external`; with 1 / 2–24 repo implementations the call keeps going to them
  (`framework` / `dispatch`).
- **Foreign declared receiver → no guess** now applies to Java as well as C#.
- Effect on spring-petclinic (@753d35c): key-edge recall 0.333 → 1.0; name-only `global_unique` edges
  120 → 20; `receiver_typed` 19 → 113, `inherited` 8 → 62 (a 24-edge spot check of the new typed and
  inherited edges was all correct; the graded sample needs re-drawing before precision is re-quoted).

### DI registrations and test doubles (M6.7.a)
- **Dispatch is narrowed by what the container injects**, and the chosen target is a `framework` edge
  (`framework_binds`); with no DI fact, dispatch is unchanged (one implementation → `framework`, 2–24 →
  `dispatch` to each, more → `dispatch_cap`). Order of evidence:
  1. Spring `@Qualifier("x")` / `@Named("x")` on the field, or on the constructor parameter of the same name
     → the bean named `x` (`@Component("x")`, `@Service("x")`…, else the decapitalised class name);
  2. ASP.NET `Add{Scoped,Transient,Singleton,HttpClient}<I, T>()` / `TryAdd*` anywhere in the repo (files are
     read, since a top-level `Program.cs` has no node) → the registered `T`; same-named `T`s in several
     projects → the caller's project's;
  3. Spring `@Primary` → that bean.
  Two or more registered/qualified candidates → `di_ambiguous`, no edge.
- **Test doubles stop at the declared type.** A receiver assigned from `Substitute.For<T>()`, `Mock.Of<T>()`,
  `A.Fake<T>()`, `mock(..)`/`spy(..)` or annotated `@Mock`/`@MockBean`/`@MockitoBean`/`@Spy` never reaches an
  implementation class (`external`); a call to a method the declared interface itself declares (a Spring Data
  repository) keeps its edge to that declaration.
- Effect on eShop (graded): `framework` 0.767 → 0.958 (the 7 NSubstitute rows gone), `global_unique` 0.926 →
  0.962; production ClientApp services bind to the registered real implementation instead of dispatching to the
  unit-test `Mock*` classes too (those 9 dispatch edges were graded "correct" under any-implementation semantics;
  the stratum was re-sampled).
- **M6.7.b adapters.** Spring beans are stereotype classes, `@Bean` factory products (name = method or
  `@Bean("x")`, implementation = the `new X(..)` returned) and XML `<bean id|name class>` in the module's XML
  files; when exactly one implementation of an interface is a known bean, dispatch narrows to it, and
  `@Qualifier` matches every kind of bean name. Two beans of one type with neither qualifier nor `@Primary` keep
  dispatching (Spring itself would refuse to start). C# registrations also cover single-type `Add*<T>()` /
  `AddHttpClient<T>()`, factories `Add*<I>(sp => new T(..))`, Unity `RegisterType<I, T>()` and Autofac
  `RegisterType<T>().As<I>()` / `.AsImplementedInterfaces()` (Unity/Autofac prevalence unverified). A
  registration's short type name resolves through the registering file's namespace and usings, and the
  caller's own project's registrations are consulted first — eShop's WebApp `ChatState` now reaches
  WebAppComponents' `CatalogService`, not HybridApp's same-named class.

### Precision rules from the M6.14 re-grade (M6.15)
- **Same short name, different interface.** An implementation is a dispatch target only if it does not
  explicitly import a *different* type of that name than the caller (`jakarta.validation.Validator` vs
  Spring's `Validator`). Unknown on either side (wildcards, C# namespace usings) keeps the old behaviour.
- **Loop variables are typed** (`for (PropertySource<?> s : list)`), so a library-typed one is `external`
  rather than a `global_unique` guess. **Type parameters are not types:** `<T extends BaseEntity>` types `T`
  locals as `BaseEntity`; an unbounded `T`/`E` types nothing.
- **Client-library names need the library.** Registry tokens that name JS/Python libraries (`request`,
  `requests`, `axios`, `prisma`, …) never match in Java/C#/VB (there they are variable names: a MediatR
  command, an `HttpRequestMessage`), and in JS/Python only when the file imports that name (`fetch` is a
  global). Type-like tokens (`HttpClient`, `RestTemplate`, `KafkaTemplate`, …) are unchanged.
- **Duplicate `.proto` contracts** (a client's copy of a service's file) are qualified by the namespace their
  generated code lives in (`csharp_namespace` → `java_package` → `package`): `proto::<ns>.<Message>`, and a
  caller in that namespace binds to its own copy (`receiver_typed`). A message defined once keeps `proto::<Message>`.
- Effect (graded, `bench/precision/sheets/CURRENT.json`): eShop `external_client` 0.667 → 1.0 (10 wrong `request`
  edges gone; one correct edge that was only found through a variable named `request` is lost), `receiver_global`
  0.864 → 0.95; spring-petclinic `global_unique` 0.9 → 1.0, the two wrong `framework` edges gone; four loop-variable
  calls become `inherited`, `EntityUtils.getById` becomes `receiver_typed`.

### Chained calls and extension methods (M6.16)
- **`a.b().c()` is typed from `b`'s declared return type** when `b` resolves to exactly one repo method
  (Java, C#; up to 3 hops; `this.m().x()` and bare `m().x()` included). The outer type is used, so a library
  return type (`Optional<Owner>`, `List<T>`, `Task<T>`) makes `.c()` `external` — never a same-named repo
  method. A type-parameter return (`T get()`) types nothing; generic unwrapping is not attempted.
- **C# extension methods** record the type they extend (`this IServiceCollection s` → `extends_type`). A
  receiver whose declared or returned type is outside the repo first tries the repo's extension methods of
  that name on that type (`receiver_typed`); only then is the call `external`.
- **C# receivers are the rightmost operand.** The grammar parses `1 + _repo?.Find(id)` with the whole binary
  expression as the access's condition; the receiver is cut to `_repo` (brackets, generic `<…>` and
  `global::` kept). Fluent chains split across lines lose their whitespace; `#if` lines are dropped. A chain
  whose head the grammar lost (an `#if` block inside it) is left untyped.
- Effect: eShop +2 correct typed edges (`ResilientTransaction.New(..).ExecuteAsync`, `IMediator` extension),
  −2 wrong name guesses (`GetClient().LogoutAsync` on the OIDC client); petclinic 1 edge `global_unique` →
  `receiver_typed`. Hard case `java-chained-call` 0.5 → 1.0.

### JS/TS module rules (M6.5.a)
- **One query table per grammar.** Tree-sitter queries are compiled for JS, TS and TSX and chosen by file
  extension. A query compiled for the JS grammar silently matches nothing on a TS tree, which had left every
  `.ts`/`.tsx` file without call sites or imports (every call fell to a 0.5 name guess). `.tsx` uses the TSX
  grammar (JSX parses).
- **CommonJS is an import.** `const x = require('./m')` binds a namespace (like `import * as x`);
  `const { a, b: c } = require('./m')` binds names. Only module-level, string-literal `require`s.
- **A relative import names a file, so the call binds there** (`import_exact`): `x.fn()` on a namespace
  binding, or `fn()` on a named/default binding, resolves to the module-level function defined in the file
  the specifier points to (extensions and `index` files tried). An aliased binding looks up the original
  name. A default import also tries the module's `export default <name>`.
- **Barrels are followed.** A module without the definition is read from disk for re-exports
  (`export * from`, `export { a as b } from`, `export { a as b }`, `export default a`), followed to a fixed
  point — cycle-safe, at most 8 hops. `export * as ns` is not followed.
- **Library requires never bind repo code.** A non-relative module that is not a repo file keeps the
  existing rule: the call is `external`, never a same-named repo function.
- Effect on node-express-realworld (@30b68e1): key-edge recall 0.0 → 1.0; 37 `global_unique` edges → `import_exact`;
  21 Prisma `external_client` edges now found. Re-graded sample: all correct (`bench/precision/sheets/CURRENT.json`).

### SPA route files and TS receiver typing (M6.6)
- **A route's component file is resolved, not globbed.** The import specifier is resolved against the route
  file (`./`, `../`), `@/`/`~/` (→ `src/`) and tsconfig/jsconfig `paths` aliases, with extensionless and
  `index` files tried; lazy `import()` (Vue/React) and Angular `loadComponent`/`loadChildren` included. The
  screen's `fe_component` is that file; the old stem glob (first `Home.vue` anywhere) remains only the fallback
  for unresolvable specifiers.
- **TS declarations type receivers**: constructor parameter properties (`private readonly orders:
  OrderService` — Angular/Nest DI), annotated class fields, `inject(X)` and `new X()` field initialisers;
  inside functions, typed parameters and `const x: T` / `= new T()` locals. A type imported from a package
  (`HttpClient` from `@angular/common/http`) makes the call `external`; a type the repo declares as a TS
  `interface` (not a node) keeps the previous behaviour.

### Vue single-file components (M6.5.b)
- **Scripts keep their offsets.** Every byte outside `<script>` / `<script setup>` bodies is blanked (newlines
  kept), so functions get their real `.vue` line numbers; `lang="ts"|"tsx"` picks the grammar.
- **One `ui_component` node per SFC** (qualified name = file stem) carries the template's bindings as call sites.
  Options-API `methods` become `<Stem>.<name>` method nodes; script-setup functions already are.
- **Template bindings are `framework` edges.** `@event` / `v-on:` handlers bind to a function **of the same
  file** (matched by file path, never by qualified name — two `Index.vue` files must not cross-link); inline
  statements (`count++`) and `$emit`/`$router` bind nothing. A component tag (PascalCase or kebab-case) binds
  only when the script imports it: a relative import → the imported `.vue` file's component node; a library
  import (`element-plus`) → `external`; an unimported tag (HTML, globally registered) → no call site.
- **`this.m()` in an Options-API method** → the same component's method (`self_scope`).
- Not covered: globally registered components, mixins, `setup()` inner functions, template expressions other
  than event handlers (`:prop="fn()"`).

### History (pre-ADR-0004)
The 2026-05/06 cascade — stage 0 index (1.0), exact (1.0), import-scope
(0.95), receiver-type (0.93; HIGH-3), short-name contextual 0.95…0.6 with A-3
community narrowing, unresolved 0.5 — is in git history (`call_graph.py`
before M1.12.a). Its HIGH-3 receiver-type and P0-2 interface→impl ideas
survive as `receiver_typed` and `framework`; Stage 0 survives as `scip`.

---

## Execution Slice Depth Limit: BFS ≤ 5 Levels

### Decision
Bounded breadth-first search with depth ≤ 5 for building execution scenarios.

### Why
- **Unbounded traversal is expensive**: Without a limit, we traverse the entire reachable call graph, which grows exponentially.
- **Business processes are modular**: Most meaningful execution paths are ~3–5 hops (entry → validation → processing → storage → logging).
- **LLM context limits**: Summarizing >50 nodes per scenario becomes unwieldy; Haiku/Sonnet work best on focused scenarios.

### Heuristic
```
primary_path = top 15 nodes by confidence in BFS order
alternate_paths = conditional branches (nodes with >1 callee)
```

Depth ≤ 5 means: (Entry) → (Hop 1) → (Hop 2) → ... → (Hop 5) = max 6 levels.

### Trade-offs
- **Pro**: Fast; manageable scenario size; mirrors how humans think about processes
- **Con**: Might miss long-running workflows (e.g., async batch processing with delayed steps)

**If you have a 10-hop process**, you might need to:
1. Increase depth limit to 7–8 (slower, larger scenarios)
2. Break into multiple scenarios (entry points at intermediate steps)
3. Mark long-hop edges as async/delayed


### Following rules (M1.12.b, ADR-0004 §2)
- Slices, alternate paths and the backward entry-point walk (`graph/entry_point_linker.py`)
  follow only edges that pass `origins.followable`: evidence `static|framework|scip`, edge kind
  `calls|dispatches_to|framework_binds|calls_external`, origin confidence ≥ 0.75. Name-only
  `global_unique` (0.50) edges are never walked; they stay in the graph for search and pages.
- A node is visited once (the visited set is filled at enqueue time).
- Every point where a slice stops records one reason in `Scenario.stop_reasons`:
  `db_boundary`, `external_boundary` (synthesized `external::` systems and EXTERNAL_API/QUEUE
  hints), `depth_cap`, `cycle` (all followable callees already in the slice), `dynamic_dispatch`
  (the node has `dynamic`/`dispatch_cap`/`di_ambiguous` unresolved calls), `unresolved_call`
  (calls exist but none can be followed), else `leaf_no_callees`.
- Depth stays 5 here; the depth-8 / 50-flows-per-entry flow tracer is M6.12.

---

## MANUAL Node Injection Heuristics

### Decision
Synthetic user task nodes are injected for:
1. Function names containing "approve", "review", "validate_manually"
2. State transitions with large gaps (DRAFT → APPROVED without intermediate)
3. Integration points with no direct code (business rule gates)

### Why
- **Incomplete code traces**: Real business processes include manual approvals that aren't in code (compliance review, manager sign-off).
- **State-driven semantics**: A state transition DRAFT → APPROVED implies a decision point, even if no code reflects it.
- **Execution credibility**: BPMN diagrams that skip manual steps are misleading to business users.

### Confidence Range
MANUAL nodes are scored 0.3–0.6 (low confidence) to signal "inferred, needs human validation".

### Validation Strategy
When generating docs, flag MANUAL nodes for business owner review. Example:

```markdown
⚠️ **Inferred Manual Step** (confidence: 0.4)
- Step: Manager Approval
- Reason: State transition DRAFT → APPROVED with no intermediate code
- Action: Validate against actual workflow documentation
```

---

## Tier 1/2/3 Model Routing: Haiku → Sonnet → Opus

### Decision
Three-tier LLM pipeline with increasing model capacity:
- **Tier 1 (Haiku)**: Chunk summarization (high concurrency, cost-effective)
- **Tier 2 (Sonnet)**: Flow analysis (per-domain, reasoning-heavy)
- **Tier 3 (Opus)**: Document rollup (final polish, self-review)

### Why
- **Cost optimization**: Haiku is ~50% cheaper than Sonnet, ~85% cheaper than Opus. Use it for high-volume tasks.
- **Quality gradient**: Not all tasks need Opus. Chunk summarization is straightforward; doc rollup is complex reasoning.
- **Budget control**: Tier 1 has no budget limit (cost-effective); Tier 2/3 check budget before running.

### Cost Breakdown (typical)
- Tier 1: 60% of volume, 20% of cost (many chunks, cheap)
- Tier 2: 30% of volume, 40% of cost (per-domain, medium)
- Tier 3: 10% of volume, 40% of cost (few docs, expensive)

### Tuning Levers
```yaml
discovery.yaml:
  # Provider selection: bedrock | ollama | mlx-gemma | mlx-qwen
  provider: bedrock
  max_concurrent: 10  # Increase to speed up
  budget_limit_usd: 50.00

  # Bedrock model overrides (defaults to claude-haiku-4-5/sonnet-4-6/opus-4-6)
  bedrock:
    tier1: us.anthropic.claude-haiku-4-5-20251001-v1:0
    tier2: us.anthropic.claude-sonnet-4-6
    tier3d: us.anthropic.claude-opus-4-6
    tier3p: us.anthropic.claude-opus-4-6

  # Ollama model overrides (defaults to gemma4:e2b / gemma4:26b / gemma4:31b)
  ollama:
    tier1: gemma4:e2b
    tier2: gemma4:26b
    tier3d: gemma4:26b
    tier3p: gemma4:31b
```

---

## Domain Classification: Namespace/Path Heuristics

### Decision
Group code nodes by namespace prefix + directory path, with fallback to file-level grouping.

### Why
- **Scalable**: Doesn't require ML training; works immediately on new codebases
- **Predictable**: Developers can reason about which domain a new file belongs to
- **Language-agnostic**: Works for Java (package), Python (module), C# (namespace), JavaScript (folder)

### Heuristic
```
1. Extract namespace/package prefix (longest common ancestor)
2. Group by domain (e.g., com.company.orders → "orders" domain)
3. Fallback: Group by top-level directory if namespace is unclear
4. Threshold: Need ≥ 3 nodes to form a domain (else singleton)
```

### Trade-offs
- **Pro**: Works without training; easy to explain
- **Con**: Doesn't capture cross-cutting concerns (logging, auth might belong in multiple domains); doesn't handle microservice boundaries

**When heuristics fail**, you can manually define domains in `discovery.yaml`:

```yaml
domains:
  - name: orders
    includes: [com.company.orders, app/src/orders]
  - name: payments
    includes: [com.company.payments, app/src/payments]
```

---

## State Machine Extraction: Explicit State Transitions

### Decision
Extract state machines from code by detecting patterns:
- Enum fields named `*_status`, `*_state`, `status`, `state`
- Assignments to these fields (e.g., `order.status = OrderStatus.APPROVED`)
- Function names that transition states (e.g., `approveOrder()`)

### Why
- **Business-critical**: State machines define the "playbook" for business processes
- **Visualizable**: State machines (FSM) are easy to render as Mermaid or BPMN
- **Verifiable**: We can extract state transitions from code and validate them against real logs

### Pattern Detection
```python
# Detected pattern:
order.status = OrderStatus.SUBMITTED  # State transition: ? → SUBMITTED
approveOrder()  # Trigger function

# Inferred:
state_transition = { entity: "Order", field: "status", from: "?", to: "SUBMITTED", trigger: "approveOrder" }
```

### Confidence
Explicitly coded state transitions (literal assignments such as
`order.status = APPROVED`) are extracted at confidence 1.0.

> **Reconciled 2026-05-31 (HIGH-9):** earlier prose here claimed a separate
> *name-inferred* transition path scored 0.6–0.8. That path is **not
> implemented** — every transition currently extracted comes from an explicit
> assignment and is confidence 1.0. The LLM does, separately, invent `USER_TASK`
> steps (approval/review) during flow analysis; those are not state transitions
> and are now rendered with a `⚠ inferred` marker in BPMN/Mermaid so they are
> never read as code-grounded facts.

---

## Pseudo Event Log Generation for Process Mining

> **Reversed 2026-06-06 — pm4py removed.** An objective code review found the
> pm4py integration carried no information in its implemented form:
> - The only real pm4py call (`inductive_miner.apply`) ran on a **single
>   linear trace per scenario** — discovery of a single sequential trace
>   returns the input unchanged (zero information gain). Its output
>   (`result.net`) was never consumed by any report, doc, or DB write.
> - `token_replay` was imported but never called; **fitness was hardcoded to
>   1.0**, so the quality gate (≥ 0.90) always passed.
> - Bottleneck/cycle-time metrics were computed over **synthetic 1-second
>   timestamps**, producing fabricated numbers (every duration exactly
>   1000 ms) that were injected into PF spec docs.
>
> Phase 16 now computes pure-Python edge-frequency flow statistics only
> (`ai/process_miner.py`). The pseudo event log **format below is retained**
> as the Phase 16 input.
>
> **Reintroduction condition**: pm4py becomes meaningful only with *real*
> runtime event logs (app logs / APM traces) — conformance-checking the
> code-derived model against actual execution. If that ingestion is built,
> reintroduce pm4py as an optional extra with lazy imports.

### Decision (superseded — see reversal above)
Convert execution scenarios to JSON event logs compatible with PM4Py and other process mining tools.

### Why
- **Bridging worlds**: Code analysis + process mining = comprehensive process discovery
- **Validation**: Process mining can detect bottlenecks, loops, and variant discovery that code analysis alone misses
- **Extensibility**: Pseudo logs can be mixed with real event logs for hybrid analysis

### Format
```json
{
  "case_id": "scenario_create_order_1",
  "process_name": "order_creation",
  "variant": "main",
  "events": [
    { "order": 1, "event_name": "Validate Order", "event_type": "PROCESS", "timestamp": "2026-04-17T10:00:00Z" },
    { "order": 2, "event_name": "Calculate Price", "event_type": "PROCESS", "timestamp": "2026-04-17T10:00:01Z" },
    { "order": 3, "event_name": "Save Order", "event_type": "DB", "timestamp": "2026-04-17T10:00:02Z" }
  ]
}
```

### Limitations
- **No real timestamps**: Pseudo logs use synthetic timestamps (order + 1s intervals)
- **No parallelism**: Event logs assume sequential execution; real code may run concurrently
- **No frequency**: We don't know which variant is most common (all variants have weight = 1)

**Future improvement**: Mix pseudo logs with real event logs to weight variants by actual frequency.

---

## Node Ranking Score: Multi-Signal Approach

> **Reconciled 2026-05-31 (MED-1):** this score ranks nodes *within* an
> execution slice; it is distinct from call-edge confidence (0.5–1.0, above).
> `call_graph._score_node` returns the **raw, unnormalized** sum below (range
> ~0–17) and stores it on `ExecutionNode.confidence` purely for ordering
> (primary_path = top-N by this score). The `min(1.0, …)/10` normalization in
> earlier revisions of this doc was **never implemented**; do not compare this
> field against a 0–1 threshold — it is a ranking key, not a probability.

### Decision
Rank = sum of independent signals (depth, state transition, data boundary, external call, state dependency):

```
# As implemented (raw ranking score, NOT normalized to 0–1):
rank = max(0, 5 - depth) + state_transition*4 + boundary*3 + external*2 + dependency*3   # 0..17
```

### Why
- **Composable**: Each signal captures a different kind of evidence (structure, semantics, data)
- **Explainable**: We can show the user exactly which signals contributed to a score
- **Tunable**: If a signal is wrong, we can adjust its weight without retraining

### Signals Explained

| Signal | Weight | Meaning | Example |
|--------|--------|---------|---------|
| Depth (5 - current_depth) | 1 | Nodes near the entry point are more likely in the primary path | Entry point (depth 0) = +5, leaf (depth 5) = 0 |
| State transition | 4 | If a node changes state, it's significant to the business process | `order.status = APPROVED` in a function = +4 |
| Data boundary (DB/Queue) | 3 | Database or queue interaction indicates a domain boundary | Repository call = +3 |
| External API call | 2 | External calls are notable but less central than internal logic | Payment gateway call = +2 |
| Read-after-write | 3 | If a node reads data after writing it, suggests important state | Save order, then fetch inventory = +3 |

**Max score**: 5 + 4 + 3 + 2 + 3 = 17; normalized to 0–1.0.

---

## Summary Table: Key Design Decisions

| Component | Decision | Trade-off | Reference |
|-----------|----------|-----------|-----------|
| Call Resolution | 7-level confidence | Precision vs recall | `docs/guides/call-graph/resolution-heuristics.md` |
| Execution Slices | BFS ≤ 5 depth | Coverage vs manageability | Phase 6 in pipeline docs |
| Manual Nodes | Heuristic injection | Accuracy vs completeness | `docs/guides/pipeline/phase-breakdown.md` |
| LLM Routing | Tier 1/2/3 (Haiku→Sonnet→Opus) | Cost vs quality | `docs/guides/pipeline/cost-tracking.md` |
| Domain Classification | Namespace heuristics | Scalability vs precision | Phase 4 in pipeline docs |
| State Machines | Pattern extraction | Automation vs coverage | `docs/guides/pipeline/phase-breakdown.md` |
| Pseudo Event Logs | Synthetic JSON format (pm4py removed 2026-06-06) | Simplicity vs realism | `docs/guides/pipeline/profiling.md` |
| Confidence Scoring | Multi-signal sum | Explainability vs accuracy | `docs/guides/call-graph/test-strategy.md` |

---

**Next**: When you want to change a heuristic or add a new one:
1. Document the **why** here (decision + rationale)
2. Update the relevant guide doc (call-graph, parsers, pipeline)
3. Add test cases to validate the change
4. Re-measure confidence scores on a corpus before merging
