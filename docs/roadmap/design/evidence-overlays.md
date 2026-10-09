# Design: Evidence Overlays (git history, coverage, runtime traces)

- **Status:** Proposed. M8.1–M8.3 are Planned, M8.4 is Exploring.
- **Last updated:** 2026-10-08
- **Related items:** M8.1–M8.4. Depends on M1.12 (`evidence_kind`). Consumed by M5.2 (impact) and M3.x (pages).
- **Inputs:** `../research/research-doc-review.md` (verification of the OTel/eBPF claims)

Static analysis misses dynamic dispatch, DI by string, pub/sub and loose DB coupling (`docs/new-requirement-research.md`). Overlays add **evidence from outside the AST**. Repowise explicitly decided not to build runtime or APM data ("Everything here is derived from source and git history"). For ai-discovery this layer is a differentiator, but only if these provenance rules hold.

## 0. Provenance rules (non-negotiable)

1. **Separate `evidence_kind`.** `co_change`, `coverage` and `runtime` rows sit beside `static`, `framework` and `scip` edges. They never change static confidence and are never merged into one score at write time. Ranking may combine them at query time and must show why.
2. **"Not observed" is never negative evidence.** A static edge with no runtime observation only means the path was not exercised. It never feeds reachability or "unused" verdicts.
3. **Every overlay row is labelled** with its source (file/run id), environment (`test`, `staging`, `prod`), time window, and the commit it was collected against. A row collected against a different commit is shown as `stale_evidence`.
4. **Static output stays reproducible.** Two scans of the same commit with the same config produce byte-identical static pages whatever overlays are present. Overlay facts render in a separate, clearly labelled section ("Observed at runtime").
5. **Co-change is not a dependency.** Repowise had to fix exactly this, where co-change partners were served as imports. Impact answers list co-change under "often changes with", never under "may break".

## 1. Git history layer (M8.1)

`src/ai_discovery/overlays/git.py` (new) collects:
- **Churn:** a decayed commit count per file and per symbol (via line ranges).
- **Ownership:** blame-based. `%aN/%aE` honours `.mailmap`.
- **Co-change pairs:** support and confidence.
- **Recent fix commits:** messages matching fix/bug keywords; counts and recency only.

**Noise filters:**
- `git log --no-merges`;
- skip bot authors (dependabot, renovate, CI);
- ignore commits touching more than 200 files for co-change (mass reformat);
- honour `blame.ignoreRevsFile` if configured;
- follow renames (`--follow`).

**Scale guards:** a per-file commit cap (500), a co-change window (2,000 commits), and shallow-clone detection with a warning.

**Consumers:** impact ranking tie-breaks, a hotspot section on module and file pages, and `get_impact` "often changes with".

## 2. Coverage ingestion (M8.2)

This is the cheapest runtime evidence: test suites already exercise business logic, and no code change is needed.

| Format | Producer | Per-test? |
|---|---|---|
| coverage.py JSON / SQLite with **dynamic contexts** (`--cov-context=test` / `dynamic_context = test_function`) | Python | Yes |
| JaCoCo XML/exec | Java (Maven/Gradle) | Aggregate by default. Per-test needs one session per test or listeners **(Unverified approach)** |
| Cobertura XML | .NET coverlet, many others | Aggregate |
| LCOV | istanbul/nyc, c8 (V8), many | Aggregate. Per-test only if run one test at a time. |

Mapping:
- Covered line ranges → enclosing symbols (from `code_nodes` line spans) → `executes` rows (test → symbol) with `evidence_kind=coverage`.
- Aggregate reports produce symbol-level "covered" facts, **labelled as aggregate**. A per-test claim from aggregate data is never made.

Uses:
- `impacted-tests` for a change (MCP `get_change_impact`);
- confirming that a static path is exercised;
- surfacing statically unreachable but covered code. That is a resolution miss, which feeds the M6 bench.

## 3. OTLP / Jaeger span ingestion (M8.3)

**What zero-code auto-instrumentation actually gives (verified):** **request-level spans**:
- inbound HTTP/RPC server spans (route template),
- outbound HTTP client spans,
- DB client spans (statement / table, depending on semantic-convention version and sanitization),
- messaging spans.

It does **not** give method-level call chains through your own services and repositories. The research doc's claims to the contrary are wrong. eBPF tools (Grafana Beyla, now OpenTelemetry eBPF Instrumentation/OBI) are likewise protocol/transaction-level only.

