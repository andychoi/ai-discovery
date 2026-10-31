# Agent-Platform Assessment: ai-discovery vs repowise

- **Status:** Accepted (research input; drives the 2026-10-31 rework of M1, M2, M5 and M8)
- **Last updated:** 2026-10-31
- **Sources:**
  - ai-discovery at HEAD `dad72b8` (M1.12.b done; 1586 tests).
  - `../repowise` at HEAD `bcf4d96e` (v0.53.0). This is unchanged since `repowise-benchmark.md`.
  - Read-only exploration of repowise's docs, CHANGELOG, manifests and module docstrings. Paths below are relative to `../repowise/`.
- **Clean-room rule (ADR-0006):** this page describes repowise's *behaviour*. No repowise code is copied, translated or paraphrased into ai-discovery.
- **Relationship to [`repowise-benchmark.md`](repowise-benchmark.md):**
  - That page compared the two products piece by piece (CLI, state, LLM, graph, wiki, MCP) and its adoption decisions stand.
  - This page uses one lens: **can an autonomous coding agent run its whole loop on top of ai-discovery?**

## 0. Scope decision (owner, 2026-10-31)

ai-discovery becomes the **knowledge layer for autonomous agents**. External agents run the loop: Claude Code, Codex, the Claude Agent SDK, Cursor and CI bots. ai-discovery supplies five things to every stage of that loop, with honest provenance:
- context
- impact
- rationale
- safety
- verification contracts

The earlier decision stands: **no built-in orchestrator or agent runtime**, and **ai-discovery never executes the user's code**.

## 1. Framing: the agent loop and what a knowledge layer owes it

```
        ┌────────────── Freshness (index tracks HEAD) ──────────────┐
Orient → Locate → Plan → Edit → Verify → Review → Learn
        └────────────── Safety (untrusted content, secrets, budgets) ┘
```

| Stage | What the agent needs from us | Failure without it |
|---|---|---|
| Orient | Overview, entry points, conventions, in a file the agent reads at start (AGENTS.md / CLAUDE.md) | Agent greps blindly; it burns tokens and stops early |
| Locate | Hybrid search, symbol context, exact source bounds, screen/entity/table views | Agent pattern-matches text and misses DI, markup and SQL links |
| Plan | Impact (reverse traversal) with `unknowns`, rationale ("why is it like this"), risk | Missed blast radius; agent "fixes" intentional behaviour |
| Edit | Pinned files, style exemplars, governing decisions at edit time | Inconsistent idiom; violated decisions |
| Verify | Change impact of the working tree, impacted tests, build/test commands per stack | Agent declares success without running the right tests |
| Review | A diff of plan against actual impact; unplanned impact flagged | Silent scope creep |
| Learn | Write back findings and decisions, with signer kind; humans confirm | The same mistakes again next session |
| Freshness | Index follows commits automatically; every answer states how stale it is | Agent trusts a stale graph |
| Safety | Repo text marked as untrusted data; secrets masked; response budgets | Prompt injection through comments or docs; leaked secrets; context blow-up |

## 2. Readiness levels

| Level | Definition | ai-discovery today | repowise today |
|---|---|---|---|
| L0 | No agent surface (CLI/wiki for humans only) | **Here.** There is a FastAPI read API and the SPA wiki, but no MCP, no `query/` and no change impact | — |
| L1 | Read MCP: overview, search, context, freshness `_meta`, failure shield, budgets | Planned (M5.1, M5.3) | Yes: 18 tools, profiles, shield, budget contracts |
| L2 | Impact and change impact, plus automatic freshness | Planned (M5.2); freshness was **not planned** before this rework | Yes: change risk, impacted tests, post-commit hook, watch, webhooks |
| L3 | Write-back, rationale, safety envelope, verification contract, multi-client install | **Not planned** before this rework | Mostly. Decisions layer, `set_finding_status`, 6 agent targets. **No** labelling of untrusted content served over MCP; **no** auth on MCP over HTTP |
| L4 | Effect on autonomous agents proven by a pre-registered eval, published including losses | Planned (M5.7) | Partly. A published agent-loop benchmark (output tokens −31.6%, 3.8 vs 7.2 tool calls, `docs/BENCHMARKS.md`). Hooks judged by transcript replay, not randomised A/B |

