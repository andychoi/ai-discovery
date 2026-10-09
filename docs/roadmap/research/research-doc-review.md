# Review of `docs/new-requirement-research.md`

- **Status:** Accepted
- **Last updated:** 2026-10-08
- **Related items:** M5.2, M5.5, M5.6, M5.7, M8.1–M8.4, M1.12, M6.*

**Method:**
- Each external claim was checked against the project's GitHub repo or official docs. Repo stats come from the GitHub API on 2026-10-08.
- Claims about the owner's own benchmark (Sonnet "early stop", GLM recall) can't be checked externally and are labelled **UNVERIFIED (owner data)**.

**Statuses:**
- **VERIFIED:** correct as stated.
- **PARTIAL:** true with important caveats.
- **WRONG:** contradicted by the source.
- **UNVERIFIED:** not checkable.

## 1. Tools and projects

| Claim in the doc | Reality | Status | Source |
|---|---|---|---|
| **repowise** "simply concatenates files or flattens directories"; its graph is a "fake" text graph | It builds a two-tier file/symbol graph with 37 resolution origins, plus git, health, decision and wiki layers; prompts receive assembled facts. The doc describes **repomix** ("packs your entire repository into a single, AI-friendly file"). The "fake graph" remark is only partly fair: the weakest edges are unique-name matches at 0.50 | **WRONG** (confused with repomix) | github.com/repowise-dev/repowise; github.com/yamadashy/repomix; `../repowise/docs/BENCHMARKS.md` |
| **vitali87/code-graph-rag**: tree-sitter + Memgraph, NL → Cypher, dead code, AST patching | True. MIT, ~5.2k stars, also Qdrant, compiler frontends, MCP server | VERIFIED | github.com/vitali87/code-graph-rag |
| code-graph-rag merges runtime traces (eBPF / test traces) | It has `cgr trace`, using its **own tracers**: pytest `sys.monitoring`, a JVM agent (JDK 24+), Node `--cpu-prof`, .NET `dotnet-trace` speedscope, Go/Rust pprof. It can read eBPF *profiler* pprof (Parca/Pyroscope). Edges are tagged with the test that produced them | PARTIAL | .../docs/guide/dynamic-tracing.md |
| Feed OTel trace JSON into code-graph-rag; it correlates `SpanKind.SERVER`/`ActivitySource` with source | No OTLP/span ingestion exists | **WRONG** | same |
| **Abhishek-Aditya-bs/CodeGraph**: Neo4j + hybrid vectors | True, but tiny (22 stars, 17 commits); the impact-analysis and token-saving claims have no backing | PARTIAL | github.com/Abhishek-Aditya-bs/CodeGraph |
| **DeusData/codebase-memory-mcp**: C, SQLite graph, static binary, 17 tools incl. `get_architecture`, `trace_path`, `detect_changes`, `search_graph` | Exactly 17 tools; all four names exist. Fully static builds only on Linux `-portable`. "Linux kernel in 3 min" is self-reported. Its `ingest_traces` tool is a **stub** | VERIFIED (with caveats) | github.com/DeusData/codebase-memory-mcp |
| **ast-grep/ast-grep-mcp**: official experimental MCP | True; 4 tools (`dump_syntax_tree`, `test_match_code_rule`, `find_code`, `find_code_by_rule`) | VERIFIED | github.com/ast-grep/ast-grep-mcp |
| ast-grep-mcp / codebase-memory-mcp check lint or type breakage "in real time" | Syntax matching only; no type checking or diagnostics. Use an LSP plugin or the compiler | **WRONG** | same |
| **yailPeralta/ast-mcp-server**: ts-morph, references and diagnostics | True; 16 tools, ISC, **4 stars**. Citation [1] points to a different project (`angrysky56/ast-mcp-server`) | VERIFIED (low adoption; citation mismatch) | github.com/yailPeralta/ast-mcp-server |
| **2015xli/clangd-graph-rag** | Real: clangd index + Clang → Neo4j; git-diff updates; Apache-2.0. Its own Linux-kernel run takes ~4 h | VERIFIED | github.com/2015xli/clangd-graph-rag |
| **future-agi/traceAI**: OTel code observability for backend transaction graphs | It traces **LLM applications** (prompts, tokens, agent steps), not backend code | **WRONG** | github.com/future-agi/traceAI |
| **o11y-dev/opentelemetry-skill**: a "cognitive router" compressing spans via OTTL | A SKILL.md + reference docs that help an AI write OTel config; it processes no spans | **WRONG** | github.com/o11y-dev/opentelemetry-skill |
| **ImmersiveFusion/opentelemetry-tracegen**: synthetic traces with GenAI conventions | Redirects to `ImmersiveFusion/snowglobe`: synthetic OTLP for a simulated 28-service system; unrelated to your code | PARTIAL (renamed) | github.com/ImmersiveFusion/snowglobe |
| **agamm/awesome-ai-sre**: list incl. HolmesGPT, K8sGPT | True; SRE/incident focus, not code intelligence | VERIFIED (relevance overstated) | github.com/agamm/awesome-ai-sre |
| Anthropic blog: agentic search beats stale RAG indexes | Exists (2026-05-14). It says embedding pipelines go stale; agentic search "works best when Claude has enough starting context"; LSP avoids wrong-symbol pattern matching; recommends codebase maps and subagents. It criticises embedding RAG, **not** structural graph indexes | VERIFIED (scope narrower than implied) | claude.com/blog/how-claude-code-works-in-large-codebases-best-practices-and-where-to-start |
| Claude Code "uses local language servers" by default | Only with a code-intelligence plugin installed ("The LSP tool is inactive until you install a code intelligence plugin") | PARTIAL | code.claude.com/docs/en/features-overview |

