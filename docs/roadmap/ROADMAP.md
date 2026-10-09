# ai-discovery v1 Roadmap

No dates — status is the commitment (repowise-style). Item-level status lives in `milestones/*.md`; run `python scripts/roadmap_lint.py --board` for live counts.

| Status | Meaning |
|---|---|
| Done | Merged to main |
| In progress | Claimed on a branch |
| Planned | Committed, not started |
| Exploring | Good idea, not committed; never auto-picked |

## Milestones

| # | Milestone | Goal | Status |
|---|---|---|---|
| M0 | [Stabilize & measure](milestones/M0-stabilize.md) | Fix defects that survive any rewrite; golden harness; baseline vs repowise | Planned |
| M1 | [Foundations (strangler)](milestones/M1-foundations.md) | Paths, config v2, store + lock, stages, llm/, edge contract | Planned |
| M2 | [CLI v1 (hard break)](milestones/M2-cli-v1.md) | 11 commands (+ `evidence` in M8), cost gate, status/doctor | Planned |
| M3 | [Page model & keyless wiki](milestones/M3-pages.md) | Deterministic pages, prose layer, SDLC export profile | Planned |
| M4 | [Wiki web](milestones/M4-wiki-web.md) | React SPA in wheel, `serve` | Planned |
| M5 | [MCP + Claude Code](milestones/M5-mcp-agents.md) | Query service, impact, MCP, skills, agent eval | Planned |
| M6 | [Graph accuracy](milestones/M6-graph-accuracy.md) | Walker, C#/Java/VB/Vue gaps, DI, SQL/procs/MyBatis, CI gates | Planned |
| M7 | [SAP ABAP + UI5](milestones/M7-sap.md) | abapGit + Fiori/UI5 extraction | Planned |
| M8 | [Evidence overlays](milestones/M8-overlays.md) | Git history, coverage, OTLP spans | Planned |

## Slices

**Slice 1 — first lovable slice (★ items):** keyless `discover init → scan → serve` with the SPA wiki.
Critical path: M0.2 → M0.6 → M0.10 → M2.3 → M3.5 → M4.3 → M4.5, with M1.2 → M1.4 → M1.5 → M3.1 → M3.2/M3.3 and M4.1/M4.2 in parallel.

**Slice 2:** prose layer + cost gate (M1.10–M1.11, M2.4, M3.7–M3.8). The legacy ASIS/ASD/ASSC SDLC tree is dropped (owner decision 2026-10-08); its LLM stages are deleted in M3.7.

**Slice 3:** MCP + skills + agent eval (M5), gated for "GA" on M6.13 accuracy thresholds.

**Parallel tracks:** M6 (lane C) — parser-coverage items (M6.2, M6.3, M6.5) can start after M0.3; walker/resolution items wait for the M0.12 baseline; M7 (lane F) after M1.12; M8 (lane G) after M1.12, low priority.

```mermaid
graph LR
  M0[M0 Stabilize] --> M1[M1 Foundations]
  M0 --> M6[M6 Graph accuracy]
  M1 --> M2[M2 CLI v1]
  M1 --> M3[M3 Pages]
  M2 --> M3
  M3 --> M4[M4 Wiki web]
  M3 --> M5[M5 MCP + agents]
  M1 --> M7[M7 SAP]
  M1 --> M8[M8 Overlays]
  M6 --> M5
  M4 --> M3x[M3.10 static export]
```

## What we are not building

| Not building | Why |
|---|---|
| A built-in BA/orchestrator agent | Owner decision: Claude Code orchestrates; we provide MCP tools + skills |
| A graph database (Neo4j/Memgraph) | SQLite + in-memory traversal is sufficient at our scale and keeps install trivial |
| Claims of method-level runtime tracing via eBPF/OTel auto-instrumentation | Verified false: those give request-level spans only (`research/research-doc-review.md`) |
| An LLM PR reviewer | Non-deterministic; out of scope |
| A Node runtime dependency | Air-gapped enterprise installs; SPA is prebuilt into the wheel |
| Multi-project server process | One project per process keeps config/state simple |
| Pooled cross-repo recall numbers | Misleading (benchmark protocol) |
| Dates on this roadmap | Status is the commitment |
| SDLC export profile (ASIS/ASD/ASSC/PF/BPMN/DMN/EARS folder tree) | Owner: not needed (2026-10-08). Entity FSM/DMN/EARS and flows live on wiki pages instead |

**Exploring (not committed):** multi-repo workspace (old `federate`), MCP HTTP transport, output languages other than English, presentation mode, secret masking in prompts (M1.13), SessionStart hook (M5.8), profiler stacks (M8.4).

## Top risks

| Risk | Mitigation |
|---|---|
| Strangler regressions in LLM phases | Recorded-response golden harness (M0.2); move code verbatim before changing it (ADR-0003) |
| MCP/impact answers only as good as the graph | Edge contract early (M1.12); CI accuracy gates (M6.13); unknowns surfaced; `may_break` wording |
| Edge contract lowers recall | Measure vs M0.12; report unresolved counts; tune per origin rather than re-adding guesses |
| Thin ground truth for WebForms / VB.NET / JSP / CP949 / ABAP | `../sample-apps` covers only ASP.NET Core + Spring; add repos when available; synthetic encoding fixtures; assumptions labelled (Unverified) |
| Enterprise install (Windows, air-gapped, pysqlite3/sqlite-vec/FTS5) | Wheel smoke test (M4.5); `doctor` checks (M2.5); vendored web assets |
| Parallel-agent conflicts | Lanes with single owners, claim-before-branch, migration number reservations |
| AGPL contamination from repowise | ADR-0006 clean-room rule + PR attestation |

## Owner decisions (2026-10-08)

1. **License:** AGPL-3.0 (`LICENSE`, `pyproject.toml`). The repowise no-copy rule stays as an engineering policy (ADR-0006).
2. **Benchmark apps:** `../sample-apps/eShop` (ASP.NET Core) and `../sample-apps/spring-petclinic` (Java/Spring). No WebForms, JSP, VB.NET or ABAP repo yet — add one when available.
3. **SDLC export profile:** not needed; dropped (formerly M3.9).