**Summary.**
- ai-discovery has a **stronger fact base** than repowise for its target stacks:
  - measured edge precision of Java 0.98, C# 0.90, Python 1.00 (`benchmarks/2026-10-13-edge-contract.md`);
  - a business layer of screens, entities, tables and flows;
  - legacy UIs (WebForms, JSP).
- It has **zero agent surface**.
- The catch-up is mostly *plumbing and contracts*, not analysis.

## 3. Capability matrix

- **Severity:**
  - **P0** blocks L1/L2.
  - **P1** is needed for L3.
  - **P2** is polish, or comes later.
- **Decision:** Adopt / Adapt / Diverge / Skip, with the same meaning as in `repowise-benchmark.md`.

### Orient and Locate

| Capability | ai-discovery | repowise (behaviour) | Sev | Decision | Item |
|---|---|---|---|---|---|
| Shared read service behind MCP, CLI and API | Gap: `server/api_v1.py` only; `rag/retriever.py` is vector-only | One core used by MCP, CLI, REST and chat | P0 | Adopt | M5.1 |
| MCP server, stdio | Gap | 10 default, 7 opt-in and 1 workspace tool (`docs/agent/MCP_TOOLS.md`) | P0 | Adapt: 8 default tools centred on business entities | M5.3 |
| Tool profiles (default / lean / allowlist / `+`/`-`) | Gap | `lean` profile costs about 2.1k schema tokens against about 4.1k for the default | P1 | Adopt | M5.3 (amended) |
| Failure shield (no `isError`; success-shaped remedy) | Designed | Every tool is wrapped (`mcp_server/_failure_shield.py`) | P0 | Adopt | M5.3 |
| Per-tool response budget, rank-then-cap, omissions recoverable | Designed (about 8k cap, `omitted[]`) | Block drop order per tool; omission store with `ref` recall; CI contract test that every tool has a budget | P0 | Adopt + CI contract test | M5.1, M5.3 (amended) |
| `_meta` freshness (indexed commit, behind-by, stale warning) | Designed | Yes, plus `completeness`, `floor` and next-tool hints | P0 | Adopt | M5.1 |
| Hybrid search (FTS5 + vector, RRF) | FTS5 (`store/fts.py`) and vectors (`rag/`) exist separately | Hybrid with modes (concept/symbol/path) | P0 | Adopt | M5.1 |
| Symbol source with line bounds checked against the live file | Gap (`/source` API serves files) | `get_symbol` re-checks bounds against the live file | P1 | Adopt | M5.1 (`get_symbol_source` opt-in) |
| Screen / entity / table / flow context | **Built in pages**; not exposed to agents | **None** | P0 | **Diverge: our edge** | M5.1, M5.3 |
| Generated AGENTS.md / CLAUDE.md block | Gap (A8 was Exploring) | Deterministic block between markers; trimmed 30% because it is re-read about 50× per session | P1 | Adopt, with a token cap | **M5.11 (new)** |
| Install for several clients (Claude Code, Codex, Cursor, VS Code) | Gap (`.mcp.json` only) | 6 agent targets plus `print-config`; stands down when the host plugin is present, to avoid duplicate schemas | P1 | Adopt | **M5.14 (new)** |
| Stable Python API for bots and CI | Gap (`__init__` exports the version only) | The core imports without the server, for CI | P1 | Adopt | M5.1 (amended: `ai_discovery.api`) |

### Plan, Edit and Verify

