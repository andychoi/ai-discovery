# repowise Benchmark — What to Adopt, Adapt, or Skip

- **Status:** Accepted (research input to the v1 roadmap)
- **Last updated:** 2026-10-08
- **Related items:** M0.11, M0.12, M1.*, M2.*, M3.*, M4.*, M5.*, M6.*, M8.1
- **Sources:**
  - `../repowise` at HEAD `bcf4d96e`: v0.53.0, AGPL-3.0-or-later.
  - Read-only exploration of its code, docs, CHANGELOG and tests.
- **Clean-room rule (ADR-0006):** no repowise code enters this repo. This page describes *behaviour and design*, never implementation.

## 1. What repowise is (facts)

- **Positioning:** "The codebase intelligence layer for your AI coding agent". It provides a dependency graph, git history, dead code, decisions and code health, exposed over MCP.
- **It is not a file concatenator.** `docs/new-requirement-research.md` confused it with repomix.
  - The pipeline runs ingestion (tree-sitter AST → two-tier file/symbol graph), then git indexing, analysis, generation and persistence.
  - Results go into SQLite: 48 tables, with Alembic migrations.
  - LLM prompts receive assembled facts, never raw file dumps (`../repowise/docs/layers/WIKI.md`).
- **Code base:**
  - Monorepo: Python core/cli/server (~242k LOC core) plus TypeScript web/ui/types/api-client/vscode.
  - Web stack: Next.js 15 standalone + React 19 + FastAPI.
  - Graph stack: sigma/graphology + ELK; mermaid for diagrams.
- **Languages:** 40, of which 26 have a full AST.
  - 25 tree-sitter query files, plus SFC (Vue/Svelte) projection and sqlglot SQL.
  - No ABAP, UI5, WebForms or JSP support.
- **Graph:**
  - 17 edge types.
  - 37 resolution origins (docs say 29), each with a single fixed confidence.
  - 24 framework modules (docs say 22).
  - Leiden communities, PageRank, betweenness, SCC and execution flows.
- **Benchmarks:** published as precision/recall pairs against compiler oracles (Go `callgraph/rta`, `tsc`), plus seeded hand-graded samples (`../repowise/docs/BENCHMARKS.md`).
- **Not built, on purpose:** runtime/APM data, an LLM PR reviewer, paywalled languages, dates on the roadmap.

## 2. Side-by-side

| Area | ai-discovery today | repowise | Gap |
|---|---|---|---|
| Purpose | Brownfield reverse-engineering into SDLC/business docs (ASIS, BPMN, DMN, EARS, screens) | Code intelligence for coding agents, plus a wiki | Different purposes. ai-discovery's business layer is unique. |
| CLI | 13 commands, 69 options, `-p` required on 7, flag meanings inconsistent, no `--version`/`status`/`doctor` | 44 commands, but a 3-command quickstart; `init` has a menu, a cost gate and "What's next" | **UX** |
| First run | `init` writes yaml; YAML ignored without `-c`; default provider ollama | Keyless run gives a complete structural wiki; a key only adds prose | **UX/value** |
| Spend control | Budget enforced for Bedrock only; budget stop exits 0 | Single `--prose/--no-prose` knob; estimate; $2/$25 gate; no-TTY auto-declines | **Correctness** |
| Config | 3 templates that disagree, 2 tier-naming schemes, ~65 keys, silent unknown keys | `.repowise/config.yaml` + `.env` + `state.json`; one resolver for CLI/server/MCP | **Simplify** |
| State layout | `data/discovery-output/output-<slug>/` vs `data/output-<slug>/`; readers disagree | Everything under `<repo>/.repowise/` | **Correctness** |
| Orchestrator | 2,071-line `pipeline.py`; numbered phases 2, 5–19 | Phase modules + resume ledger | **Maintainability** |
| Incremental | Same-SHA fast path; only Tier-1 reused across scans | git diff + working tree; renames, tombstones, cascade budget, freshness states, prompt-hash reuse | **Cost** |
| Parsers | 5 tree-sitter grammars + WebForms/JSP custom + regex import-map tier | 25 query files + SFC + sqlglot; tree-sitter floor CI | **Breadth** (repowise ahead), **legacy web** (ai-discovery ahead) |
| Call resolution | 6 stages; fan-out ≤8; unresolved → 0.5 edge | 37 origins, one confidence each; ambiguity → no edge; unresolved → no edge | **Precision** |
| Business layer | Menus → screens → backend → DB; entity FSM; DMN/EARS/BPMN; FK-aware tables | None | **ai-discovery strength — keep** |
| Wiki | Jinja viewer (stdlib HTTP), CDN assets, read-only, broken paths | Next.js docs explorer, backlinks, confidence badges, version history, human notes, graph explorer, chat, present mode | **UX — main ask** |
| Search | sqlite-vec only | FTS5 + vector with RRF; graph expansion | **Quality** |
| MCP | None | 10 default + 7 opt-in tools; middleware for shield/trust/budget; `_meta` freshness | **Agent integration** |
| Git layer | None | Churn, ownership, co-change, significant commits, decisions | **Signal** |
| Runtime evidence | None | Explicitly not built | **Differentiator for ai-discovery** |
| Tests | 95 files / 1,073 tests; no CLI tests; saturated corpus baseline | ~1,300 unit files; golden, parity and idempotence tests; MCP contract tests | **Process** |
| Distribution | pip; Docker | PyPI wheel incl. web tarball; MCP registry; Claude Code plugin; VS Code | **Packaging** |

