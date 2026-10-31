# MCP server and Claude Code integration

- **Status:** Accepted (default tool list frozen at M5.3; schemas may grow additively; new tools are opt-in only)
- **Last updated:** 2026-10-31
- **Related items:** M5.1–M5.15, M2.8, M1.12, M1.13, M6.13, M3.5, M8.1, M8.2, M8.5
- **Scope decision:** ai-discovery is the **knowledge layer for autonomous agents**: it serves graph, impact, page, rationale and verification data over MCP and ships skills. **External agents orchestrate** (Claude Code, Codex, Agent SDK, Cursor, CI bots). There is no built-in BA agent and ai-discovery never executes user code. Gap analysis: `research/agent-platform-assessment.md`.

## 1. Why (from `docs/new-requirement-research.md`, verified in `research/research-doc-review.md`)
- Agentic search works best when the agent has enough starting context. Without precise structure it pattern-matches on text. Source: the Anthropic blog "How Claude Code works in large codebases", 2026-05-14.
- Brownfield blast radius is missed through DI strings, reflection, event wiring, ORM/SQL and markup-to-code-behind binding. A static graph with **explicit provenance and explicit unknowns** gives the agent a pinned starting set. Compilers and tests remain the final check.
- The user's own benchmark observed early-stop on Sonnet (Unverified externally). Mitigation: skills that impose a **multi-tier trace protocol** with explicit stop rules, fed by a structured impact list rather than text dumps.

## 2. Server
- **Package:** `src/ai_discovery/mcp/`, using the official `mcp` Python SDK **2.x** (`mcp>=2.3,<3`; low-level `Server` with `on_list_tools`/`on_call_tool`; 2.x renamed `FastMCP` to `MCPServer`, which we do not use). Only `mcp/server.py` imports the SDK; `ToolSpec`, the registry and the middleware are SDK-free.
- **Transport:** stdio, via `discover mcp serve`.
- **Data access:** every tool calls `query/` (M5.1) and reads `current_scan`, read-only.
- **Middleware**, in order:
  1. **shield.** No exception escapes as an MCP error. Domain failures return a success-shaped `Answer` with a `remedy`, e.g. `not_indexed` → "run `discover scan`".
  2. **budget.** Each response gets about 8k tokens by default (config `mcp.max_tokens`). Lists are **ranked, then cut**. The response carries `omitted: [{field, dropped, total}]` and `truncated: true`.
  3. **meta.** Attaches the `_meta` envelope.
  4. **timing.** Adds `_meta.timing_ms`.
- **Tool selection:** default set below. Users can pass `--tools +get_table,-get_flow`.

### `_meta` envelope
```json
{"contract_version":"1","scan_id":12,"indexed_commit":"abc123","head_commit":"def456","behind_by":3,
 "dirty":false,"stale_warning":"2 of the files in this answer changed since indexing","degraded":["encoding_fallback"],
 "evidence_kinds_used":["static","framework"],"timing_ms":41}
```
`stale_warning` is set only when files *in this answer* changed; otherwise only `behind_by` is reported.
M5.1 adds `content_trust: {untrusted: [field paths], note}` (fields holding repo text or prose; M5.12 extends it), `truncated` and `omitted_count`. The envelope's `omitted` is a list of `{field, dropped, total}`. Statuses: `ok | not_found` (with `did_you_mean`) `| not_indexed` (with `remedy`) `| unknown` (ambiguous target, with `candidates`).

## 3. Tools (default 8)

| Tool | Input (JSON schema sketch) | Output `data` |
|---|---|---|
| `get_overview` | `{}` | `{summary, stacks:[…], counts:{files,symbols,screens,entities,tables,endpoints}, entry_points:[…top 20], domains:[{name,page_id}], unresolved_ratio_by_lang:{…}}` |
| `search` | `{query:str, kinds?:[page_type\|"symbol"], limit?:int≤50}` | `{hits:[{id,type,title,snippet,score,path?,line?}]}` |
| `get_context` | `{target:str}`: symbol QN or short name, `symbol:`, `file:`, `screen:`, `table:`, `entity:` or any page id | `{page_id, type, title, summary, symbols:[…], callers:[…≤20], callees:[…≤20]` (each with `origin, evidence_kind, confidence, followable`)`, callers_complete: yes\|unknown, screens:[…], tables:{reads:[],writes:[]}, links, backlinks, citations:[{path,line_start,line_end}], unresolved_calls_here:int, unresolved_calls:[…≤20]}` |
| `get_impact` | `{target:str, depth?:1..6=3, include?:[evidence_kind], max_items?:≤100=50}` | see §4 |
| `get_change_impact` | `{diff?:str, range?:"A..B", working_tree?:bool}` | `{changed_symbols:[…], impact:<get_impact data merged>, tests_to_run:[…] (if coverage overlay), screens_affected:[…]}` |
| `get_screen` | `{id_or_route:str}` | `{screen, menu_path, events:[{ui_event, handler, endpoint?}], chain:[{layer,node,citation}], tables, batch_jobs, interfaces}` |
| `get_entity` | `{name:str}` | `{states, transitions:[{from,to,guard,writer}], decisions(DMN), requirements(EARS), readers, writers}` |
| `get_flow` | `{id:str}` | `{steps:[{node,citation}], bpmn_available:bool, stop_reason}` |