| Capability | ai-discovery | repowise (behaviour) | Sev | Decision | Item |
|---|---|---|---|---|---|
| Impact (reverse BFS, depth cap, hub dampening, `unknowns`, `may_break`) | Gap: `graph/impact.py` covers only an entity's state machine | Blast radius through dependents and co-change | P0 | Adapt: business entry points, evidence per item | M5.2 |
| Change impact from a diff, range or working tree | Gap | `get_change_risk(revspec)` with no re-index | P0 | Adopt | M5.2 |
| Risk signals (churn, hotspots, ownership) | Gap (M8.1 planned, without a hook into impact) | Hotspot and health per file in `get_risk` | P1 | Adapt: a separate `risk` section, never merged into `items` | M8.1 (amended) |
| Impacted tests | Gap (M8.2 planned) | Per-test coverage gives `tests_to_run` | P1 | Adopt (+JaCoCo, coverlet) | M8.2 → M5.15 |
| Rationale / decisions ("why") | **Gap, not planned** | Large layer: mined from ADRs, commits, PRs, `WHY:` comments and transcripts; review queue; staleness tracking | P1 | Adapt: a small rationale overlay with provenance, no transcript mining in v1 | **M8.5 (new)** |
| Verification contract (build/test commands, smoke targets, plan diffed against actual impact) | Gap: M5.6 *lists* commands; the autonomous loop is out of scope | `directive` block in `get_risk` (`may_break`, `tests_to_run`, `breaking_changes`) | P1 | **Diverge:** compare the plan with the actual change impact, plus screen and endpoint smoke targets | **M5.15 (new)** |
| Change-plan skill with a human gate | Planned | `pre-modification` and `change-review` skills | P1 | Keep the gate in the skill | M5.6 |

### Learn, Freshness, Safety and Evaluation

| Capability | ai-discovery | repowise (behaviour) | Sev | Decision | Item |
|---|---|---|---|---|---|
| Agent write-back (findings, notes, proposed decisions) | Human page notes only (`pages/notes.py`) | `set_finding_status` over MCP; decisions through the CLI; agents can propose and withdraw, confirming needs opt-in | P1 | Adapt: opt-in MCP tools with signer kind and session id; never changes graph facts | **M5.10 (new)** |
| Post-commit auto-update | **Gap, not planned** | Installed by default; runs in the background | P0 | Adopt (opt-in at `init`) | **M2.8 (new)** |
| Watch mode | **Gap, not planned** | `repowise watch` with debounce | P2 | Adopt | **M2.8 (new)** |
| Webhooks and scheduler | Gap | GitHub/GitLab webhooks (HMAC), APScheduler | P2 | Skip for v1 (one project per process; CI can call `scan`) | — |
| MCP over streamable HTTP | Exploring | streamable-http and SSE; **no enforced auth**, only a warning on a wide bind | P1 | **Diverge:** a non-loopback bind *requires* a token | **M5.9 (promoted)** |
| Mark repo-derived text as untrusted data | **Gap** | **Not found**, except in dashboard chat | P1 | **Diverge: our edge** | **M5.12 (new)** |
| Secret masking (prompts and served source) | Output redaction (`core/redact.py`); prompt masking was Exploring | Redaction for the security scan and provider keys only | P1 | Adopt and extend to MCP | M1.13 (promoted), M5.12 |
| Agent tool-use telemetry and savings ledger | Gap | Per-call ledger, `repowise saved`, `--missed` | P2 | Adopt, local only | **M5.13 (new)** |
| Hooks (SessionStart / PostToolUse) | M5.8 Exploring | Several shipped; nudge hooks retired after measurement (11.4% follow-up against an 11.9% base rate) | P2 | Adopt **only after a randomised A/B** | M5.8 (amended) |
| Agent-loop eval | Planned (M5.7) | Published: 43 questions, 6 arms; external harness repo | P0 for GA | Adopt, plus headless and Codex arms and gold diffs | M5.7 (amended) |

### Deliberately not built

| Capability | repowise | Decision | Why |
|---|---|---|---|
| Built-in chat agent or orchestrator | 10-iteration tool loop, 15 LLM adapters | **Skip** | Owner decision: external agents orchestrate. `discover ask` stays a single-shot query |
| Dead code, health scores, refactoring codegen, security patterns | Yes | **Skip** | Outside the brownfield-understanding scope (`repowise-benchmark.md` E5) |
| Multi-tenant, RBAC, job queue | Planned there | **Skip** | One project per process; air-gapped single-user installs |
| Transcript mining (sessions, precedent, corrections) | Yes | **Skip for v1** | Privacy, and the value is unmeasured; revisit after M5.13 data |
| Multi-repo workspace | ~25 modules; `repo:"all"` | **Exploring.** Trigger: two or more pilot users whose frontend and backend live in separate repos *and* an M5.7 task that needs cross-repo impact | Brownfield estates are often split, but one-project-per-process keeps v1 simple |

