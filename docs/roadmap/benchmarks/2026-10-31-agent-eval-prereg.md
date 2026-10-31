# Pre-registration: agent-loop eval (M5.7)

- **Status: Draft** — awaiting owner approval of the spend ceiling and bands. Nothing has been run.
  To freeze: set `status: Frozen` in the block below, commit this page alone, and record the commit here.
  After freezing, changes are **deviations** (listed at the end of the results page), never edits.
- **Freeze commit:** _(none yet)_
- **Harness:** `bench/agent/` (roadmap item M5.7.a) · **Tasks:** `bench/agent/tasks.toml` (15 tasks)
- **Question:** does ai-discovery (MCP tools + skills + instructions block) help a coding agent find and change the right files in a brownfield repo, at acceptable cost? (design/mcp-and-agents.md §6, benchmark-protocol.md §6)

<!-- prereg
status: Draft
model: claude-sonnet-5-5
codex_model: none
reps: 3
seed: 20261031
arms: bare, discover, discover-auto
exploratory_arms: codex-bare, codex-discover, repowise
budget_per_run_usd: 4.00
budget_total_usd: 400
timeout_minutes: 30
bootstrap_resamples: 10000
-->

## 1. Tasks

15 historical changes, 3 stacks, chosen by fixed rules (`bench/agent/mine.py`): non-merge commits touching 2–8 source files and ≤ 400 changed lines, whose message reads as a behaviour change (not a bump, docs, CI, rename, refactor or release). Prompts were written from each commit's message and diff as a ticket would state the requirement, and never name a file (checked by `python -m bench.agent validate`).

| Stack | Repo (pinned history) | Tasks |
|---|---|---|
| Java / Spring MVC + JPA | spring-petclinic | pc-page-bounds, pc-pet-association, pc-unique-pet-names, pc-edit-pet-name, pc-birthdate, pc-confirmations |
| C# / ASP.NET Core + Blazor | dotnet/eShop | es-mask-cards, es-chat-images, es-remove-card-ui, es-catalog-openapi |
| JavaScript / Express + Mongoose | node-express-realworld-example-app | nx-tags, nx-follow, nx-comments, nx-favorites, nx-profiles |

**Gold** = source files changed by the commit (tests listed separately and excluded from both sides of the file metrics).
**Workspace** = the parent commit's files in a fresh one-commit repository (`git archive`): the agent cannot read the answer from history.

## 2. Arms

All arms get the byte-identical task prompt plus the same fixed suffix (`bench/agent/arms.py`), the same model, the same per-run spend cap and timeout, `--setting-sources project` and `--strict-mcp-config` (no operator settings, plugins, hooks or MCP servers leak in).

| Arm | Role | Gets |
|---|---|---|
| `bare` | primary | Claude Code, no MCP servers, no plugin |
| `discover` | primary | + keyless `discover scan` of the workspace, ai-discovery MCP server (`default,+get_verification_plan,+get_symbol_source`), the plugin's skills, the generated CLAUDE.md block |
| `discover-auto` | secondary | `discover` + operator pre-approval to plan (discover-change-plan) → implement → verify (discover-verify-change) without stopping |
| `codex-bare`, `codex-discover` | exploratory | Codex CLI without / with the ai-discovery MCP server; enabled only if `codex_model` is set |
| `repowise` | exploratory | Claude Code + repowise's MCP server (black box); enabled only if `BENCH_REPOWISE_MCP` is set |

Exploratory arms never decide the verdict.

## 3. Metrics (per run; the harness measures, the agent's own claims are ignored)

- **Files-touched recall** = |touched ∩ gold| / |gold|; **precision** = |touched ∩ gold| / |touched| (source files only).
- **Build / tests**: the stack's commands run by the harness after the agent stops (`bench/agent/tasks.py:BUILD`).
- **Output tokens** (headline cost metric), total cost, turns, tool calls (transcript), ai-discovery tool calls (transcript + M5.13 ledger).
- **Ran tests before finishing**: the transcript contains a test command.

## 4. Hypotheses and bands

Unit = task (mean over its 3 repetitions). Paired per-task differences, `discover − bare`. 95% CIs by percentile bootstrap over tasks (10,000 resamples, seed above).

| ID | Claim | Pass band |
|---|---|---|
| **H1** (primary) | ai-discovery raises files-touched recall | mean diff ≥ **+0.10** and CI lower bound **> 0** |
| **H2** (primary) | …without losing precision | mean diff ≥ **−0.05** and CI lower bound ≥ **−0.10** |
| S1 | cost stays acceptable | median per-task output-token ratio ≤ **1.25** |
| S2 | tool use is not wasteful | reported only (tool calls per run, repeats from the ledger) |
| S3 | the autonomous arm verifies | share of `discover-auto` runs that ran tests ≥ **0.80** |

**GA rule:** H1 and H2 pass and the M6.13 accuracy gate is green at the run commit → the `discover-impact` workflow becomes the recommended default. Otherwise the result is published as a loss or a null, with the per-stack breakdown.
The bands are mirrored in `bench/agent/score.py:BANDS`; a test keeps the two in sync.

## 5. Procedure and exclusions

1. Environment: a disposable machine or container (agents run with `bypassPermissions` inside the workspace) with JDK 17+, .NET 9 SDK and Node 20. Record OS and tool versions.
2. `python -m bench.agent validate` and `python -m bench.agent preflight` before the first run. A task whose **unmodified base** fails to build or test has that metric excluded for every arm (recorded, never silently).
3. Runs execute in the frozen random order (`python -m bench.agent plan`), resumable; every transcript and diff is kept under `bench/agent/results/`.
4. **Infrastructure failures** (harness error before the agent starts, provider outage) are re-run once. **Agent outcomes** — timeout, spend cap reached, no edits — count as they are.
5. The runner stops before a run could cross the total ceiling; unfinished cells are reported as missing, never imputed.

## 6. Spend ceiling (to approve)

- 15 tasks × 3 arms × 3 repetitions = **135 runs**.
- Per run: capped at **$4.00** by `claude --max-budget-usd`.
- Total: capped at **$400** by the runner, which tracks each run's reported cost and stops before the next run could cross it.
- The expected spend is unknown until real runs report cost; the ceilings bound it. Lowering `reps` to 2 reduces the batch to 90 runs.

## 7. Threats to validity (stated in advance)

- The gold diff is one correct answer; a valid alternative touching other files loses precision.
- Commit messages may under-describe the change; the prompts were written from the diff, which may make them easier than real tickets.
- spring-petclinic is a well-known public repo and may be in the model's training data; eShop and the node app less so. The per-stack breakdown is reported.
- The `discover` arm also changes the repository's CLAUDE.md, by design: the instructions block is part of what is being measured.