## 3. Adoption decisions

**Adopt** = take the pattern as is. **Adapt** = take the idea, change the design. **Skip** = don't build.

### UX / CLI

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| U1 | Single `init` with a 3-option first-run menu (keyless / prose / advanced); any flag or `--yes` skips it; stdin EOF → defaults | Adopt | Removes the "which flags?" problem | M2.2 |
| U2 | `--prose/--no-prose` as the single spend knob | Adopt | Replaces `--prod`, tiers and `--budget` confusion | M2.3, M1.10 |
| U3 | Cost estimate + gate (confirm >$2; default No >$25; no-TTY → error naming `--yes`/`--no-prose`) | Adopt | Prevents surprise spend | M2.4, M1.11 |
| U4 | End-of-run panel: metrics + degradations + ≤2 "What's next" commands | Adopt | Discoverability | M2.3 |
| U5 | Reason slugs on errors; documented exit codes | Adapt (one global exit-code table, which repowise lacks) | CI and agent friendliness | M2.1 |
| U6 | `--progress json` NDJSON events | Adopt | Agents and CI can drive the CLI | M2.1 |
| U7 | `status`, `doctor --repair` | Adopt | Self-diagnosis | M2.5 |
| U8 | Lazy command registration | Adopt | `--help` stays fast without importing tree-sitter or boto3 | M2.1 |
| U9 | 44 top-level commands | Skip | ai-discovery targets about 11 verbs | M2.* |
| U10 | Uninstall inventories everything init wrote | Adopt (for MCP/plugin wiring) | Install and uninstall stay symmetric | M5.3 |
| U11 | Owl mascot / branding | Skip | — | — |

### State, config, pipeline

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| S1 | All state in `<repo>/.repowise/` | Adapt → `<repo>/.discovery/`, plus `--state-dir` for read-only sources | Kills the path bugs | M1.2 (ADR-0001) |
| S2 | One config resolver shared by CLI/server/MCP; broken config surfaced, never silently defaulted | Adopt | 64 config-drift fixes in repowise's changelog | M1.3 |
| S3 | Stamp every artifact with (extractor version, config hash, commit) | Adopt | Incremental correctness | M1.8, M3.1 |
| S4 | Partial-graph patching on update | Skip → per-file parse cache + full re-resolution + deterministic page rebuild | Simpler, and parity tests become meaningful | M1.9 (ADR-0002) |
| S5 | Single-flight update lock with dead-PID eviction | Adopt | Hook and serve can run concurrently | M1.4 |
| S6 | Numbered migrations; FTS rebuild path | Adopt (plain numbered SQL/py files, no Alembic) | `db.py` if-block migrations don't scale | M1.4, M1.5 |
| S7 | Embedder pinned to the store | Adopt | Prevents mixed-dimension vectors | M1.5 |

