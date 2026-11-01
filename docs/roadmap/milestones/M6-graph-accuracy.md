# M6 — Graph accuracy for target stacks

**Goal:** close the parsing/resolution gaps that matter for ASP.NET/WebForms, Java/Spring/JSP, TS/JS SPAs and Python, measured by the benchmark protocol.
**Exit criteria:** accuracy gates in CI; per-language precision/recall reported vs M0.12.
**Refs:** `docs/roadmap/design/graph-accuracy.md`, `docs/roadmap/research/edge-case-catalog.md`

> Every item: add fixtures for the edge cases it fixes, and record bench deltas in PROGRESS.md.

### M6.1 Walker v2
- **Status:** Done (e0c6c57)
- **Size:** M
- **Lane:** C
- **Depends on:** M0.12
- **Owns:** `src/ai_discovery/repo/walker.py` (new; merges `repo/lang_detector.py` + `repo/file_walker.py`)
- **Acceptance:**
  - Git repos: `git ls-files -co --exclude-standard`; non-git: `.gitignore` via pathspec; plus `.discoveryignore` and `scan.exclude`.
  - Path-pattern excludes (not bare dir names — `packages/` no longer skipped); size caps via `scan.max_file_kb_source` (2048) and `scan.max_file_kb_other` (500), skipped files recorded as `file_too_large`; binary + minified detection; generated-file tags (`*.designer.cs`, `*.g.cs`, banners); symlinks not followed (realpath containment); submodules/nested repos opt-in (`scan.submodules`).
  - Every skip reason counted in degradations.
- **Verify:** `pytest -q tests -k walker`
- **Out of scope:** encoding (M0.4).

### M6.2 C# coverage
- **Status:** Done (ba29c22)
- **Size:** M
- **Lane:** C
- **Depends on:** M0.3
- **Owns:** `src/ai_discovery/parsers/csharp.py`
- **Acceptance:**
  - interface/record/struct/enum nodes; file-scoped and single-segment namespaces; partial classes merged by full name; `generic_name` and `?.` call capture; same-namespace + `global using` scope.
- **Verify:** `pytest -q tests -k csharp`
- **Out of scope:** VB.NET (M6.4).

### M6.3 Java coverage
- **Status:** Done (0c9bbca)
- **Size:** M
- **Lane:** C
- **Depends on:** M0.3
- **Owns:** `src/ai_discovery/parsers/java.py`
- **Acceptance:**
  - interface/enum/record nodes; `_receiver_type` split from `_unwrap_type` (receiver keeps outer type `List`, JPA association keeps inner); local-variable and parameter receiver typing; same-package scope.
  - spring-petclinic call-edge recall improves vs baseline.
- **Verify:** `pytest -q tests -k java`
- **Out of scope:** DI (M6.7.a/.b).

### M6.4 VB.NET code-behind
- **Status:** Blocked (owner 2026-11-01: VB.NET support not needed)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.1
- **Owns:** `src/ai_discovery/parsers/vbnet.py` (new)
- **Acceptance:**
  - Spike first: tree-sitter VB.NET grammar availability/quality (Unverified); fall back to import-map tier if none.
  - Classes, Sub/Function, `Handles` clauses, `Imports`; `.aspx` `On*` events resolve to VB handlers.
- **Verify:** `pytest -q tests -k vbnet`
- **Out of scope:** —

### M6.5.a CommonJS bindings + TS barrels
- **Status:** Done (031ee4d)
- **Size:** M
- **Lane:** C
- **Depends on:** M0.3
- **Owns:** `src/ai_discovery/parsers/javascript.py`; resolver JS module matching in `src/ai_discovery/graph/call_graph.py` (same lane, as in M6.2/M6.3)
- **Acceptance:**
  - CommonJS `require` bindings become imports: `const x = require('./m')` (namespace), `const { a, b: c } = require('./m')` (named, aliased); `x.fn()` resolves to the function defined in `./m` (`import_exact`), never by global name.
  - Arrow-function / function-expression consts keep their call sites (hard case `js-commonjs-arrow` passes: both key edges ≥ 0.75).
  - ES namespace imports (`import * as x from './m'`) + `x.fn()` resolve the same way.
  - TS re-exports/barrels (`export * from`, `export { a as b } from`) followed to a fixed point (cycle-safe, depth-capped); aliases preserved; barrel files with no declarations are read from disk.