**Edges derived** (`evidence_kind=runtime`):
- `route → table` (read/write by statement verb when available);
- `route → external_endpoint`;
- `route → queue/topic`;
- `service → service` (distributed).

These fill the DB and interface blast radius that static SQL extraction misses (ORM-generated SQL, EF Core/LINQ, dynamic queries).

**Input:** OTLP JSON (file exporter or collector file export) and Jaeger JSON export, ingested with `discover evidence import <file>` (the `evidence` command, delivered by M8).

**Mapping spans to code:**
- server span `http.route` → endpoint node (from `extractors/` route detection);
- `code.function`/`code.namespace` attributes when present (some instrumentations emit them);
- otherwise route-level only.

### Verified zero-code setup per stack (for the docs and the `doctor` hint)

| Stack | Setup | Yields |
|---|---|---|
| Java | `java -javaagent:opentelemetry-javaagent.jar -jar app.jar` | Server/client/JDBC spans. Method spans **only** if listed via `otel.instrumentation.methods.include=pkg.Class[m1,m2]` (manual list) or `@WithSpan` (a code change). |
| Node.js | `NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register"` | Spans for supported libraries only |
| Python | `opentelemetry-instrument python app.py` (opentelemetry-distro) | Spans for supported libraries **(Unverified detail per framework)** |
| .NET (Core/5+) | Official `OpenTelemetry.AutoInstrumentation` install script (`instrument.sh`/PowerShell module). Env vars include `CORECLR_ENABLE_PROFILING=1`, `CORECLR_PROFILER={918728DD-259F-4A6A-AC2B-B85E1B658318}`, `CORECLR_PROFILER_PATH=<install>/<platform>/OpenTelemetry.AutoInstrumentation.Native.<ext>`, `DOTNET_STARTUP_HOOKS=<install>/net/OpenTelemetry.AutoInstrumentation.StartupHook.dll`, plus `OTEL_DOTNET_AUTO_HOME`, `DOTNET_ADDITIONAL_DEPS`, `DOTNET_SHARED_STORE`. **Use the official script rather than hand-set variables.** | ASP.NET Core, HttpClient, SqlClient (EF Core partial). **No spans for your own service/repository methods.** |
| .NET Framework 4.6.2+ / IIS | PowerShell module: `Install-OpenTelemetryCore`, then `Register-OpenTelemetryForIIS`. `COR_PROFILER` uses the same GUID `{918728DD-259F-4A6A-AC2B-B85E1B658318}`. | ASP.NET (System.Web), SqlClient, WCF. Library level only. |

The GUID `{91A16E64-…}` in `docs/new-requirement-research.md` appears to be fabricated. Never copy it.

Source: the opentelemetry-dotnet-instrumentation `docs/config.md` and opentelemetry.io zero-code docs, as checked in the 2026-10-08 research pass. Re-verify against the current docs when implementing M8.3.

**Recommended collection environment:** run the existing test suites (unit and integration) with the agent attached in CI or locally. This gives zero production risk. Spans then also carry the test name, via resource attributes or the root span name **(Inference)**.

## 4. Profiler stacks (M8.4, Exploring)

**Method-level** runtime edges need sampled or instrumented stacks:
- .NET `dotnet-trace` → speedscope;
- Java JFR / async-profiler → collapsed stacks;
- Python `sys.monitoring` or `py-spy` → speedscope;
- Node `--cpu-prof`;
- Go/Rust pprof.

Ingest speedscope, pprof and collapsed formats. Adjacent frames give `calls` edges with `evidence_kind=runtime`, and frames are matched to `code_nodes` by qualified name.

Sampling misses short calls, which is another reason rule 2 applies. code-graph-rag's `cgr trace` demonstrates the test-tracer approach (its own tracers plus eBPF profiler pprof). It does **not** ingest OTel spans.

## 5. Store and API sketch
- `evidence_rows(id, scan_id, kind, src_node, dst_node, attrs_json, source_ref, environment, window_start, window_end, collected_commit)` (new, via a migration in the M8 items). This row model is also usable for `co_change` and `coverage`.
- `query/impact.py` returns overlay findings in separate arrays (`observed_runtime`, `covered_by_tests`, `often_changes_with`). They never appear in `may_break` unless a static or framework edge also exists.