## 2. Runtime tracing claims

| Claim in the doc | Reality | Status | Source |
|---|---|---|---|
| eBPF (Grafana Beyla / Pixie) extracts which **functions/classes** a request passed through on the way to SQL | Beyla docs: "only provides generic metrics and transaction level trace span information". OBI's support matrix covers protocols only (HTTP, gRPC, SQL drivers, Redis, Kafka, Mongo). You get **request → SQL spans, not the methods in between**. Beyla was donated to OTel as OBI (2025) | **WRONG** | grafana.com/docs/beyla/latest/; github.com/open-telemetry/opentelemetry-ebpf-instrumentation (SUPPORT_MATRIX.md) |
| Beyla gives a complete runtime topology for .NET on Linux | Docs list .NET tracing as limited when .NET is the call-chain entry point | WRONG / overstated | grafana.com/docs/beyla |
| OTel .NET auto-instrumentation: `CORECLR_PROFILER` / `COR_PROFILER` = `{91A16E64-D37F-49E8-924E-A340397E06CC}` | Official value is **`{918728DD-259F-4A6A-AC2B-B85E1B658318}`**. The doc's GUID returns no search hits (appears fabricated); with it, the profiler would not attach | **WRONG** | github.com/open-telemetry/opentelemetry-dotnet-instrumentation/blob/main/docs/config.md |
| `DOTNET_STARTUP_HOOKS` / profiler paths | Official layout: `$INSTALL_DIR/net/OpenTelemetry.AutoInstrumentation.StartupHook.dll`; the native profiler lives under an OS/arch folder (e.g. `linux-x64/`). Use the official `instrument.sh` rather than hand-setting variables | PARTIAL | same |
| .NET Framework + IIS via machine-wide `COR_*` + `iisreset` | .NET Framework 4.6.2+ is supported; the documented route is the PowerShell module (`Install-OpenTelemetryCore`, `Register-OpenTelemetryForIIS`) | PARTIAL | opentelemetry.io/docs/zero-code/dotnet/ |
| OTel captures "every internal method chain"; spans prove `SqlUserRepository` was called | Instrumentation is **per library**: ASP.NET (Core), HttpClient, SqlClient, WCF; EF Core partial. Your own service/repository methods get **no spans** | **WRONG** | .../docs/internal/instrumentation-libraries.md |
| Java `-javaagent:` one-liner; Node `NODE_OPTIONS=--require @opentelemetry/auto-instrumentations-node/register` | Both commands are correct | VERIFIED | opentelemetry.io/docs/zero-code/java/agent/, opentelemetry.io/docs/zero-code/js/ |
| These agents yield the real "function A → module B → API C" stack | Edges only (inbound request, outbound HTTP/DB). Java method spans need `otel.instrumentation.methods.include` (listed by hand) or `@WithSpan` (a code change) | **WRONG** | opentelemetry.io/docs/zero-code/java/agent/annotations/ |
| Running OTel only during tests is the safest, most realistic option | Sound. But test runs + OTel still give request-level edges. Method-level data needs per-test **coverage** (coverage.py contexts, JaCoCo, coverlet, istanbul/V8) or profilers | PARTIAL | (Inference, consistent with sources above) |

## 3. Architecture and strategy claims

