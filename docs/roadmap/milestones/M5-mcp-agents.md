# M5 — MCP + agent platform (knowledge layer)

**Goal:** make ai-discovery the knowledge layer for autonomous coding agents (Claude Code, Codex, Agent SDK, Cursor, CI bots): precise graph-backed context, blast radius, rationale, safety and verification contracts through a small MCP surface and skills. External agents orchestrate; ai-discovery never runs an agent loop or executes user code (owner decisions 2026-10-08 and 2026-10-31).
**Exit criteria:** readiness level L3 per `research/agent-platform-assessment.md` §2 — MCP contract + budget-contract tests green; write-back, safety envelope and verification contract shipped; agent-loop eval (M5.7) shows measured effect vs bare agents (published either way).
**Refs:** `docs/roadmap/design/mcp-and-agents.md`, `docs/roadmap/research/agent-platform-assessment.md`, `docs/roadmap/research/research-doc-review.md`

**Slice 3a (to L2):** M5.1 → M5.2 → M5.3 → M5.14 + M5.11 → M5.4/M5.5 (with M2.8 in parallel).
**Slice 3b (to L3):** M1.13 → M5.12; M5.6 → M5.15; M5.10; M8.5; M5.9; M5.13. **L4:** M5.7.a → M5.7.b.

### M5.1 `query/` service (read)
- **Status:** Done (80ba6f2)
- **Size:** M
- **Lane:** E
- **Depends on:** M3.5
- **Owns:** `src/ai_discovery/query/` (new), `src/ai_discovery/api.py` (new); absorbs `src/ai_discovery/rag/retriever.py`, `src/ai_discovery/rag/chat.py`
- **Acceptance:**
  - Functions: overview, search (FTS5 + vector, RRF), context(target), screen, entity, flow, table — shared by API, MCP and CLI.
  - Rank-then-cap with true totals; three-state answers (yes/no/unknown); unresolved target → typed not-found error, never an empty "0".
  - `_meta`: indexed commit, behind-by, dirty, degraded flags, truncated + omitted counts, `content_trust` (which response fields carry repo-derived text; filled by M5.12).
  - Stable public facade `ai_discovery.api` re-exporting the query functions and result types (documented, semver-tracked) so bots/CI use the same answers as MCP without importing the server.
- **Verify:** `pytest -q tests -k query`
- **Out of scope:** impact (M5.2).

### M5.2 Impact + change impact
- **Status:** Done (8a2ef96)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.1, M1.12
- **Owns:** `src/ai_discovery/query/impact.py` (new); absorbs `src/ai_discovery/graph/impact.py`
- **Acceptance:**
  - Target = symbol | file | table | screen | entity | git diff/range/working tree.
  - Reverse traversal with depth cap (default 3), visited set, hub dampening; ranking data-write > call > dispatch/framework > type-use; co-change and runtime evidence listed separately.
  - Output: affected symbols/screens/endpoints/tables/tests with `may_break` wording, evidence + confidence per item, and `unknowns` (unresolved calls, dynamic hints on the path).
- **Verify:** `pytest -q tests -k impact`
- **Out of scope:** coverage-based test selection (M8.2).

### M5.3 MCP server
- **Status:** Done (c2164d7)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.1
- **Owns:** `src/ai_discovery/mcp/` (new), `src/ai_discovery/cli/commands/mcp.py` (new), `tests/mcp/` (new), `pyproject.toml` (append `mcp`)
- **Acceptance:**
  - stdio transport; ~8 default tools per design doc; tools auto-discovered.
  - Tool profiles: `default`, `lean` (≤ 5 tools, schema tokens measured and recorded), `all`; `--tools`/`mcp.tools` accept allowlist and `+x,-y` edits.
  - CI contract test: every registered tool declares a response budget and a shed order; a test fails if a tool lacks one.
  - Success-shaped errors with remedy (not indexed → "run discover scan"); per-tool token budget with ranked shedding and recorded omissions; golden response tests.
  - `discover mcp serve|install|uninstall|print-config` per `design/cli-v1.md`; install/uninstall write/remove the `.mcp.json` entry symmetrically.
