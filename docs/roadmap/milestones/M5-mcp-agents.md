# M5 — MCP server + Claude Code integration

**Goal:** give Claude Code precise, graph-backed context and blast radius through a small MCP surface and skills — the research doc's "Custom MCP" option, with Claude Code as orchestrator.
**Exit criteria:** MCP contract tests green; agent-loop eval shows measured effect vs bare Claude Code (published either way).
**Refs:** `docs/roadmap/design/mcp-and-agents.md`, `docs/roadmap/research/research-doc-review.md`

### M5.1 `query/` service (read)
- **Status:** Planned
- **Size:** M
- **Lane:** E
- **Depends on:** M3.5
- **Owns:** `src/ai_discovery/query/` (new); absorbs `src/ai_discovery/rag/retriever.py`, `src/ai_discovery/rag/chat.py`
- **Acceptance:**
  - Functions: overview, search (FTS5 + vector, RRF), context(target), screen, entity, flow, table — shared by API, MCP and CLI.
  - Rank-then-cap with true totals; three-state answers (yes/no/unknown); unresolved target → typed not-found error, never an empty "0".
  - `_meta`: indexed commit, behind-by, dirty, degraded flags, truncated + omitted counts.
- **Verify:** `pytest -q tests -k query`
- **Out of scope:** impact (M5.2).

### M5.2 Impact + change impact
- **Status:** Planned
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
- **Status:** Planned
- **Size:** M
- **Lane:** E
- **Depends on:** M5.1
- **Owns:** `src/ai_discovery/mcp/` (new), `src/ai_discovery/cli/commands/mcp.py` (new), `tests/mcp/` (new), `pyproject.toml` (append `mcp`)
- **Acceptance:**
  - stdio transport; ~8 default tools per design doc; tools auto-discovered.
  - Success-shaped errors with remedy (not indexed → "run discover scan"); per-tool token budget with ranked shedding and recorded omissions; golden response tests.
  - `discover mcp serve|install|uninstall|print-config` per `design/cli-v1.md`; install/uninstall write/remove the `.mcp.json` entry symmetrically.
- **Verify:** `pytest -q tests/mcp`
- **Out of scope:** HTTP transport (Exploring).

### M5.4 Plugin + triage/consistency skills
- **Status:** Planned
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
- **Status:** Planned
- **Size:** M
- **Lane:** E
- **Depends on:** M5.2, M5.4
- **Owns:** `plugin/skills/discover-impact/` (new)
- **Acceptance:**
  - Protocol: call impact tool → for each tier, read pinned files and confirm with grep/LSP → continue until entry points/screens reached or depth cap; explicit stop rules (counters early-stop bias); report every claim with a tool or file:line citation; unknowns listed, never dropped.
- **Verify:** dry-run transcript on fixture recorded in PROGRESS.md
- **Out of scope:** editing code.

### M5.6 `discover-change-plan` skill
- **Status:** Planned
- **Size:** M
- **Lane:** E
- **Depends on:** M5.5
- **Owns:** `plugin/skills/discover-change-plan/` (new)
- **Acceptance:**
  - Requirement → `IMPACT_REPORT.md` + `IMPLEMENTATION_PLAN.md` (templates in design doc) with pinned target files, idiom exemplars, backward-compat notes, and per-stack compile/test commands (`dotnet build`, `mvn -q compile`, `tsc --noEmit`, `pytest`) to run after edits.
  - Stops for human approval before any edit.
- **Verify:** dry-run on fixture; outputs committed as examples under `plugin/skills/discover-change-plan/examples/`
- **Out of scope:** autonomous implementation loop.

### M5.7 Agent-loop eval
- **Status:** Planned
- **Size:** M
- **Lane:** E
- **Depends on:** M5.6, M6.13
- **Owns:** `bench/agent/` (new)
- **Acceptance:**
  - ≥ 15 change tasks with gold diffs (historical commits) across ≥ 3 stacks; arms: bare Claude Code vs + ai-discovery MCP/skills (and repowise as black box if available).
  - Metrics: files-touched recall/precision vs gold, build/test pass, output tokens; pre-registered before running; results published including losses.
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
- **Verify:** measurement note in PROGRESS.md
- **Out of scope:** —
