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

## 2026-10-08 — M0.4 — encoding fallback
- **Branch/commit:** rm/M0.4-encoding-fallback @ 39b2b9a
- **Changes:** `src/ai_discovery/core/{__init__,encoding}.py` (new); `parsers/{csharp,java,python_parser,javascript}.py` read via `read_source` and stamp `framework_hints.source_encoding`; `parsers/{jsp,webforms,import_map}.py`, `jsp_extractor.py`, `webforms_extractor.py` read via `read_source` (previously `errors="ignore"` silently deleted Hangul); `tests/test_encoding.py`, `tests/fixtures/encoding/` (UTF-8 sources; CP949/EUC-KR/UTF-16/BOM twins generated at test time).
- **Decisions:** strict UTF-8 is tried **before** a declared charset hint (design doc updated) — a JSP still declaring EUC-KR after conversion to UTF-8 must not decode as mojibake. Fallback stats count distinct files (JSP/WebForms files are read by both extractor and parser).
- **Cross-lane edit (README exception):** `pipeline.py` parse phase — reset fallback stats before parsing, print/log the count after. 9 lines, no behaviour change for UTF-8 repos.
- **Verify:** `pytest -q tests -k encoding` → 26 passed; same twin tests on pre-change parsers → 12 failed (proves coverage); full `pytest -q` → 1187 passed, 3 skipped, 2 xfailed; golden unchanged; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** other readers outside parsers still use `errors="ignore"` (e.g. menu/route/SQL extractors) — sweep in M6.1 walker work; file-level encoding hints from `web.config`/`.editorconfig` not used yet.

## 2026-10-08 — M0.5 — budget enforced for all paid providers
- **Branch/commit:** rm/M0.5-budget-all-providers @ 85e03ef
- **Changes:** `pipeline.py`: `_is_metered()` (not in `shared.model_defaults._LOCAL_PROVIDERS`) replaces the `provider != "bedrock"` checks in `_budget_ok`/`_budget_exhausted_fn`; `run_pipeline` returns final status; end-of-run check marks `budget_exceeded` when spend hit the limit mid-phase; `cli.py` scan exits 3 on `budget_exceeded`. `tests/test_budget.py` (23 tests incl. end-to-end OpenAI over-budget scan via golden stub).
- **Decisions:** a mid-phase budget hit now records `budget_exceeded` instead of `completed`, so the run is resumable after raising `--budget` (resume treats non-completed runs as unfinished). Imported the private `_LOCAL_PROVIDERS` from lane-B `shared/model_defaults.py` read-only rather than editing it; M1.10 should expose a public capability for this.
- **Verify:** `pytest -q tests -k budget` → 23 passed (+ existing); full `pytest -q` → 1210 passed, 3 skipped, 2 xfailed; golden unchanged; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** M1.10 — public `is_metered`/capability in `llm/`; M2.4 adds the pre-run estimate/cost gate.

## 2026-10-08 — M0.6 — resume correctness
- **Branch/commit:** rm/M0.6-resume-correctness @ cd605f3
- **Changes:** `pipeline.py`: `_repo_keys` + multi-key `_find_previous_run`; refuse resume across commits (returns `resume_refused`; `cli.py` exits 2); phase 13 persists flows and the resume branch loads them via `load_scenario_flows`; `_SKIPPABLE_PHASES` validation; `_iter_with_deadline` for self-review (deadline counted from start, not submit). `ai/rollup.py`: `_load_rollups_from_db` keeps only rollup doc types. `tests/test_resume.py` (11 tests).
- **Decisions:** reject (rather than implement) skipping phases 14–19 — they're replaced by named stages in M1.7/M1.8. When any self-review times out, the executor is shut down with `wait=False`: `_review_one` only reads (claims are persisted in the main loop), so abandoning a stuck thread can't race writes; its LLM cost may still accrue in the client.
- **Cross-lane edit:** `ai/rollup.py` one-line filter (resume crash found by the new test: phase-15 `onboard` docs have no rollup template).
- **Verify:** `pytest -q tests/test_resume.py` → 11 passed; full `pytest -q` → 1221 passed, 3 skipped, 2 xfailed; golden unchanged; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** runs created before this change have no persisted phase-13 flows; resuming them past 13 now warns and suggests `--resume-from 13` instead of silently re-paying.

## 2026-10-08 — M0.8 — secret hygiene
- **Branch/commit:** rm/M0.8-secret-hygiene @ b6348b8
- **Changes:** `core/redact.py` (new); `pipeline.py` config_json via `redact_mapping`; `db.py` `record_phase_error` uses `safe_message` (redact then truncate), `SCHEMA_VERSION` 14→15 with `_scrub_secrets` migration; `repo/resolver.py` `_git` helper (timeout `DISCOVERY_GIT_TIMEOUT`=900s, `GIT_TERMINAL_PROMPT=0`, credentials via `GIT_CONFIG_COUNT/KEY_0/VALUE_0` `http.extraHeader`), `_clean_url`, `_scrub_remote`; `ResolvedRepo.url` is credential-free. `tests/test_secret_hygiene.py` (16 tests).
- **Migration reserved/used:** db.py schema v15 (legacy inline migration; numbered migrations start with M1.4).
- **Decisions:** redaction is key-name based for mappings (unset values kept, so the record shows a setting was empty) plus pattern based for free text. Header credentials require git ≥ 2.31 (`GIT_CONFIG_COUNT`); `doctor` (M2.5) should check the git version.
- **Verify:** `pytest -q tests/test_secret_hygiene.py` → 16 passed; config_json leak test fails against the previous `pipeline.py` (proves the leak existed); full `pytest -q` → 1237 passed, 3 skipped, 2 xfailed; golden unchanged; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** M2.5 doctor: git ≥ 2.31 check. Secrets inside *source code* sent to LLMs remain M1.13 (Exploring). Remote clone paths still live under the output dir (moves with M1.2).