- **Verify:** `pytest -q tests/mcp`
- **Out of scope:** HTTP transport (M5.9).

### M5.4 Plugin + triage/consistency skills
- **Status:** Done (35a3880)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.3
- **Owns:** `plugin/` (new), `CLAUDE.md` (skills section)
- **Acceptance:**
  - Claude Code plugin layout verified against current official docs at implementation time (Unverified now); bundles MCP config + skills.
  - `discover-triage` and `discover-consistency` skills exist (CLAUDE.md already documents them); every skill CLAUDE.md names resolves to a file.
- **Verify:** `pytest -q tests -k plugin` (manifest + file existence)
- **Out of scope:** —

### M5.5 `discover-impact` skill
- **Status:** Done (752141d)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.2, M5.4
- **Owns:** `plugin/skills/discover-impact/` (new)
- **Acceptance:**
  - Protocol: call impact tool → for each tier, read pinned files and confirm with grep/LSP → continue until entry points/screens reached or depth cap; explicit stop rules (counters early-stop bias); report every claim with a tool or file:line citation; unknowns listed, never dropped.
- **Verify:** dry-run transcript on fixture recorded in PROGRESS.md
- **Out of scope:** editing code.

### M5.6 `discover-change-plan` skill
- **Status:** Done (db49608)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.5
- **Owns:** `plugin/skills/discover-change-plan/` (new)
- **Acceptance:**
  - Requirement → `IMPACT_REPORT.md` + `IMPLEMENTATION_PLAN.md` (templates in design doc) with pinned target files, idiom exemplars, backward-compat notes, and per-stack compile/test commands (`dotnet build`, `mvn -q compile`, `tsc --noEmit`, `pytest`) to run after edits.
  - Stops for human approval before any edit (gate kept even for autonomous runs; an autonomous agent proceeds only when its operator pre-approves, then verifies with M5.15).
- **Verify:** dry-run on fixture; outputs committed as examples under `plugin/skills/discover-change-plan/examples/`
- **Out of scope:** running an implementation loop inside ai-discovery (external agents do; see M5.15).

### M5.7.a Agent-loop eval — harness + pre-registration
- **Status:** In progress (rm/M5.7a-agent-eval-harness, 2026-10-31)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.6, M6.13
- **Owns:** `bench/agent/` (new), `docs/roadmap/benchmarks/2026-10-31-agent-eval-prereg.md` (new)
- **Acceptance:**
  - ≥ 15 change tasks with gold diffs (historical commits) across ≥ 3 stacks; workspaces built from the parent commit with no git history (the answer cannot be read from the repo).
  - Arms: bare Claude Code vs + ai-discovery MCP/skills; a headless autonomous arm (`claude -p`, no human turns, operator pre-approval, verification on); Codex and repowise (black box) as exploratory arms.
  - Metrics: files-touched recall/precision vs gold, build/test pass (run by the harness), output tokens, tool calls (transcript + M5.13 ledger), share of runs that ran tests before finishing; randomised run order; paired bootstrap CIs.
  - Pre-registration with hypotheses, bands, model, repetitions, seed and a spend ceiling, committed as **Draft**; `run` refuses to spend until it is marked **Frozen** and `--confirm-spend` is passed.
- **Verify:** `python -m bench.agent report` (plan + empty results); `pytest -q tests -k bench_agent`
- **Out of scope:** spending on model runs (M5.7.b).

### M5.7.b Agent-loop eval — run and publish
- **Status:** Blocked (owner approval of the pre-registration and spend ceiling)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.7.a
- **Owns:** `bench/agent/results/`, `docs/roadmap/benchmarks/` (results page)
- **Acceptance:**
  - Pre-registration frozen in a commit before the first run; all arms × tasks × repetitions run in the frozen order; results published per the template including losses and deviations.
  - Hooks (M5.8) are evaluated only by randomised A/B within this harness, never by transcript replay.
