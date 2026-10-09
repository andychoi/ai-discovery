# Edge-Case Catalog (benchmarked against repowise)

- **Status:** Accepted (living document; add rows as you discover cases)
- **Last updated:** 2026-10-08
- **Related items:** M0.3–M0.10, M1.2–M1.13, M2.*, M3.*, M5.*, M6.*, M7.*, M8.*

**How to use:**
- Every row maps to a work item.
- When you implement the item, follow the described *behaviour*. Do not copy repowise code (ADR-0006).
- repowise references are doc or test file names under `../repowise`. Read them for behaviour only.
- Columns:
  - **AD status:** what ai-discovery does today (Present / Partial / Absent / Bug).
  - **Pri:** priority for the target stacks — ASP.NET/WebForms, Java/Spring/JSP, TS/JS, Python, SAP.

**Path shorthand:**

| Prefix | Location |
|---|---|
| `RWT/` | `../repowise/tests/unit/` |
| `RWD/` | `../repowise/docs/` |
| `AD/` | `src/ai_discovery/` |

## 0. Reference: repowise resolution origins and confidences

Recorded in the 2026-10-08 research pass. Behavioural reference: `../repowise/docs/layers/GRAPH.md` (resolution tiers and confidences) and `../repowise/docs/BENCHMARKS.md` (per-origin precision method). The research pass counted 37 origins; the docs describe 29. This table is reference data only — ai-discovery's own vocabulary is ADR-0004.

| Conf. | Origins |
|---|---|
| 0.95 | same_file, self_scope, enclosing_class |
| 0.93 | receiver_same_file, scoped_name, receiver_typed_same_file, receiver_field_same_file, receiver_framework_same_file, receiver_extension_same_file, return_type_same_file |
| 0.90 | same_package, import_scoped, receiver_same_package, receiver_typed_same_package, receiver_field_same_package, receiver_framework_same_package, return_type_same_package, self_inherited, enclosing_inherited |
| 0.88 | package_alias, module_alias, crate_root, receiver_import, receiver_typed_import, receiver_field_import, receiver_framework_import, receiver_extension_import, return_type_import |
| 0.85 | import_merged, same_target |
| 0.75 | receiver_global, receiver_typed_global, receiver_field_global, receiver_framework_global, receiver_extension_global, return_type_global |
| 0.50 | global_unique |

**Other edge kinds:**
- `dispatches_to`: 0.75
- `framework_binds`: 0.90
- `type_use`: 0.80
- heritage: 0.95 / 0.90 / 0.85 / 0.50

**Flow tracing** excludes `global_unique` and anything below 0.5.

**ai-discovery today** uses `resolved_by` with these values:
- `index` 1.0
- `exact` 1.0
- `import_scope` 0.95 / 0.85
- `receiver_type` 0.93
- `interface_impl` 0.90
- `short_name` 0.95–0.6
- `short_name_community` 0.8 / 0.7
- `unresolved` 0.5

ADR-0004 / M1.12 defines ai-discovery's own closed vocabulary. It is inspired by repowise's, not copied.

## 1. File discovery

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| `.gitignore` (root + nested), `.git/info/exclude`, tool ignore file; a malformed line doesn't abort the walk | Walker honours all of them (`RWT/ingestion/test_traverser.py`) | **Absent.** Hard-coded dir-name skip list (`AD/repo/lang_detector.py:7`) | High | M6.1 |
| Over-broad dir skips | Blocks vendor/build dirs; keeps ambiguous `bin`/`obj`/`lib` for later decision (`RWT/ingestion/test_fs_walk.py`) | **Bug.** Skips `packages/`, `migrations/`, `assets/`, `env/`, `out/`, so JS monorepos lose `packages/*` (`AD/repo/lang_detector.py:7-20`) | High | M6.1 |
| Generated files | First-2-line banners (`@generated`, "Code generated"); language suffixes (`*.Designer.cs`, `*.g.cs`) | Absent for C#/Java | Med | M6.1 |
| Minified bundles | `*.min.js` plus a >200 bytes/line sniff (`test_skips_minified_bundle_under_the_ceiling`) | Present for JS only (`AD/parsers/javascript.py:16-41`) | Low | M6.1 |
| Binary files | NUL byte in the first 8 KB (UTF-16 files therefore treated as binary) | Absent | Med | M6.1, M0.4 |
| Huge files | Separate caps: 500 KB for non-parseable files, 2 MB for source; skipped files recorded by name | **Absent** | High | M6.1 |
| Symlinks / junctions | Never followed; realpath cycle detection (`test_a_self_referential_link_does_not_recurse`) | **Absent.** Recursion risk (`AD/repo/file_walker.py:58-69`) | Med | M6.1 |
| Submodules / nested `.git` (dir or file) | Pruned unless opted in | Absent | Med | M6.1 |
| Monorepo package roots | Manifest-based roots (pnpm, tsconfig, `.sln`, Maven/Gradle) | Partial | Med | M6.1 |
| Test-file detection | One shared anchored rule set (`FooTest.java`, `Foo.Tests/`, `src/test/java`, `.spec.`) (`RWT/test_test_paths.py`) | Partial (`AD/ai/summarizer.py:28`) | Med | M6.1 |
| Worktrees (`.git` is a file) | Ask `git rev-parse` for paths | Untested | Low | M1.2 |
| Nested-checkout explosion | Contract extraction once scanned a million files; bounded since | Absent | Med | M6.1 |

