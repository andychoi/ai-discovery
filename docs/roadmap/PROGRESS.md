# Roadmap Progress Log

Append-only. Newest entries at the bottom. One entry per session/item.

## Entry template

```
## YYYY-MM-DD — <item IDs> — <one-line outcome>
- **Branch/commit:** rm/<ID>-<slug> @ <sha>
- **Changes:** what was added/changed (paths)
- **Decisions:** anything not in the design docs (link ADR if a contract changed)
- **Verify:** commands run + result (paste summary lines)
- **Bench/golden changes:** none | <justification>
- **Follow-ups / open questions:** new items filed, blockers
```

## Reservations

| Kind | Number | Item | Date |
|---|---|---|---|
| migration | — | — | — |

---

## 2026-10-08 — roadmap scaffold — roadmap, design, ADR and research docs created
- **Branch/commit:** uncommitted on main (docs + `scripts/roadmap_lint.py` + `tests/test_roadmap_lint.py` only)
- **Changes:** `docs/roadmap/**` (README, ROADMAP, milestones M0–M8, adr 0001–0006, design ×8, research ×4), `scripts/roadmap_lint.py`, `.claude/skills/roadmap-next/SKILL.md`, pointers in `CLAUDE.md` and `docs/INDEX.md`, review banner on `docs/new-requirement-research.md`.
- **Decisions (owner):** MCP tools + skills only (no built-in orchestrator); target stacks ASP.NET/WebForms, Java/Spring/JSP, TS/JS, Python, SAP ABAP (abapGit) + Fiori/UI5; CLI hard break; own engine with repowise patterns, no repowise code; React SPA bundled in wheel; license unchanged (mismatch logged as open item).
- **Inputs:** repowise v0.53.0 @ `bcf4d96e` (docs, tests, changelog read for edge cases); verification of `docs/new-requirement-research.md` claims (2026-10-08 web check).
- **Verify:** `python scripts/roadmap_lint.py` → ok; no product code changed.
- **Follow-ups:** start with `python scripts/roadmap_lint.py --next`.

## 2026-10-08 — owner decisions — license, benchmark apps, SDLC export dropped
- **Changes:** `LICENSE` (AGPL-3.0 text) added; `pyproject.toml` license → `AGPL-3.0-only` + AGPLv3 classifier; README license line links `LICENSE`. Benchmark protocol + M0.12 use `../sample-apps/eShop` (@ dc7ea49, ASP.NET Core) and `../sample-apps/spring-petclinic` (@ 500158f). M3.9 (SDLC export profile) removed; legacy SDLC generators/rollup deleted in M3.7 instead; references updated (ROADMAP, ADR-0003/0005/0006, cli-v1, architecture-v1, audit).
- **Decisions:** ADR-0006 no-copy rule kept as engineering policy even though licenses are now compatible.
- **Verify:** `python scripts/roadmap_lint.py` → ok (76 items).
- **Follow-ups:** no WebForms/JSP/VB.NET/ABAP sample repo yet.
