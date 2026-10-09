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

## 2026-10-08 — M0.2 — golden characterization harness in place
- **Branch/commit:** rm/M0.2-golden-harness @ 8226863
- **Changes:** `tests/golden/stub_llm.py` (deterministic `LLMClient` stand-in: prompt-hash lookup in optional `recorded_llm.json`, tier-shaped placeholders, schema-default structured output, call log; unknown attributes raise), `tests/golden/test_golden.py` (composed fixtures `java-spring-jsp`, `dotnet-webforms`; two scans must match; compare to `tests/golden/snapshots/*.json`; `UPDATE_GOLDEN=1` to rewrite).
- **Decisions:** injection seam is `ai_discovery.ai.llm_client.LLMClient` (as in `tests/test_integration.py`), not `shared/llm_router` functions; embedding/search and self-review persistence patched as in the integration test. Machine-specific path segments that leak into domain names are normalized to `<abs-path-segment>`.
- **Verify:** `pytest -q tests/golden` → 2 passed; tampered golden → fails with update hint; full `pytest -q` → 1126 passed, 3 skipped (baseline 1124 + 2); `roadmap_lint` ok.
- **Bench/golden changes:** initial golden snapshots created.
- **Environment note:** tests run from a local `.venv` (`uv venv .venv --python 3.12 && uv pip install -e ".[dev]"`); `.venv` is gitignored.
- **Follow-ups:** audit #81 domain named from absolute path segment (lane C); audit #82 `screens` table never written (M3.3). Stub screen/tier1 structured calls return schema defaults, so `screen_specs` content is placeholder.

## 2026-10-08 — M0.3 — CLI smoke tests + tree-sitter floor
- **Branch/commit:** rm/M0.3-cli-smoke-ts-floor @ 78b8cc8
- **Changes:** `tests/cli/test_cli_smoke.py` (command-set guard, `--help` for all 13, one no-network path each), `tests/test_ts_floor.py` (module Queries compile, each parser extracts ≥1 node, route_parser grammars load), `pyproject.toml` tree-sitter floors.
- **Decisions:** known bugs asserted as correct behaviour under `xfail(strict=True)` (ingest-docs NameError, verify-drift exit 0) — M0.9 must remove the markers. Floor verified empirically: throwaway venvs with tree-sitter 0.24.0 (9/10 fail, ImportError QueryCursor) and 0.25.0 + c-sharp 0.23.1 / java 0.23.5 / python 0.23.6 / javascript 0.23.1 / typescript 0.23.2 (10/10 pass).
- **Verify:** `pytest -q tests/cli tests/test_ts_floor.py` → 35 passed, 2 xfailed; full `pytest -q` → 1161 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** audit #84 scan traceback on missing repo (M2.3). Existing `tests/test_cli_commands.py` (function-level detect/verify tests) left as is; fold into tests/cli when M2 replaces the CLI.