- **Verify:** `pytest -q tests -k "commonjs or barrel"`; `python -m bench gate`
- **Out of scope:** dynamic `require(expr)`; `module.exports = class …` default-export typing.

### M6.5.b Vue SFC
- **Status:** Done (7af7b86)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.5.a
- **Owns:** `src/ai_discovery/parsers/vue.py` (new), `src/ai_discovery/parsers/javascript.py`
- **Acceptance:**
  - `.vue` `<script>`/`<script setup>` parsed with byte offsets preserved (non-script regions blanked); template component tags and `@event` handlers become edges.
- **Verify:** `pytest -q tests -k vue`
- **Out of scope:** globally registered components; mixins.

### M6.6 SPA routing + Angular DI
- **Status:** Done (2296ad6)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.5.a, M6.5.b
- **Owns:** `src/ai_discovery/route_parser.py`, `src/ai_discovery/extractors/` (new adapters)
- **Acceptance:**
  - Vue/React/Angular routes (incl. lazy `import()`) → screens with component file; Angular `@Injectable` constructor DI edges.
- **Verify:** `pytest -q tests -k "route or angular"`
- **Out of scope:** —

### M6.7.a DI registrations: ASP.NET, Spring qualifiers, test mocks
- **Status:** Done (ad1b363)
- **Size:** M
- **Lane:** C
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/extractors/di_*.py` (new), `src/ai_discovery/graph/` (dispatch post-pass), `src/ai_discovery/parsers/java.py` (bean names, `@Qualifier`, `@Primary` hints)
- **Acceptance:**
  - ASP.NET `AddScoped/AddTransient/AddSingleton/TryAdd*<I,T>` registrations (any file of the project, incl. top-level `Program.cs`) restrict interface dispatch to the registered implementation(s).
  - Spring: `@Qualifier("name")` on a constructor parameter or field picks the bean of that name (`@Component("name")` or the default decapitalised class name); `@Primary` wins when nothing qualifies.
  - A receiver that is a test double (`Substitute.For<T>()`, `new Mock<T>()`, `Mock.Of<T>()`, Mockito `mock(T.class)` / `@Mock`) never reaches a repo implementation.
  - DI-selected edges use `framework`; dispatch still refused above 24 implementations; ambiguous registration → no edge. Hard cases `csharp-di-two-impls` (dev) and `java-spring-qualifier` (sealed) pass.
- **Verify:** `pytest -q tests -k di`; `python -m bench gate --external`
- **Out of scope:** Spring XML beans, `@Bean` factory methods, Unity/Autofac (M6.7.b).

### M6.7.b DI adapters: Spring @Bean and XML, single-type and factory registrations, Unity/Autofac
- **Status:** Done (8976a24)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.7.a
- **Owns:** `src/ai_discovery/graph/call_graph.py` (DI selection), `src/ai_discovery/parsers/java.py` (`@Bean` hints)
- **Acceptance:**
  - Spring `@Bean` factory methods (bean name = method name or `@Bean("x")`, implementation = the `new X(..)` it returns) and XML beans (`<bean id|name class>` in the module's XML files) are beans; when exactly one implementation of an interface is a known bean, dispatch narrows to it (hard case `java-spring-bean-method` passes); `@Qualifier` also matches `@Bean`/XML names.
  - ASP.NET single-type (`AddHttpClient<T>()`, `AddScoped<T>()`) and factory registrations (`AddSingleton<I>(sp => new T(..))`); registrations of the caller's own project win, and a registered short name resolves through the registering file's namespace/usings (eShop `ChatState` → WebAppComponents `CatalogService`, not HybridApp's).
  - Unity `RegisterType<I, T>()` and Autofac `RegisterType<T>().As<I>()` (prevalence unverified — no pinned repo uses them; unit tests only).
- **Verify:** `pytest -q tests -k di`; `python -m bench gate --external`
- **Out of scope:** Spring profiles/conditional beans; Java config imported from libraries.

### M6.8 SQL via sqlglot
- **Status:** Done (d6b64f3)
- **Size:** M
- **Lane:** C
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/extractors/sql/` (new), `src/ai_discovery/extractors/sql_extractor.py`, `pyproject.toml` (append sqlglot)
- **Acceptance:**
  - DDL + SQL-in-strings parsed with sqlglot (tsql/oracle/mysql/postgres dialects), per-statement table read/write edges; regex fallback counted as degradation; interpolated table names skipped.
  - `.sql` skip check uses repo-relative path.
