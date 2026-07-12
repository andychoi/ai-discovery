# docs/reviews — Architecture Review (2026-07-12)

Principal-architect review of the full repository at commit `d9d997b`. No code was modified; these documents record the analysis, reasoning, recommendations, and decisions requested before implementation work begins.

| Document | Deliverable |
|---|---|
| [00-executive-summary.md](00-executive-summary.md) | Executive summary, confirmed-bug list, scorecard |
| [01-architecture-overview.md](01-architecture-overview.md) | Product vision, layered architecture, end-to-end 18-phase workflow, doc/code divergences |
| [02-complex-algorithms.md](02-complex-algorithms.md) | Deep analysis: call resolution, FSM identity, Louvain batching, screen detection |
| [03-ai-fable-integration-review.md](03-ai-fable-integration-review.md) | Anthropic/Claude integration review; Fable-class model adoption plan (incl. stated assumption — no Fable integration exists today) |
| [04-risks-and-technical-debt.md](04-risks-and-technical-debt.md) | Risk register: P0 correctness, P1 structural debt, P2 performance, P3 hygiene, test/security gaps |
| [05-roadmap-and-refactoring-plan.md](05-roadmap-and-refactoring-plan.md) | Prioritized roadmap (impact × effort, 4 waves), refactoring sequencing, explicit non-recommendations |

Method: four parallel code-analysis passes (orchestration, graph algorithms, parsers/extraction, output/RAG/viewer/tests) plus a direct review of the LLM integration layer against current Anthropic API capabilities. All findings cite `file:line` at the reviewed commit.

Related prior material: `docs/review/system-logic.md` (2026-06-06 system-logic walkthrough), `docs/assessments/` (earlier gap assessments). This folder supersedes neither — it is a point-in-time architecture review with an actionable roadmap.