## 4. Where ai-discovery is ahead, and how agents should benefit

1. **Business-entry-point impact.** Our impact ends at *screens, endpoints, batch jobs and table writes* (`screen_mapper.py`, `menu_detector.py`, the entity state machines). repowise ends at files and symbols. An agent changing `OrderService.cancel` can be told "screen *Order Edit* → `POST /api/orders/{id}/cancel` → writes `ORDERS.STATUS`, a guard in the order state machine". No competitor can say that.
2. **Legacy stacks.** WebForms markup → code-behind, JSP, and later ABAP/UI5 (M7). repowise skips `.aspx` and `.jsp` entirely.
3. **Honest graph.** Closed origin vocabulary, no edge on ambiguity, unresolved calls counted (ADR-0004). This gives impact items per-edge provenance that agents can weight.
4. **Runtime evidence as separate provenance** (M8.2, M8.3). repowise deliberately builds none.
5. **Places to beat repowise outright:**
   - authenticated HTTP from day one (M5.9);
   - untrusted-content labelling (M5.12);
   - randomised A/B before any hook ships (M5.8);
   - a plan-versus-actual review step (M5.15).

## 5. Catch-up sequence

**Critical path to L2 (Slice 3a):**
`M5.1 query/` → `M5.2 impact + change impact` → `M5.3 MCP stdio` → `M5.14 install targets` + `M5.11 agent instructions file` → `M5.4/M5.5 plugin + impact skill`.
Freshness `M2.8` runs in parallel once `M3.6` incremental parity lands.

**To L3 (Slice 3b):**
- `M1.13` → `M5.12 safety envelope`
- `M5.6 change-plan` → `M5.15 verification contract`
- `M5.10 write-back` (after M4.6 notes UI)
- `M8.1` (risk) → `M8.5 rationale + get_why`
- `M5.9 HTTP`
- `M5.13 telemetry`

**To L4:** `M5.7` eval with the headless, Codex and repowise arms, gated on `M6.13` accuracy thresholds.

| New or changed item | Lane | Change |
|---|---|---|
| M1.13 | B | Exploring → Planned; masker reused for MCP output |
| M2.8 | A | **New:** git hook through `init --git-hook`, `scan --watch` |
| M5.1 | E | + `ai_discovery.api` facade, + `_meta.content_trust` |
| M5.3 | E | + `lean` profile, + CI budget contract test |
| M5.6 | E | Clarified: human gate kept; autonomous use goes through M5.15 |
| M5.7 | E | + headless and Codex arms, + tool-call metric |
| M5.8 | E | Measured only by randomised A/B |
| M5.9 | E | **New** (was Exploring): streamable HTTP with required auth off loopback |
| M5.10 | E | **New:** agent write-back with signer kind |
| M5.11 | E | **New:** generated AGENTS.md / CLAUDE.md block |
| M5.12 | E | **New:** content-safety envelope |
| M5.13 | E | **New:** local tool-use ledger |
| M5.14 | E | **New:** install targets for several clients |
| M5.15 | E | **New:** `get_verification_plan` + `discover-verify-change` skill |
| M8.1 | G | + `risk` section in change impact |
| M8.5 | G | **New:** rationale overlay + `get_why` |

## 6. Measures of success (pre-register in M5.7 before running)

All targets below are **(Unverified)** until measured.

- **Recall.** Files-touched recall against the gold diff, arm (b) ai-discovery compared with arm (a) bare agent: improves, with no precision loss. This gates "GA", as before.
- **Efficiency.** Median output tokens and tool calls per task: lower in arm (b). repowise reports −31.6% and 3.8 vs 7.2 calls on django questions; ours are change tasks, so the numbers are not directly comparable.
- **Verification.** With M5.15, the share of tasks where the agent ran the impacted tests before declaring success.
- **Honesty.** No answer reports a confident zero on an unresolved target. Every injection fixture in the M5.12 golden set comes out labelled as untrusted.
- **Freshness.** With M2.8 installed, the median `behind_by` at session start is 0.