- **Verify:** `pytest -q tests -k sql`
- **Out of scope:** stored procs (M6.10).

### M6.9 MyBatis
- **Status:** Done (4b17fdc)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.8
- **Owns:** `src/ai_discovery/extractors/mybatis.py` (new)
- **Acceptance:**
  - Mapper XML `namespace` + statement `id` ↔ mapper interface method; statement SQL → table edges (dynamic `<if>/<foreach>` flattened conservatively). Prevalence in target estates Unverified — measure on owner repos.
- **Verify:** `pytest -q tests -k mybatis`
- **Out of scope:** —

### M6.10 Stored procedures + EXEC edges
- **Status:** Done (4357dbb)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.8
- **Owns:** `src/ai_discovery/extractors/sql/procs.py` (new)
- **Acceptance:**
  - Proc nodes; code → proc edges (ADO.NET `CommandType.StoredProcedure`, JDBC `CallableStatement`, MyBatis `statementType=CALLABLE`); proc → table read/write and proc → proc `EXEC` (sqlglot procedural coverage Low confidence → regex fallback).
- **Verify:** `pytest -q tests -k proc`
- **Out of scope:** —

### M6.11 WebForms/JSP/Struts extras
- **Status:** Done (6f9e8b7)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.1
- **Owns:** `src/ai_discovery/parsers/webforms.py`, `src/ai_discovery/parsers/jsp.py`, `src/ai_discovery/webforms_extractor.py`, `src/ai_discovery/jsp_extractor.py`, `src/ai_discovery/extractors/webconfig.py` (new)
- **Acceptance:**
  - `.ascx`/`.master`/`.asmx`/`.ashx`/`Global.asax`, `<%@ Register %>`, `Page_Load` auto-wiring, `SqlDataSource`; `web.config`/`web.xml` servlet mappings; `struts-config.xml` actions; `markup_event` edges.
- **Verify:** `pytest -q tests -k "webforms or jsp or struts"`
- **Out of scope:** —

### M6.12 Entry-point scoring
- **Status:** Done (c6272e1)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.7.a, M6.7.b, M6.11
- **Owns:** `src/ai_discovery/graph/entry_points.py` (new), `src/ai_discovery/graph/call_graph.py`
- **Acceptance:**
  - Evidence-based score (framework registration, route/screen binding, fan-out, in-degree 0, visibility) replaces substring name hints; test/demo paths excluded; precision measured on bench repos.
- **Verify:** `pytest -q tests -k entry_point`
- **Out of scope:** the depth-8 / 50-flows-per-entry flow tracer of design §8 (M6.17); feeding the score to `get_overview` / domain entry points (`query/views.py`, `graph/domain_classifier.py`).

