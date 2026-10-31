# ai-discovery v1 Roadmap — Start Here

This folder is the **operating manual for implementing ai-discovery v1** across many Claude Code sessions and sub-agents. A fresh session with no prior context should be able to read this file, pick the next work item, implement it, verify it, and record the result.

Last updated: 2026-10-31

## Why v1

ai-discovery works but grew too wide: 13 CLI commands / 69 options, a 2,071-line `pipeline.py`, 7 LLM providers, three disagreeing config templates, and several correctness bugs (see `research/ai-discovery-audit.md`). v1 keeps what is unique — screen-centric docs, entity FSM/DMN/EARS, business-process artifacts for legacy enterprise stacks — and rebuilds the shell around patterns benchmarked from repowise (`research/repowise-benchmark.md`):

- **Keyless first:** `init → scan → serve` produces a full structural wiki with no LLM key; prose is an opt-in layer.
- **Small CLI** (hard break): `init, scan, status, doctor, serve, mcp, ask, impact, export, drift, push` (+ `evidence` with M8).
- **Wiki web:** React SPA bundled in the wheel, served by FastAPI on one port, offline.
- **Agent platform (knowledge layer):** MCP tools (stdio + HTTP) + skills (impact / change-plan / verify-change), freshness triggers, write-back, safety envelope and verification contract for autonomous agents. External agents (Claude Code, Codex, Agent SDK, CI bots) orchestrate; we never run the loop or execute user code (`research/agent-platform-assessment.md`).
- **Honest graph:** closed edge-origin vocabulary, no guessing on ambiguity, unknown ≠ zero, measured precision/recall.
- **Target stacks:** ASP.NET/WebForms (C#, VB.NET), Java/Spring/JSP, TS/JS SPAs, Python, SAP ABAP (abapGit) + Fiori/UI5.

## Read order

1. This file.
2. `ROADMAP.md` — milestones, slices, dependency graph, not-building list, risks, open items.
3. The milestone file for your item: `milestones/M*.md` — **single source of truth for item status**.
4. The design doc(s) and ADRs the item references (`design/`, `adr/`).
5. Relevant rows in `research/edge-case-catalog.md` and `research/ai-discovery-audit.md`.
6. Last ~5 entries of `PROGRESS.md`.

| Folder | Contents |
|---|---|
| `milestones/` | M0–M8 work items (status, deps, lane, owned files, acceptance, verify) |
| `adr/` | Architecture decision records (0001–0006; more added as decisions are made) |
| `design/` | architecture-v1, cli-v1, wiki-web, mcp-and-agents, graph-accuracy, sap-abap-ui5, evidence-overlays, benchmark-protocol |
| `research/` | repowise benchmark, agent-platform assessment (readiness levels, capability matrix), edge-case catalog, ai-discovery audit, review of `docs/new-requirement-research.md` |
| `benchmarks/` | Benchmark results (created by M0.12) |
| `PROGRESS.md` | Append-only session log |

## Environment

- Python ≥ 3.10; install: `pip install -e ".[dev]"`.
- Fast tests: `pytest -q`. Corpus accuracy: `pytest -q -m accuracy`. Slow/smoke tests are marked and skipped by default once introduced.
- No network or LLM keys needed for tests (stub LLM from M0.2).
- SPA build (after M4.2): `python scripts/build_web.py` (requires Node at build time only).
- Roadmap tooling: `python scripts/roadmap_lint.py` (validate), `--next [--lane X]` (ready items), `--board` (status counts).

## Work-item protocol

0. **One-time:** the roadmap scaffold itself (this folder, `scripts/roadmap_lint.py`, `.claude/skills/roadmap-next/`) must be committed on main before the first claim.
1. **Green baseline.** Run `pytest -q` on main. If red, stop and add/fix a work item for it first.
2. **Pick.** `python scripts/roadmap_lint.py --next` (optionally `--lane X`) lists ready items with ★ critical-path items first — take the first, or the item the user names. Never auto-pick `Exploring` items.
3. **Claim.** In the milestone file set `Status: In progress (rm/<ID>-<slug>, <YYYY-MM-DD>)`; commit that one-line change to main before branching, so parallel agents see the claim.
4. **Split check.** If the item won't fit in one session, split it in the milestone file into `<ID>.a`, `<ID>.b` (same fields) before coding.
5. **Branch.** `rm/<ID>-<slug>` (e.g. `rm/M1.6-stages-front`). Sub-agents use worktree isolation, one item per agent.
6. **Ownership.** Edit only the item's **Owns** paths. Needing to change another lane's file → add a contract-change item (or note in PROGRESS.md and ask), don't edit it. Reserve migration numbers in PROGRESS.md before creating migration files.
7. **Implement** following the referenced design doc. If the design must change, update the design doc / ADR in the same PR (CLAUDE.md principle 2).
8. **Verify.** Run the item's **Verify** commands plus `pytest -q`. Golden snapshots and bench baselines change only with a one-line justification in PROGRESS.md.
9. **Close.** Set `Status: Done (<PR # or branch-head short-sha>)` in the same PR (the squash SHA isn't known yet; that's fine); append a PROGRESS.md entry; run `python scripts/roadmap_lint.py`. Squash-merge.

## Lanes (parallel work boundaries)

| Lane | Owns | Notes |
|---|---|---|
| A Core | `pipeline.py`, `stages/`, `core/`, `config/`, `store/`, `cli/`, `cli.py`, `db.py`, `viewer/` (until M4.6) | **Serial — one agent at a time** |
| B LLM | `ai/llm_client.py`, `ai/summarizer.py`, `shared/`, `llm/`, `prompts/`, `pages/prose.py`, `pages/grounding.py` | |
| C Extraction | `parsers/` (except `abap/`, `ui5/`), `extractors/`, `repo/`, `graph/`, `route_parser.py`, `*_extractor.py` | |
| D Wiki | `pages/` (except prose/grounding), `server/`, `web/`, `export/`, `generators/` | |
| E Agent | `query/`, `mcp/`, `plugin/`, `bench/agent/` | |
| F SAP | `parsers/abap/`, `parsers/ui5/` | |
| G Overlays | `overlays/` | |
| Q Bench | `bench/` (except `agent/`), `tests/corpus/`, `docs/roadmap/benchmarks/` | |

**Explicit cross-lane exceptions** (the item's Owns list wins over the lane table for these paths):

| Item | Lane | Owns outside its lane |
|---|---|---|
| M0.4 | C | `core/encoding.py` (+ one call site in `pipeline.py`, coordinated with lane A) |
| M0.8 | A | `repo/resolver.py` |
| M1.12 | C | one migration file under `store/migrations/` (number reserved in PROGRESS.md) |
| M4.5 | D | `cli/commands/serve.py` |
| M5.3 | E | `cli/commands/mcp.py` |
| M8.2 | G | `cli/commands/evidence.py` |
| M5.10 | E | one migration file under `store/migrations/` (number reserved in PROGRESS.md) |
| M5.13 | E | `cli/commands/status.py` (`--agent` section, coordinated with lane A) |
| M8.5 | G | `mcp/tools/get_why.py` (auto-discovered tool) + one migration file (number reserved in PROGRESS.md) |
| M0.3 → M2.x | C → A | `tests/cli/` is created by M0.3 (lane C) and owned by lane A from M2.1 on |

Rules: registries (parsers, page builders, stages, MCP tools, post-scan hooks) are auto-discovered so adding one never edits a central list; `pyproject.toml` dependency edits are append-only; each migration is its own numbered file.

## Clean-room rule (ADR-0006)

repowise (`../repowise`) is AGPL-3.0. **Do not copy, translate, or paste its code.** Work from the prose in `research/edge-case-catalog.md` and the design docs. repowise may be *run* as a black box for benchmarks (lane Q). Every PR description includes: "No repowise code was copied or translated."

## Honesty rules (from the org directives and repowise lessons)

- Unknown is not zero: an unresolved target is an error or `unknown`, never a confident empty answer.
- No silent degradation: every skipped file, fallback decode, failed LLM item or truncation is counted and surfaced.
- Label unverified assumptions in docs as (Unverified) until measured.