## 2. Encoding

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| CP949 / EUC-KR legacy files | UTF-8 with `errors=replace`; the file survives but Hangul is lost. No charset detection | **Bug.** Strict `.decode()` raises and `_parse_one` drops the whole file (`AD/pipeline.py:599`) | **High** | M0.4 |
| BOM (UTF-8-sig, UTF-16) | BOM stripped (C#); CRLF normalized (`source_text`) | Absent | High | M0.4 |
| Offsets after transcoding | Transcode to UTF-8 bytes *before* tree-sitter so byte offsets match the text | — | High | M0.4 |
| JSP `pageEncoding`, `<?xml encoding?>` | — | Absent | Med | M0.4 (Inference) |
| Explicit encoding everywhere (Windows locale) | Changelog: "decoded as UTF-8 rather than the platform locale" | Unknown | Med | M0.4, M2.1 |

## 3. Parsing, by language

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| C# interfaces, records, structs, enums | Captured as symbols | **Bug.** Classes only (`AD/parsers/csharp.py:16`) | High | M6.2 |
| Java interfaces, enums, records | Captured | **Bug.** Classes only (`AD/parsers/java.py:26`) | High | M6.3 |
| C# file-scoped `namespace X;`, single-segment namespace | Handled | **Bug.** Falls back to the filename | High | M6.2 |
| C# partial classes (WebForms code-behind) | Fragments linked by full name; cross-fragment overrides missed, never wrong (`RWT/ingestion/test_csharp_partials.py`) | Absent; field types overwritten per fragment | High | M6.2 |
| C# extension methods | Instance method wins; std receivers refused; ambiguity → no edge (`test_csharp_extension_methods.py`) | Absent | Med | M6.2 |
| C# `?.` calls, generic calls `F<T>()`, `new X().m()` | Query patterns exist; an uncaptured shape is "absent", not low-confidence (`test_csharp_null_conditional_calls.py`) | **Absent** | High | M6.2 |
| C# `global using` / implicit usings / same-namespace scope | From `.csproj`/`.sln` (`test_csharp_same_namespace.py`) | **Absent.** `using` binds only the first segment (`AD/graph/call_graph.py:300`) | High | M6.2 |
| VB.NET code-behind | Supported: `Imports` via MSBuild index, `RootNamespace` (`test_vbnet_resolver.py`) | **Absent** | High | M6.4 |
| Java generics for receiver typing | Strip type arguments to the outer type; refuse single-letter params and builtins | **Bug.** `List<Item>` → `Item` (`AD/parsers/java.py:556,573`); correct for JPA at `:617` | High | M6.3 |
| Java same-package scope; Lombok synthesis | Supported (`test_java_resolver.py`, `test_lombok_synthesis.py`) | Absent | Med | M6.3 |
| Razor `.cshtml` | `@code` projected to C# | Absent | Med | M6.11 (Exploring) |
| WebForms extras: `Page_Load` auto-wire, `.master`, `.asmx`, `.ashx`, `Global.asax`, `<%@ Register %>`, `SqlDataSource` | Not handled (repowise skips `.aspx`) | Partial (`AD/webforms_extractor.py`) | High | M6.11 |
| JSP scriptlets, EL, Struts, `web.xml` servlet mapping | Not handled | Partial (`AD/jsp_extractor.py`) | High | M6.11 |
| Vue/Svelte SFCs | Blanked to valid TS at byte-identical offsets; tag and `@click` edges; `v-for` heads skipped (`RWT/ingestion/test_sfc_source.py`) | **Absent** | High | M6.5 |
| TS barrels / re-exports | Fixed-point through direct, wildcard and renaming re-exports (`test_star_import_barrel.py`) | **Absent** | High | M6.5 |
| Dynamic `import()` | Import edges | Partial | Med | M6.5 |
| Python `__init__` barrels, `importlib` hints | Supported; `__all__` not read | Present (aliases); importlib absent | Med | M6.7 |
| COBOL as an ABAP analog: literal `CALL`/`PERFORM` resolve; dynamic `CALL` deliberately silent | `test_cobol.py::test_dynamic_call_target_is_deliberately_silent` | — | High (pattern) | M7.3 |
| ABAP / SAPUI5 | **None in either repo** | Absent | High | M7.* |
| Tree-sitter grammar/version drift: queries silently compile to nothing | CI "floor" job pins the minimum tree-sitter and compiles every language query; asserts `query is not None` | Absent | High | M0.3 |

## 4. Call resolution

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| Ambiguous name, several candidates | **No edge.** The repo-wide tier needs a unique name, same language, not a builtin, and a callable target | **Fan-out ≤8** (`AD/graph/call_graph.py:15`) | High | M1.12 |
| Unresolved call | No edge; per-language "guessed share" exposed (`RWT/server/mcp/test_call_resolution_basis.py`) | **0.5 edge to the bare name** (`AD/graph/call_graph.py:196`) | High | M1.12 |
| Stdlib name shadowed by a repo file (`import json` → `json.py`) | Shadowing guard (changelog 0.53: 527 false edges) | Absent | High | M1.12 |
| Short-name match across languages; builtin method names (`list.add`) | Language + builtin guards | **Absent** | High | M1.12 |
| Calls vs references vs type-uses | Separate edge kinds; a method passed as a value is a reference, not a call | Single edge kind | Med | M1.12 |
| Receiver typing: locals, params, fields, `new Foo().m()` | Per-language, admitted after a precision audit; two candidate types → none (`test_receiver_types.py`) | Partial (fields + ctor params) | High | M6.2, M6.3 |
| Chained calls `a().b()` | Typed from the declared return type | Absent | Med | M6.3 |
| Inherited `self`/`this` calls | ≤4 ancestors; two branches declaring the method → no edge | Absent | Med | M6.2, M6.3 |
| Interface → implementation | `dispatches_to` per subtype; private/ctor excluded; **refused above 24 impls** | Partial: only a single impl | High | M6.7 |
| Lambdas / callbacks / closures | Recorded as references (some languages); closures are a measured recall miss | Absent | Med | M6.7 (Exploring) |
| Reflection / string dispatch (`Class.forName`, `Activator.CreateInstance`, `importlib`) | `dynamic_uses` hint edges: used for reachability, **never for flows** | Absent | Med | M6.7 |
| Overloads | One symbol id per overload set → not an ambiguity | Last overload wins | Low | M6.2, M6.3 |
| Precise index tier (SCIP/LSP) | None | **Ahead.** Stage-0 `symbol_index.json` import, but no producer | Med | M6.13 (Exploring) |

## 5. Framework / DI adapters

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| Spring annotations: `@Autowired`/`@Inject`/`@Resource`, ctor injection (incl. Lombok), `@Bean` | Symbol-level edge only when the type is unambiguous (`test_spring_framework_edges.py`) | Partial (`AD/parsers/java.py:537,635`) | High | M6.7 |
| Spring XML beans, `@Qualifier`/`@Primary` | **Not handled** | Absent | High | M6.7 |
| ASP.NET Core `AddScoped<I,T>` etc. | Hint links interface → impl (`test_csharp_framework_edges.py`) | Partial (ctor param types only) | High | M6.7 |
| Classic ASP.NET MVC5 / Web API 2 / WebForms | Not covered (gated on `Microsoft.AspNetCore`) | Partial (routes, WebForms events) | High | M6.11 |
| Unity / Autofac containers | Not handled | Absent | Med | M6.7 |
| Jakarta: JAX-RS, CDI, `@WebServlet`, EJB, JPA | Supported; no `web.xml` | Absent | High | M6.11 |
| Express / Nest routing and modules | Supported | Partial | Med | M6.6 |
| Angular router + DI | `framework_binds` from router config and decorators | Partial | Med | M6.6 |
| Vue / React routing | **No detector** | **Ahead** (`AD/route_parser.py`, `AD/menu_detector.py`) | — | keep |
| Django / Flask / FastAPI | urls→views, blueprints, routers | Partial | Med | M6.6 |
| Event emitters / pub-sub | Channel links (Quarkus), cross-repo topics; no in-process emitter | Partial (callee-name heuristic) | Med | M8.3 (runtime), M6.7 |
| MyBatis mapper XML | Not handled | Absent | High (Unverified prevalence) | M6.9 |
| SAPUI5 manifest routing, OData | None | Absent | High | M7.5 |

## 6. SQL / DB

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| DDL parsing | sqlglot; per-config dialect; malformed SQL degrades to no symbols (`test_sql_handler.py`) | Regex (`AD/extractors/sql_extractor.py`) | High | M6.8 |
| SQL in string literals | Only strings with a SQL verb; sqlglot first, regex fallback; interpolated names skipped | Partial (regex) | High | M6.8 |
| `.sql` skip rule | — | **Bug.** Checks the absolute path, so a clone under `/build/` loses all SQL | High | M6.8 |
| ORM → table (`@Table`, `__tablename__`, EF `[Table]`/DbSet) | Convention-inferred names get lower confidence | Partial | High | M6.8 |
| Stored procedure table read/write; `EXEC` / CallableStatement / `CommandType.StoredProcedure` | **Absent in repowise** | Absent | **High** | M6.10 |
| Column-level lineage | Planned upstream | Absent | Med | Exploring |

## 7. Graph algorithms and blast radius

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| Entry-point detection | Weighted: fan-out .35, in-degree .25, visibility .20, name tier .15, framework .05; threshold 0.3; tests excluded | Substring name hints over-trigger (`AD/graph/call_graph.py:558`) | High | M6.12 |
| Flow tracing | Primary successor only; depth 8; ≤50 flows; drops weak origins; 6 named stop reasons | BFS depth 5 enqueues unresolved/fanned edges; no stop reason | High | M1.12 |
| Hub explosion (logging/utils) | Structural: no fan-out, dispatch cap 24, PR blast depth 3 | Partial (fan-out 8) | High | M5.2 |
| Cycles | Tarjan SCC on the file graph; co-change excluded | Absent | Low | M5.2 (cycle-safe traversal) |
| Communities | Leiden, Louvain fallback, fixed seed, giant-community split | Partial (Louvain for batching) | Med | M3.4 |
| Dead-code false positives: DI/convention roots, framework registration, reflection cap 0.40 | `RWD/layers/DEAD_CODE.md` | n/a (no dead-code feature); same knowledge needed for entry points | Med | M6.12 |
| External entry points (ABAP TCODE/RFC/jobs, like COBOL JCL) | Suppress reachability verdicts | — | High | M7.3 |

## 8. Incremental update

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| An extractor or parser change alters every file | Fingerprint includes extractor version | Absent | High | M1.8 |
| Deleted files | Tombstoned and pruned from every table, incl. FTS (`test_tombstone_*`) | Old `.md` never swept | Med | M3.5 |
| Renames | Git rename status + symbol-rename heuristic | Cache miss (re-paid) | Med | M1.9 |
| Working-tree (uncommitted) changes | Included | **Absent.** Same SHA → "already scanned" (`AD/pipeline.py:401-436`) | Med | M2.3 |
| Force-push / rebase (base commit gone) | Drop the marker, fall back to a full diff | **Bug.** `git pull` with `check=True` fails on divergence; no timeouts (`AD/repo/resolver.py:108-160`) | Med | M0.6, M1.2 |
| Cascade budget for huge diffs | Adaptive budget; stale pages listed; oldest regenerated first | Absent | Med | M3.7 |
| Staleness propagates up the hierarchy (file → module → overview) | Fixed in 0.51 | Absent | High | M3.5 |
| Full vs incremental parity | Integration tests: full == update == resume | Absent | High | M1.9, M3.6 |
| Idempotence: two full runs byte-identical | `test_wiki_idempotence.py` | Absent | High | M3.5 |
| Config change with an unmoved commit | Config fingerprint forces re-score | Absent | Med | M1.8 |

## 9. Resume, locking, schema

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| Phase resume | Ledger per phase; RUNNING after a crash → redo | Present (`phase_checkpoints`) | — | M1.8 |
| Resume never pays twice | Skip set from ground truth (stored pages), not job JSON; stubs retried | **Bug.** Failed placeholders count as done (`AD/ai/summarizer.py:143-155,453`) | High | M0.7 |
| Resume mixes commits | — | **Bug.** Falls back to the latest same-branch run with no SHA check (`AD/pipeline.py:262-290`) | Med | M0.6 |
| Resume path identity | — | **Bug.** Raw arg vs stored absolute path (`AD/pipeline.py:359` vs `AD/repo/resolver.py:198`) | High | M0.6 |
| Concurrent runs | Single-flight lock: exclusive create, pid + token, dead-PID eviction, pending marker | **Absent** | Med | M1.4 |
| WAL / busy timeout | Yes | **Present** (`AD/db.py:332-395`) | — | — |
| Migrations | 77 Alembic revisions + additive reconcile; store-format verdicts | Partial: forward-only if-blocks (`AD/db.py:430-530`) | Med | M1.4 |
| FTS5 can't be altered | Rebuild path | n/a | Med | M1.5 |
| Doctor/repair | 19 checks; repair FTS/vector drift | Absent | Med | M2.5 |

## 10. LLM

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| Provider outage | Stub at confidence 0.3; never overwrites prose; no version churn | Placeholder persisted and reused | High | M0.7, M3.7 |
| Truncated output (`stop_reason=max_tokens`) | Invalid and not retryable | **Bug.** Flag dropped (`AD/shared/llm_router.py:460`, `AD/ai/llm_client.py:137-157`) | High | M0.7 |
| Junk output (empty, repetitive, addresses the prompter) | One corrective re-ask, then a stub | Partial (tolerant JSON extraction) | Med | M3.8 |
| Hallucinated paths, symbols, links | Validated against the parsed index | **Present** (`AD/ai/prose_validator.py`, `AD/ai/self_review.py`) | — | M3.8 |
| LLM-written mermaid | Sanitizer post-pass | Partial | Low | M3.8 |
| Context overflow | Ranked truncation, never unranked | Partial; oversized functions sent whole (`AD/ai/chunker.py:94-127`) | Med | M3.7 |
| Rate limits | 429 → honour Retry-After (6 tries); 5xx 3 tries; 4xx fail fast | Substring-based, 3 tries (`AD/shared/llm_invoke.py:26-40`) | Med | M1.11 |
| Per-provider quirks (temperature, timeouts) | Capability matrix; flat 30 s timeout broke local models | Partial | Med | M1.10 |
| Embedder silently falls back to mock | Fixed upstream; embedder pinned to store | Absent | Med | M1.5 |
| Cost accounting with cache fields | Sum all usage fields; cost writes kept off the hot path | Partial | Med | M1.11 |
| Reuse key | Content hash of inputs + model + prompt version | Tier-1 only; key lacks model/prompt (`AD/ai/summarizer.py:566-572`) | High | M0.7, M3.7 |
| Tier-2/3 reuse across scans | Content-hash reuse | **Absent** (`AD/ai/rollup.py:631-660`, `AD/ai/flow_analyzer.py:547-584`) | High | M3.7 |
| Prompt caching | `cache_control` on the shared system prefix | Absent | Med | M1.11 |
| Deterministic grouping; LLM names only | Concept tree | Partial (Louvain batching) | Med | M3.4 |
| Pre-run cost estimate | Calibrated estimate with a range | Absent | High | M1.11, M2.4 |
| Budget for all providers | n/a | **Bug.** Bedrock only (`AD/pipeline.py:229-259`) | High | M0.5 |
| Secrets in source sent to the LLM | **Not handled in repowise either** | Absent | Med | M1.13 (Exploring) |

## 11. MCP contract (for M5)

| Edge case | repowise approach | Item |
|---|---|---|
| Errors | Shield: never `isError`; success-shaped payload with a remedy (not indexed, stale schema, unknown target) | M5.3 |
| Payload size | Rank, then cap; true totals; token budget ~8k tokens per response; omitted blocks recorded | M5.1 |
| Unresolved target | Error, never a confident zero (changelog 0.52) | M5.1 |
| Certainty wording | `may_break`, not `will_break` (BREAKING rename in 0.47) | M5.2 |
| Freshness | `_meta`: index commit, `index_behind`, `stale_warning` only when served files changed | M5.1 |
| Provenance in answers | `evidence_kind` per item; co-change never shown as a dependency | M5.2 |
| Numbers | 4 significant figures; NaN → null | M5.3 |
| Path safety | `resolve()` + `relative_to`; Host allowlist on HTTP | M5.3 |
| Tool surface | Small default set; specialists opt-in; six overlapping tools removed in 0.21 | M5.3 |
| Startup | Lazy tool import; a failing module is skipped, not fatal | M5.3 |

## 12. CLI / UX

| Edge case | repowise approach | AD status | Item |
|---|---|---|---|
| Contradictory flags | Explicit `UsageError` naming the conflict | Partial | M2.1 |
| No TTY / stdin EOF | Defaults or an actionable error | Absent | M2.2 |
| Secret redaction in warnings and state | `redact_secrets`; 0600 files | **Bug** (see Security) | M0.8 |
| Machine-readable progress | `--progress json` NDJSON with `degraded`/`outcome` | Absent | M2.1 |
| Failed vs zero | "Failed" and "zero" must look different (48 silent-failure fixes upstream) | **Bug.** `verify-drift` not-found exits 0; chat silently skips docs | M0.9, M2.6 |
| Dry runs read-only | No locks, no writes | n/a | M2.4 |
| Wheel packaging | Subpackages missing from the wheel and a web tarball 404 were shipped bugs → install-from-wheel smoke test | Absent | M4.5 |

## 13. Security

| Edge case | repowise approach | AD status | Pri | Item |
|---|---|---|---|---|
| API keys persisted in DB/state | Never; masked | **Bug.** `scan_runs.config_json` contains keys (`AD/pipeline.py:452-462`) | High | M0.8 |
| Git tokens | n/a | **Bug.** Token in the clone URL persists in `.git/config` (`AD/repo/resolver.py:28-42`) | Med | M0.8 |
| Secrets masked *before* truncation | Fixed in 0.53 (head stored raw) | n/a | Med | M0.8 |
| Server auth / bind | API key with `compare_digest`; loopback-peer check; warning on exposed bind | No auth; no warning on `0.0.0.0` (`AD/viewer/server.py`) | Med | M4.1 |
| Pickle caches | HMAC-sealed (CWE-502) | n/a; avoid pickle | Low | M1.9 |

## 14. Benchmark method

| Practice | repowise | AD status | Item |
|---|---|---|---|
| Precision **and** recall always paired | Yes | Recall only for call edges (`tests/corpus/metrics.py:75-83`) | M0.11 |
| Compiler oracle where one exists (tsc; Go rta) | Yes; oracle-silent edges reported separately and charged to no tool | Absent | M6.13 |
| Seeded, stratified hand audit (30 rows / language / origin), all verdicts published | Yes | Absent | M0.11 |
| No pooled recall across repos with different entry-point counts | Rule | Pooled | M0.11 |
| Sealed split; pre-register before spending | Rule | Absent | M0.11 |
| De-saturate fixtures (baseline at 1.0 discriminates nothing) | — | **Bug.** `tests/corpus/baseline.json` saturated | M6.13 |
| Miss decomposition (dynamic dispatch, closures) | Yes | Free-text note | M6.13 |
| Agent-loop eval with a bare-agent control; output tokens as the metric | Yes | Absent | M5.7 |
| Blast-radius accuracy via historical replay (Inference) | Not done upstream | Absent | M5.7 |

## 15. Process lessons (from 1,916 commits / 432 changelog fixes)

| Lesson | Evidence | Applied in |
|---|---|---|
| No bare excepts; count and surface every degradation | ~48 "silent" fixes | M1.8 (degradations table), M2.3 |
| Don't give every artifact two generation modes; structural pages are always deterministic | 0.35 removal of the deterministic/AI axis | ADR-0005, M3.* |
| Build uninstall together with install | 0.42: "init wrote to six surfaces and nothing took them away" | M5.3 |
| Measure hooks and injected context against a base rate | 0.40: retired nudge hook (11.4% vs 11.9% base) | M5.8 |
| Field names carry no false certainty | `will_break` → `may_break` | M5.2 |
| Duplicate-constant guards (single source of rules) | `test_no_*_copies.py` | M1.12, M6.1 |
| Golden files regenerate only with a reviewed intent | perf_corpus README | README protocol step 6 |
| Status honesty on the roadmap: "blocked", not "planned" | ROADMAP.md style | ROADMAP.md |
| Runtime data was *not* built upstream (only a one-line reason) | ROADMAP "not building" | ADR / M8.3: separate provenance; "not observed" is never negative |
| Candidate vs accepted knowledge are distinct states | Decisions fixes | M3.8 (unverified claims flagged) |