| Claim | Assessment | Status |
|---|---|---|
| Sonnet stops exploring early (20–30 s); GLM explores 2–3× more | Owner's benchmark; no external source | UNVERIFIED (owner data) |
| Non-native MCP wrappers get bypassed when native tools exist | Plausible and consistent with repowise's measured hook base rates (e.g. a nudge followed 11.4% vs an 11.9% base rate). Not a general law: repowise reports agent-loop token savings with its MCP | PARTIAL |
| Static AST misses DI, reflection, events and FK-less schemas | Correct; repowise's published miss decomposition attributes most Go recall loss to dynamic dispatch (+ closures) | VERIFIED |
| Combine static graph + runtime evidence | Sound; code-graph-rag does it with test-run tracers | VERIFIED |
| Compiler, linter and tests as the final truth (`tsc`, `mvn compile`, `cargo check`) | Sound; matches the Anthropic blog's LSP/verification advice | VERIFIED |
| Depth limits, cycle detection, impact weighting (type ref < call < data write) | Sound graph practice. **Correction:** PageRank is not cycle detection; use a visited set in BFS/DFS (SCC for analysis) | PARTIAL |
| "Force-pin ≤5 target files" | Arbitrary and conflicts with an honest blast radius. Prefer ranked lists with true totals + an explicit "unknowns" section | PARTIAL |
| Use a graph DB (Neo4j/Memgraph) with Cypher | Not required: repowise and codebase-memory-mcp both use SQLite successfully. **Decision: SQLite** | Rejected (design choice) |
| Build BA-agent orchestration (LangGraph/CrewAI) | **Decision:** MCP tools + Claude Code skills only (lightest of the doc's three options) | Rejected for v1 (owner decision) |

## 4. What ai-discovery adopts, and where

| Idea from the doc | Adapted form | Item |
|---|---|---|
| Custom MCP "impact analyzer" for Claude Code | MCP `get_impact` / `get_change_impact` with ranked, provenance-labelled, depth-capped, cycle-safe results; three-state answers | M5.2, M5.3 |
| Multi-tier recursive trace prompt to counter early stop | `discover-impact` skill: tiered protocol with explicit stop rules (reach entry point / screen / table, or depth cap) and citations of tool results | M5.5 |
| BA agent → IMPACT_REPORT.md + IMPLEMENTATION_PLAN.md → Claude Code | `discover-change-plan` skill generating both files, with pinned files and per-stack compile/test commands | M5.6 |
| Compiler/test self-healing loop | Verify commands in the plan template; Claude Code runs them (no built-in orchestrator) | M5.6 |
| CLAUDE.md "legacy constraints" section | Skill writes or updates a constraints block on request (Exploring) | M5.6 |
| git diff → blast radius | `get_change_impact(diff|range|working tree)` | M5.2 |
| Runtime evidence, zero code change | Per-test coverage ingestion (method-level) + OTLP/Jaeger span ingestion (endpoint ↔ SQL/external) + optional profiler stacks; **separate provenance**; "not observed" is never negative evidence | M8.2, M8.3, M8.4 |
| Impact weighting by edge kind | Ranking order: data write > call > dispatch/framework > type use; co-change shown separately | M5.2, M1.12 |
| Prove it works | Agent-loop eval vs bare Claude Code; historical-replay blast-radius accuracy (Inference) | M5.7 |

## 5. Additional projects worth benchmarking (verified to exist)

| Project | Notes | License caveat |
|---|---|---|
| Serena (oraios/serena) | LSP-backed MCP; symbol find/references for 40+ languages | GPL-3.0 (LSP component MIT) |
| GitNexus (abhigyanpatwari/GitNexus) | Tree-sitter + LadybugDB; impact tool with depth grouping and confidence; Cypher | **PolyForm Noncommercial**, so check before commercial use |
| SCIP (scip-code/scip) | Compiler-accurate index format. A candidate producer for ai-discovery's Stage-0 `symbol_index.json` (per-language indexer availability Unverified) | Apache-2.0 |
| Joern | Code property graphs (C/C++/Java/JS/Python/Kotlin) | Apache-2.0 |
| CodeQL | Query libraries MIT; CLI licence more restrictive (Unverified) | — |
| Kythe | Cross-reference indexing; maintained | Apache-2.0 |

## 6. Action for the source document

`docs/new-requirement-research.md` keeps its body unchanged and gets a banner that points here. Do **not** copy its .NET setup snippets: the GUID is wrong and the paths are incomplete. Use `../design/evidence-overlays.md` (new) instead.