### LLM

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| L1 | Provider capability matrix (temperature rules, timeouts per class) | Adopt | 62 provider fixes upstream | M1.10 |
| L2 | Cost accounting sums cache-read/write tokens | Adopt | Under-reporting otherwise | M1.11 |
| L3 | Retry policy: 429 honours Retry-After; 4xx fail fast | Adopt | Current retry is substring-based | M1.11 |
| L4 | Truncated output is invalid and not retryable | Adopt | `truncated` flag is dropped today | M0.7 |
| L5 | Stub page at confidence 0.3 on provider failure, never overwriting prose | Adopt | Failed summaries currently persist and get reused | M0.7, M3.7 |
| L6 | Deterministic grouping; LLM only *names* groups | Adopt | LLM allocation measured worse coverage | M3.4, M3.7 |
| L7 | Prompt-cache prefix (`cache_control`) | Adopt | Cost | M1.11 |
| L8 | Content-hash reuse of model pages | Adopt (also for Tier-2/3) | Tier-2/3 are re-paid every scan today | M3.7 |

### Graph and accuracy

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| G1 | Closed origin vocabulary, one confidence per origin | Adopt | Lets precision be audited per strategy | M1.12 (ADR-0004) |
| G2 | Ambiguity → no edge; unresolved → no edge (counted instead) | Adopt | Fan-out pollutes flows and blast radius | M1.12 |
| G3 | Stdlib/builtin and language guards on bare-name tiers | Adopt | Cross-language false edges | M1.12 |
| G4 | `dispatches_to` interface→impl, refused above 24 impls | Adopt | Interface dispatch is the largest recall gap | M6.7 |
| G5 | Provenance kinds separate (static / framework / dynamic_hint / co_change / runtime) | Adopt | Co-change was once served as a dependency | M1.12, M8.* |
| G6 | `.gitignore`, size caps, minified/binary/generated detection, symlink guard, submodules | Adopt | Walker bugs today | M6.1 |
| G7 | Same-namespace/package implicit scope; C# partials; extension methods | Adopt | C#/Java resolution gaps | M6.2, M6.3 |
| G8 | Vue SFC projection at byte-identical offsets | Adopt | No `.vue` parsing today | M6.5 |
| G9 | Barrel/re-export fixed-point resolution | Adopt | TS gap | M6.5 |
| G10 | Dynamic-hint edges (reflection, `Class.forName`) used for reachability only, never for flows | Adopt | Honest about dynamic code | M6.7 |
| G11 | COBOL rule: dynamic CALL → no edge; external entry points suppress dead-code claims | Adopt for ABAP | ABAP has the same shape | M7.3 |
| G12 | Weighted entry-point scoring | Adapt | Substring name hints over-trigger | M6.12 |
| G13 | Leiden + PageRank + betweenness | Skip for now (Louvain stays) | Not on the critical path | — |
| G14 | sqlglot for SQL | Adopt + extend to stored procedures (repowise doesn't) | Legacy apps are SP-heavy | M6.8, M6.10 |

### Wiki and web

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| W1 | Keyless structural wiki as a complete product | Adopt | Core of "simpler" | M3.* |
| W2 | Page hierarchy (symbol/file → module → overview → onboarding) | Adapt: add screen, endpoint, entity, table, flow and stored-proc pages | Business layer | M3.2–M3.4 |
| W3 | Docs explorer: tree, reader, backlinks, confidence badge, freshness, human notes, versions | Adopt | The UX the owner likes | M4.3, M4.6 |
| W4 | Graph explorer with LOD caps and truncation banner | Adopt | Big graphs | M4.4 |
| W5 | ⌘K command palette; FTS + vector RRF search | Adopt | Search quality | M4.3, M2.7 |
| W6 | Next.js standalone + Node ≥20 at runtime + tarball download | Skip → Vite SPA built into the wheel, served by FastAPI | No Node at runtime; offline/air-gapped | M4.2, M4.5 |
| W7 | Present mode (slides/walkthrough) | Exploring | Nice-to-have | — |
| W8 | 15 output languages | Exploring (Korean likely useful) | — | — |

### MCP and agent

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| A1 | Small default tool surface; specialists opt-in | Adopt (~8 tools) | Tool sprawl was consolidated upstream | M5.3 |
| A2 | Shield: success-shaped errors with a remedy | Adopt | An `isError` teaches agents to abandon the server | M5.3 |
| A3 | Rank-then-cap with true totals; token budgets | Adopt | Payload-size fixes upstream | M5.1 |
| A4 | `_meta` freshness (index commit, behind-by, degraded) | Adopt | Honesty | M5.1 |
| A5 | Three-state answers; `may_break` naming | Adopt | No confident zeros | M5.2 |
| A6 | Claude Code plugin with skills + commands | Adapt (impact + change-plan skills centred on the research-doc BA workflow) | User decision: MCP + skills | M5.4–M5.6 |
| A7 | Hooks (SessionStart/PostToolUse augment) | Exploring; measure against a base rate first | repowise retired hooks that didn't move behaviour | M5.8 |
| A8 | Generated CLAUDE.md block | Exploring | Context amplification cost | — |

### Evidence and benchmarks

| # | Pattern | Decision | Rationale | Item |
|---|---|---|---|---|
| E1 | Git layer: churn, co-change, ownership, mailmap, noise filters | Adopt (later) | Impact ranking signal | M8.1 |
| E2 | Coverage ingestion (LCOV/Cobertura/coverage.py) | Adopt + per-test contexts | Cheapest runtime evidence | M8.2 |
| E3 | No runtime/APM data | Diverge: build OTLP span ingestion as a separate provenance | Brownfield DI/event gaps; owner's research | M8.3 |
| E4 | Precision+recall pairs; compiler oracle; seeded stratified hand audit; unknown bucket; no pooled recall; sealed split; pre-registration | Adopt | Saturated baseline today | M0.11, M6.13 |
| E5 | Health score / dead code / decisions / security layers | Skip (out of product scope) | Focus | — |
| E6 | LLM PR reviewer | Skip | Same reasoning as repowise | — |

## 4. Where ai-discovery is ahead (keep and invest)

- **Screen-centric business layer:** menus, routes, screens → APIs → services → tables (`src/ai_discovery/menu_detector.py`, `route_parser.py`, `screen_mapper.py`).
- **Legacy server-rendered UIs:** WebForms (`parsers/webforms.py`, `webforms_extractor.py`) and JSP (`parsers/jsp.py`, `jsp_extractor.py`). repowise skips `.aspx`/`.jsp` entirely.
- **Entity state machines, DMN/EARS/BPMN, FK-aware table docs** (`graph/fsm_*.py`, `generators/`).
- **Stage-0 precise symbol index import** (`graph/symbol_index.py`). repowise is heuristic-only. Nothing produces `symbol_index.json` yet; a SCIP producer is a candidate (Unverified per stack).
- **SAP target (M7):** neither tool supports it.

## 5. Benchmarking plan against repowise

- See `../design/benchmark-protocol.md` (new).
- repowise is run **as a black box**: `pip install repowise`, `repowise init --no-prose -y`, read outputs and MCP answers only. Run it on the same pinned repos as ai-discovery in M0.12.
- Comparable rows:
  - files indexed and skipped (by reason);
  - symbols;
  - call edges by origin;
  - precision sample;
  - recall vs ground truth;
  - time;
  - cost (prose mode).
- Not comparable (ai-discovery only): screens, FSMs, SDLC artifacts.