Opt-in: `get_table`, `get_symbol_source` (exact line bounds, re-checked against the live file), `get_path` (A→B), plus the agent-platform tools below.

| Opt-in tool | Item | Input | Output `data` |
|---|---|---|---|
| `get_why` | M8.5 | `{target?:str, query?:str}` | `{records:[{text, source:adr\|commit\|pr_message\|code_comment, citation, date, stale:bool, signer_kind, status}]}`; none → `status:"unknown"` with sources searched |
| `get_verification_plan` | M5.15 | `{diff?, range?, working_tree?, plan_path?, depth?}` | `{source, changed_files, changed_symbols, build:[{stack,tool,cwd,cmd,basis}], tests:[…], targeted_tests:[{test,path,cwd,cmd,basis:graph\|convention}], impacted_tests:{status,tests,reason}, smoke_targets:[endpoint\|screen], unknowns, unmapped_files, plan:{pinned, unplanned_changes, test_changes, unexpected_impact}\|null, verdict:ok\|stop, stop_reasons}`; `basis` is `detected` until a config `verify:` section exists |
| `record_finding` / `set_finding_status` / `add_note` / `propose_decision` | M5.10 | target + text + evidence | `{id, signer_kind:"agent", status}` — never alters graph facts |

Profiles (M5.3): `default` (8 above), `lean` (≤ 5: `search`, `get_context`, `get_impact`, `get_change_impact`, `get_screen`), `all`. Measured tool-list cost (≈ 4 chars/token, M5.3): lean ≈ 1.1k, default ≈ 1.4k, all ≈ 1.8k tokens (`discover mcp print-config --tools X` prints it).

**Implementation notes (M5.3):** every `ToolSpec` declares a `Budget(max_tokens, shed)`; a contract test fails without one. Per-tool caps: overview/search 4k, context 6k, screen/entity/flow/table/path 5k, impact/change impact/symbol source 8k; `--max-tokens` (or `$DISCOVER_MCP_MAX_TOKENS`) overrides all. Shedding keeps the ranked head of each list (binary search for the longest prefix that fits), merges the cut into `omitted` with the true total, and clips strings > 4000 chars only as a last resort (`degraded: strings_clipped`). Arguments are validated against the tool's JSON schema; failures return `status: unknown, reason: bad_arguments`. Unknown or not-enabled tools return `reason: unknown_tool` with the available list. Tools carry `readOnlyHint: true`. `mcp.tools` / `mcp.max_tokens` config keys wait on a config-v2 section (lane A); until then use `--tools` / `$DISCOVER_MCP_TOOLS`.

**Field-naming rule:** no certainty words. Use `may_break` / `may_affect`, never `will_break` (repowise 0.47.0 lesson).

## 4. Impact algorithm (`query/impact.py`, M5.2)

**Seed.** The target node or nodes. For a change impact, these are symbols whose line spans intersect the diff hunks.

**Traversal.** Reverse BFS (who depends on me) plus forward data edges (what I write), with these rules:
- Follow edges whose `evidence_kind ∈ include` (default `static, framework, dynamic_hint`; `dispatches_to` edges carry `evidence_kind=static`) and whose origin confidence is ≥ 0.75. A `global_unique` (0.50) edge is followed only at depth 1 and flagged.
- A visited set gives cycle safety. Depth is capped (default 3, maximum 6).
- **Hub dampening.** A node whose in-degree exceeds the 99th percentile is reported but not expanded, and is added to `unknowns` as `{kind:"hub_not_expanded", node, in_degree}`.

Edge kinds and origins are defined in [ADR-0004](../adr/0004-edge-origin-vocabulary.md).

**Scoring.** `score(n) = Σ over shortest paths of w(edge_kind) × conf(origin) × 0.7^(depth-1)`, with these weights:

| edge kind | w |
|---|---|
| `writes_table` | 1.0 |
| `calls` | 0.8 |
| `framework_binds` / `markup_event` | 0.8 |
| `calls_proc` | 0.8 |
| `dispatches_to` | 0.6 |
| `dynamic_hint` | 0.5 |
| `reads_table` | 0.5 |
| `type_use` | 0.4 |