- **Verify:** `python -m bench.agent report`
- **Out of scope:** —

### M5.8 SessionStart freshness hook
- **Status:** Exploring
- **Size:** S
- **Lane:** E
- **Depends on:** M5.3
- **Owns:** `plugin/hooks/` (new)
- **Acceptance:**
  - Ships only if measured to change agent behaviour vs base rate; must be fast (no heavy imports), fail silent.
  - Measurement: randomised A/B (hook on/off per task, same seed set) in the M5.7 harness; metric = follow-up tool use and files-touched recall; pre-registered threshold.
- **Verify:** measurement note in PROGRESS.md
- **Out of scope:** —

### M5.9 MCP streamable-HTTP transport
- **Status:** Done (2ccb403)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.3
- **Owns:** `src/ai_discovery/mcp/transport_http.py` (new), `tests/mcp/`
- **Acceptance:**
  - `discover mcp serve --http [--host 127.0.0.1] [--port]` using the SDK's streamable-HTTP transport; same tools, middleware and budgets as stdio.
  - Loopback by default with a Host-header allowlist (DNS-rebinding guard). A non-loopback bind **refuses to start** without `DISCOVER_MCP_TOKEN` (bearer, constant-time compare) — stricter than warning-only designs.
  - Parent-process watchdog for stdio-launched servers; graceful shutdown releases the read handle.
- **Verify:** `pytest -q tests/mcp -k http`
- **Out of scope:** multi-project serving, TLS termination (use a reverse proxy), SSE legacy transport.

### M5.10 Agent write-back (findings, notes, proposed decisions)
- **Status:** Done (73b21f4)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.3, M4.6
- **Owns:** `src/ai_discovery/mcp/tools/writeback.py` (new), `src/ai_discovery/query/annotations.py` (new), one migration file under `store/migrations/` (number reserved in PROGRESS.md)
- **Acceptance:**
  - Opt-in tools `record_finding(target, kind, text, evidence[])`, `set_finding_status(id, open|acknowledged|resolved|false_positive)`, `add_note(target, text)`, `propose_decision(target, text, evidence[])`; every row stores `signer_kind` (agent|human), client name, session id and the scan id it was made against.
  - Write-back never alters graph facts, edges or confidence; annotations live in their own tables and survive rescans (re-anchored by qualified name; orphaned → flagged, not deleted).
  - `get_context` and wiki pages show annotations with signer kind; agent-proposed decisions stay `proposed` until a human confirms in the wiki (M4.6 notes UI) or via `discover` CLI.
  - Rate cap per session (config) and size cap per text; content passes through the M5.12 envelope when served back.
- **Verify:** `pytest -q tests -k writeback`
- **Out of scope:** transcript mining; auto-accepting agent decisions.

### M5.11 Generated agent instructions block (AGENTS.md / CLAUDE.md)
- **Status:** Done (8960440)
- **Size:** S
- **Lane:** E
- **Depends on:** M5.1
- **Owns:** `src/ai_discovery/mcp/instructions.py` (new), `src/ai_discovery/mcp/templates/` (new)
- **Acceptance:**
  - Deterministic (no LLM) block between `<!-- ai-discovery:start -->`/`<!-- ai-discovery:end -->` markers: stacks, top entry points/screens, domains, how to use the MCP tools (impact before edit, verify after), freshness line; hard token cap (default 600, measured in test).
  - Written only on opt-in (`discover mcp install --instructions agents|claude|both`); idempotent; never touches text outside markers; uninstall removes the block.
- **Verify:** `pytest -q tests -k instructions`
- **Out of scope:** per-directory instruction files.