## 2026-10-08 — M0.10 — keyless --no-llm mode (+ tree-sitter 0.26 crash fix)
- **Branch/commit:** rm/M0.10-no-llm @ 6dcea40
- **Changes:** `config.py` `no_llm`; `cli.py` `--no-llm`; `pipeline.py` `_llm_skipped` gates phases 10–14, onboarding, 17 and the Ollama warm/unload calls; phase 2 writes structural specs; `ai/screen_spec_generator.py` `_spec_from_mapping` + `structural_screen_spec` (cross-lane, shared constructor so LLM and keyless specs can't drift). `tests/test_no_llm.py` (5): openai/bedrock/ollama with no keys and sockets blocked; keyless graph == LLM-scan graph; CLI flag.
- **Found while verifying on real apps (`../sample-apps`):** py-tree-sitter **0.26.0 segfaults** on eShop `CatalogApi.cs` (and corrupts memory, crashing petclinic later). Original pre-roadmap code crashes identically → pre-existing. Pinned `tree-sitter>=0.25,<0.26` (cross-lane: pyproject grammar pins are lane C/M0.3); regression fixture `tests/fixtures/regressions/ts026-segfault/` (MIT, NOTICE.md) parsed in a subprocess by `test_ts_floor.py`. Also: UTF-8 BOM no longer counted as an encoding fallback (461 false "fallbacks" on eShop).
- **Real-app keyless results:** spring-petclinic 248 nodes / 50 files / 1278 edges / 1 domain, 1s; eShop 1389 nodes / 567 files / 3271 edges / 319 domains, 1s; no screens (no supported menu format: Thymeleaf / Blazor). Exit 0 for both.
- **Verify:** `pytest -q tests -k no_llm` → 5 passed; crash fixture fails on 0.26.0, passes on 0.25.2; full `pytest -q` → 1244 passed, 3 skipped, 2 xfailed; golden unchanged; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** audit #86 domain classifier over/under-splits real apps (needs an M6 item); revisit tree-sitter ≥0.26 once upstream fixes the crash (the regression fixture will tell). Existing `.venv` must be re-installed (`uv pip install -e .`) to pick up the cap.

## 2026-10-08 — M1.2 — ProjectLayout single path resolver
- **Branch/commit:** rm/M1.2-project-layout @ 48cc2d2
- **Changes:** `core/paths.py` (new: `ProjectLayout.for_repo/legacy`, `repo_root`, `find_project`, `ensure`); `pipeline.py`, `cli.py` (`_project_output_dir` → `_layout`), `viewer/server.py`, `viewer/dashboard.py` resolve paths only via `ProjectLayout`; `tests/test_paths.py` (12: v1 layout incl. worktree + state-dir override, legacy parity + read fallback, viewer/chat readers agree with writer, impact regression, grep guard).
- **Decisions:** current commands keep the legacy layout (no user-visible change, golden unchanged); `.discovery/` becomes the scan default with the v1 CLI (M2.3) — acceptance note added. Viewer and chat path bugs from M0.9 fixed here as a side effect; M0.9 trimmed accordingly.
- **Verify:** `pytest -q tests -k paths` → 12 passed; `rg -n '"output-' src/ai_discovery | grep -v core/paths.py` → empty; ruff F821 on src → only the known `ingest-docs` NameError (M0.9); real run on spring-petclinic: scan → export-graph (248 nodes, 1278 edges, 7 FSMs) → impact OK; full `pytest -q` → 1256 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** `impact` needs the entity table name (`owners`), not the class (`Owner`) — UX item for M5.2. Add a ruff F821 check to CI (no linter configured yet).

## 2026-10-08 — M1.3 — config v2 (layered + validated)
- **Branch/commit:** rm/M1.3-config-v2 @ 54dba00
- **Changes:** `config.py` → `config/__init__.py` (env overrides factored into `_apply_env_overrides`); `config/loader.py` (`load_config`, `ConfigError`, `config_sources`, user/project discovery); `config/template.py` (single template, moved out of `cli.py init`); `cli.py` `_load_config` (exit 2 on bad config), `--prod` no longer resets YAML; `discovery.yaml` + `discovery.yaml.example` regenerated; `pyproject.toml` + `pydantic>=2.6`; `tests/conftest.py` (autouse isolation); `tests/test_config_v2.py` (20).
- **Decisions:** pydantic validates the merged dict against the *existing* dataclasses (`__pydantic_config__ = extra=forbid` + `TypeAdapter`) — strictness without rewriting the ~130 consumers. Model-role rename (fast/standard/deep, quality draft|final) moved to M1.10 (it rewrites the LLM layer). SecretStr not used: persistence is already covered by M0.8 redaction.
- **Behaviour changes:** a `discovery.yaml` in the working directory is now auto-loaded (README promised "no config needed" but the file was ignored without `-c`); configs with unknown/removed keys now fail fast instead of being silently ignored.
- **Verify:** `pytest -q tests -k config` → 52 passed; full `pytest -q` → 1277 passed, 3 skipped, 2 xfailed; golden unchanged; ruff F821/F401 on config/ + cli.py clean except known ingest-docs NameError (M0.9); `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** `.env.sample` still lists unread variables (M0.1). `doctor`/`status` (M2.5) should print `config_sources()`.