**Output:**
```json
{"target":"…","items":[{"node":"…","kind":"symbol|screen|table|endpoint|batch_job|test","score":0.82,
   "depth":2,"via":[{"src":"…","dst":"…","edge_kind":"calls","origin":"receiver_typed","evidence_kind":"static"}],
   "may_break":true,"citation":{"path":"…","line_start":10,"line_end":42}}],
 "co_change":[{"file":"…","support":7}],
 "unknowns":[{"kind":"unresolved_call|dynamic_hint|hub_not_expanded|reflection","where":"…","detail":"…"}],
 "entry_points_reached":["screen:order-edit","endpoint:POST /api/orders"]}
```
- `co_change` (M8.1) is **listed separately** and never merged into `items`.
- If the target is not found, the result is `status:"not_found"` with `did_you_mean`. It is never an empty `ok`.

**As implemented (M5.2, 2026-10-31):**
- **Seeds per target.** Symbol → its node(s); a class seed also seeds its members. `file:` → all symbols in the file. `endpoint:` → the handler. `screen:` → the backend components and API-route handlers in its screen spec ("what else shares this screen's backend"). `entity:` → the entity class plus its state-transition trigger functions. `table:` → `db_model` classes in the files the table page cites, plus screens reading the table and tables with a foreign key to it (depth 1, `reads_table` weight).
- **Change impact seeds** are the *innermost* symbols whose span intersects a hunk; a containing class is not added when a member already covers the hunk. Line ranges come from the old side of the diff when its base is the indexed commit (working tree; a range starting at the indexed commit); otherwise both sides are used and `_meta.degraded` gets `diff_base_differs_from_index` or `diff_base_unknown`. Files with no indexed symbols (new, non-code) become `unknowns[kind=unmapped_change]`.
- **Low-confidence edges** (origin confidence < 0.75: `global_unique`, `sql_regex`) are followed only from a seed, marked `certainty: "possible"`, and never expanded. The same holds for `dynamic_hint` edges.
- **Scoring:** `pw(seed) = 1`; `pw(n) = Σ over parents p on the previous layer of pw(p) · w(edge_kind) · conf(origin)`; `score = min(1, pw(n) · 0.7^(depth-1))`. A screen adds weight 0.8 × its mapping confidence; a table write or entity transition adds weight 1.0.
- **Hub dampening:** a non-seed node with in-degree ≥ max(99th percentile, 25) is reported but not expanded.
- **`unknowns[].kind`** ∈ `hub_not_expanded`, `possible_not_expanded`, `low_confidence_edge_not_followed`, `unresolved_call`, `dynamic_hint`, `unmapped_change`.
- **Extra output fields:** `certainty` (`likely|possible`) and `page_id` per item; `tests_reached`; `overlays_missing` (evidence kinds with no data yet); `get_change_impact` → `{source, changed_files, changed_symbols, impact, screens_affected, tests_to_run:{status:"unknown", tests, reason}}` until the M8.2 coverage overlay exists.