### M6.13 Accuracy gates in CI
- **Status:** Done (090b2ba)
- **Size:** M
- **Lane:** Q
- **Depends on:** M0.11, M1.12
- **Owns:** `bench/`, `tests/corpus/`, CI config
- **Acceptance:**
  - Per language × origin precision (graded sample) and per-repo recall tracked; regression beyond tolerance fails CI; saturated fixtures (all 1.0) replaced with hard cases from the edge-case catalog; sealed split never used for tuning.
- **Verify:** `python -m bench gate`
- **Out of scope:** —

### M6.14 Re-graded precision samples after M6.2/M6.3
- **Status:** Done (6c23e44)
- **Size:** M
- **Lane:** Q
- **Depends on:** M6.2, M6.3, M6.13
- **Owns:** `bench/precision/sheets/`, `bench/gate.py`, `docs/roadmap/benchmarks/` (results page, raw runs)
- **Acceptance:**
  - New seeded sample for every stratum whose emitted-edge count drifted > 20% since the M1.12 sample (spring-petclinic, eShop); strata that did not drift keep their graded rows; every new row graded from source with a reason.
  - `bench/precision/sheets/CURRENT.json` names the sheet and the recorded run per repo; the gate reads it (no hard-coded sheet suffix).
  - Results page with per-stratum precision (Wilson 95% CI) before/after and who graded.
- **Verify:** `python -m bench gate --external` (no drift warnings on the re-sampled repos)
- **Out of scope:** microblog, node-express (no drift).

### M6.15 Precision fixes from the M6.14 re-grade
- **Status:** Done (348f4e7)
- **Size:** M
- **Lane:** C
- **Depends on:** M6.14
- **Owns:** `src/ai_discovery/graph/call_graph.py`, `src/ai_discovery/parsers/csharp.py`, `src/ai_discovery/parsers/java.py`
- **Acceptance:**
  - Interface implementations matched by the import-resolved interface, not the short base name (petclinic: `jakarta.validation.Validator` ≠ Spring `Validator`).
  - A local variable whose declared type is a foreign import never gets a `global_unique` guess (petclinic `PropertySource.getName`).
  - C# `external_client` requires a client-typed receiver (`HttpClient`, `GrpcChannel`, …), not a variable named `request` (eShop: 10 of 30 graded wrong).
  - Duplicate generated types (two copies of a `.proto`) prefer the caller's project.
  - Each fix has a hard case; the gate's `known_wrong_still_emitted` lists shrink for the strata named in `docs/roadmap/benchmarks/2026-10-31-precision-regrade.md`.
- **Verify:** `python -m bench gate --external`
- **Out of scope:** re-grading (the sheets stay; wrong edges simply stop being emitted).

### M6.16 Chained-call typing
- **Status:** Done (1745b09)
- **Size:** S
- **Lane:** C
- **Depends on:** M6.2, M6.3
- **Owns:** `src/ai_discovery/parsers/java.py`, `src/ai_discovery/parsers/csharp.py`, `src/ai_discovery/graph/call_graph.py`
- **Acceptance:**
  - Methods record their declared return type; a call `a.b().c()` types `c`'s receiver from `b`'s return type when `b` resolved to a repo method (Java, C#).
  - Library return types (`Optional<T>`, `List<T>`, `Task<T>`) are foreign: the call is `external`, never guessed; generic unwrapping is out of scope.
  - New edges use `receiver_typed`; hard case added.
- **Verify:** `pytest -q tests -k chained`
- **Out of scope:** generic type inference; fluent builders returning `this` beyond one hop.

### M6.17 Flow tracer depth and per-entry flow cap
- **Status:** Planned
- **Size:** S
- **Lane:** C
- **Depends on:** M6.12
- **Owns:** `src/ai_discovery/graph/call_graph.py`
- **Acceptance:**
  - Slices trace to depth 8 with at most 50 flows per entry point (design §8), keeping the M1.12.b following rules and stop reasons; Tier-2 scenario cost before/after measured on the four pinned bench repos and recorded.
- **Verify:** `pytest -q tests -k "slice or scenario"`
- **Out of scope:** changing which edges are followable.