### M5.12 Content-safety envelope
- **Status:** Done (0e92aca)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.3, M1.13
- **Owns:** `src/ai_discovery/mcp/middleware/safety.py` (new), `tests/mcp/fixtures/injection/` (new)
- **Acceptance:**
  - Every repo-derived string (source, comments, doc excerpts, notes, page prose) is returned only in fields listed in `_meta.content_trust.untrusted`; tool descriptions state that those fields are data, not instructions.
  - Heuristic prompt-injection flags (instruction-like phrases addressed to an assistant, hidden Unicode/bidi controls, base64 blobs in comments) add `_meta.degraded: ["possible_injection"]` with locations; content is flagged, not silently dropped.
  - Secrets masked in served source using the M1.13 masker; mask count in `_meta`.
  - Golden tests: each fixture in `tests/mcp/fixtures/injection/` is served labelled and flagged.
- **Verify:** `pytest -q tests/mcp -k safety`
- **Out of scope:** guaranteeing injection immunity (agents remain responsible); sanitising the user's own prompts.

### M5.13 Agent tool-use ledger
- **Status:** Done (031aaa4)
- **Size:** S
- **Lane:** E
- **Depends on:** M5.3
- **Owns:** `src/ai_discovery/mcp/middleware/ledger.py` (new); `cli/commands/status.py` (`--agent` section, coordinated with lane A)
- **Acceptance:**
  - Local-only append log (`<state>/logs/mcp_calls.jsonl`): tool, client, args hash, response bytes/tokens, omitted counts, status, timing; rotation by size; never transmitted anywhere.
  - `discover status --agent` summarises calls per tool, not-found/unknown rates, truncation rate; M5.7 reads the ledger for tool-call metrics.
- **Verify:** `pytest -q tests -k ledger`
- **Out of scope:** remote telemetry; savings counterfactual estimates (Exploring).

### M5.14 Multi-client install targets
- **Status:** Done (e84a25f)
- **Size:** S
- **Lane:** E
- **Depends on:** M5.3
- **Owns:** `src/ai_discovery/mcp/targets/` (new); `cli/commands/mcp.py` (per M5.3 exception)
- **Acceptance:**
  - `discover mcp install|uninstall --target claude|codex|cursor|vscode` writes/removes each client's MCP config (formats verified against each client's current docs at implementation time — Unverified now); `print-config --target X` for anything else.
  - Duplicate guard: detects an existing ai-discovery entry (or the plugin) and does not wire a second server.
  - Install/uninstall symmetric; `doctor` checks each installed target launches.
- **Verify:** `pytest -q tests -k mcp_targets`
- **Out of scope:** IDE extensions.

### M5.15 Verification contract (`get_verification_plan` + `discover-verify-change`)
- **Status:** Done (4f0503c)
- **Size:** M
- **Lane:** E
- **Depends on:** M5.2, M5.6
- **Owns:** `src/ai_discovery/query/verification.py` (new), `src/ai_discovery/mcp/tools/get_verification_plan.py` (new), `plugin/skills/discover-verify-change/` (new)
- **Acceptance:**
  - Opt-in tool `get_verification_plan(diff|range|working_tree)` → `{build:[cmd], tests:[cmd], impacted_tests:[…] (when the M8.2 overlay exists, else unknown), smoke_targets:[screen|endpoint], unplanned_impact:[…] (when an IMPLEMENTATION_PLAN.md path is given)}`; commands from config, else detected per stack (design doc table), each labelled `detected|configured`.
  - Skill `discover-verify-change`: after edits, call `get_change_impact(working_tree)`, diff against the plan's pinned files, flag unplanned impact, run the commands with the agent's own tools, and report pass/fail with citations; stop rules for unplanned impact.
  - ai-discovery itself never executes build/test commands.
- **Verify:** `pytest -q tests -k verification_plan`; dry-run transcript on a fixture recorded in PROGRESS.md
- **Out of scope:** sandboxing; CI integration.