## 5. Claude Code plugin (`plugin/`, new; M5.4)
```
plugin/
  .claude-plugin/plugin.json      # name ai-discovery (verified against Claude Code plugin docs 2026-10-31)
  .claude-plugin/marketplace.json # single-plugin marketplace, source "./"
  .mcp.json                        # discover mcp serve (runs in the session working directory)
  skills/
    discover-impact/SKILL.md
    discover-change-plan/SKILL.md
    discover-triage/SKILL.md       # already promised by CLAUDE.md, missing today
    discover-consistency/SKILL.md  # already promised by CLAUDE.md, missing today
  commands/                        # optional thin wrappers: /discover-status, /discover-impact
```
The SessionStart hook is **not shipped by default**. M5.8 (Exploring) adds a freshness-line hook only if measurement against a base rate shows it changes agent behaviour (repowise retired nudge hooks that didn't).

### Skill: `discover-impact` (M5.5): recursive trace protocol
1. **Freshness.** Call `get_overview`. If `_meta.behind_by > 0` or `dirty`, tell the user and suggest `discover scan`. Continue only if they confirm.
2. **Tier 0 seed.** Resolve the target with `search`, then `get_context`. If there is more than one candidate, ask the user rather than guess.
3. **Tier 1.** Call `get_impact(depth=1)`. For **every** item with `may_break`, open the cited file range and confirm the dependency with grep or LSP. Record each as confirmed, refuted or unclear.
4. **Tier 2..N.** Repeat with `depth=2`, then 3, for confirmed items only. **Do not stop** until each branch reaches an entry point (screen, endpoint, batch job, TCODE), a table write, or the depth cap.
5. **Unknowns.** For each `unknowns[]` entry, run a targeted grep: the string literal for DI/reflection, the event name, the table name in SQL/XML. Report any that remain unresolved.
6. **Output.** A table of confirmed impact (with file:line), the unknowns, and the tests/compile commands to run. Every claim cites a tool result or a file it read.

### Skill: `discover-change-plan` (M5.6)
**Input:** a one-line requirement.

1. Run `discover-impact` on the targets the requirement implies.
2. Write `IMPACT_REPORT.md`.
3. Write `IMPLEMENTATION_PLAN.md`.
4. Stop for human approval. **No source edits.**

`IMPACT_REPORT.md` template:
```markdown
# Impact report — <requirement>
Index: <commit> (behind by N) · generated <date>
## Targets
## Confirmed impact (ranked)
| # | Node | Kind | Why (edge path) | Evidence | File:line | Confidence |
## Entry points reached (screens / endpoints / jobs)
## Data impact (tables written / read)
## Unknowns & unverified paths
## Co-change neighbours (history only — not dependencies)
```
`IMPLEMENTATION_PLAN.md` template:
```markdown
# Implementation plan — <requirement>
## Pinned target files (edit only these without re-planning)
## Style exemplars (files whose idiom to follow)
## Steps (pseudocode per file)
## Backward compatibility
## Verification (exact commands): build/compile · unit tests · targeted tests from coverage overlay
## Rollback
```
Per-stack verify command hints (from config or detected):

| Stack | Command |
|---|---|
| .NET | `dotnet build`, `dotnet test`, or `msbuild` for Framework |
| Java | `mvn -q compile` / `gradle compileJava`, then the tests |
| TS | `tsc --noEmit`, `npm test` |
| Python | `pytest` |
| ABAP | abaplint, if configured (Unverified availability) |

## 6. Evaluation (M5.7)
- **Task set:** at least 15 change requests across reference repos (`design/benchmark-protocol.md`). The gold files are the historical fix diff. Prefer historical replay: pick commit C, state the requirement from the commit message or issue, gold = the files changed.
- **Arms:**
  - (a) bare Claude Code;
  - (b) Claude Code + ai-discovery MCP + skills;
  - (c) optional: + repowise MCP, as a black box.

  Prompts are byte-identical, and every arm uses the same model and commit.
- **Metrics:**
  - files-touched recall and precision against gold;
  - compile and test pass rate;
  - **output tokens** (not dollars, since prompt-cache warmth biases cost);
  - number of tool calls.
- **Reporting:** pre-register the hypotheses and bands in a commit before running. Report every arm, including losses.
- **Gate:** the skills are marked GA only if arm (b) beats arm (a) on recall with no precision loss, *and* M6.13 accuracy gates pass.

## 7. Autonomous-agent contract (added 2026-10-31)

What an agent running without a human in the loop can rely on. Items in brackets.

- **Headless use.** Every CLI command supports `--json` and documented exit codes (M2.1); `scan --progress json` streams NDJSON; with no TTY the cost gate errors naming `--yes`/`--no-prose` instead of prompting (M2.4). The same answers are importable from `ai_discovery.api` (M5.1).
- **Freshness.** Opt-in post-commit hook and `scan --watch` keep the index at HEAD (M2.8); every answer carries `_meta.indexed_commit/behind_by/dirty/stale_warning` (M5.1). Hooks and watch never run prose (no surprise spend).
- **Orientation.** A capped, deterministic instructions block in AGENTS.md/CLAUDE.md (M5.11: `discover mcp install --instructions agents|claude|both`; markers `<!-- ai-discovery:start … -->` / `<!-- ai-discovery:end -->`; ≤ 600 tokens, measured ≈ 370 on the fixture and on eShop; contents: indexed commit + freshness rule, stacks, size, top 5 entry points / screens / domains, the search → context → impact → change-impact workflow, the untrusted-content rule); install for Claude Code, Codex, Cursor and VS Code with a duplicate guard (M5.14).
- **Safety envelope (M5.12).** Repo-derived text appears only in fields listed in `_meta.content_trust.untrusted`; tool descriptions say those fields are data, not instructions; possible injections are flagged in `_meta.degraded`; secrets are masked (M1.13 masker). Remote MCP requires a token off loopback (M5.9).
- **Write-back model (M5.10).** Agents may record findings, notes and *proposed* decisions, each stamped `signer_kind`, client, session and scan id. Write-back never changes graph facts; proposed decisions become accepted only by a human.
- **Verification (M5.15).** After editing, agents call `get_verification_plan(working_tree, plan_path)` and run the returned commands themselves; unplanned impact is a stop condition in the `discover-verify-change` skill. `discover-change-plan` keeps its human approval gate; an operator may pre-approve for unattended runs.
- **Observability (M5.13).** A local-only call ledger; `discover status --agent` summarises it; M5.7 uses it for tool-call metrics.
- **Proof (M5.7).** Effects are claimed only from the pre-registered eval, including a headless arm; hooks ship only after a randomised A/B (M5.8).
