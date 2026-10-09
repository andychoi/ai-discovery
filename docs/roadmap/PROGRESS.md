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
| migration | 0001 | M1.4 (baseline) | 2026-10-09 |
| migration | 0002 | M1.5 (v1 tables) | 2026-10-09 |

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

## 2026-10-09 — housekeeping — owner's provider work committed
- **Commit:** 5d42a72 `feat(providers): add aigw, omlx and openrouter providers` (owner's uncommitted work, reviewed for secrets — none — and committed at owner request before M1.4). `omlx` is in `_LOCAL_PROVIDERS` ($0); `aigw`/`openrouter` are metered at the conservative cloud rate.

## 2026-10-09 — M1.4 — numbered migrations + run lock
- **Branch/commit:** rm/M1.4-store-migrations-lock @ 57f5828
- **Changes:** `store/{__init__,migrate}.py`, `store/migrations/m0001_baseline.py` (new); `db.py` (`init_db` runs `store.migrate`; `SCHEMA_VERSION` frozen at 15); `core/lock.py` (new `RunLock`, `LockHeld`); `pipeline.py` (`run_pipeline` = lock wrapper, body renamed `_run_pipeline_unlocked`, unchanged); `cli.py` scan → exit 4 on `LockHeld`; `tests/test_store_and_lock.py` (15); `tests/test_screen_phase_regression.py` now inspects `_run_pipeline_unlocked`.
- **Decisions:** adopt existing DBs at 0001 instead of a hard break — baseline equals legacy v15, so one migration system covers old and new DBs. Migration modules are named `m<NNNN>_*.py` (importable). Lock staleness uses `ps -o lstart=` to detect pid reuse (POSIX); on platforms without `ps`, a live pid is always treated as the owner.
- **Migration numbers:** 0001 used; 0002 reserved for M1.5.
- **Verify:** `pytest -q tests -k "store or lock"` → 30 passed; real concurrent run on eShop: second scan exit 4 (`lock_held`), first exit 0, lock removed, DB at `0001 baseline`; full `pytest -q` → 1318 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** `tests/golden/snapshots/*.json` gain `"schema_migrations": 1` in `table_counts` — the new bookkeeping table; no other diff (checked with `git diff`).
- **Follow-ups:** `doctor --repair` (M2.5) should offer to clear a foreign-host lock; pre-existing unused imports in `pipeline.py` (ruff F401) left for the stage extraction (M1.6/M1.7).

## 2026-10-09 — M2.1 — v1 CLI skeleton
- **Branch/commit:** rm/M2.1-cli-skeleton @ f88f67a
- **Changes:** `cli.py` → `cli/legacy.py` (verbatim); `cli/__init__.py` (root callback + global flags, `main()` with flag hoisting, re-exports legacy names), `cli/context.py` (`Globals`, `get_globals`, `hoist_global_flags`), `cli/output.py` (`emit`, `fail`, `handle_errors`, `ProgressEmitter`), `cli/commands/__init__.py` (v1 registry, empty until M2.2+); `core/errors.py`, `core/logging.py` (new); `pyproject.toml` entry point → `ai_discovery.cli:main`; `tests/cli/test_cli_skeleton.py` (31).
- **Decisions:** legacy commands stay registered on the same app until their v1 replacements land (no name clashes; hard break completes in M2.7). Global flags are declared once on the root callback and hoisted from anywhere in argv by `main()` (tests calling `app` directly put them first). NDJSON progress goes to stdout, or stderr when `--json` owns stdout.
- **Verify:** `pytest -q tests/cli` → 53 passed, 2 xfailed; `discover --help` 0.36 s and imports no tree-sitter/boto3/sqlite-vec/pipeline; real `discover scan … --no-llm -q --no-color --progress none` (flags after the command) → exit 0; unknown flag → exit 2; full `pytest -q` → 1346 passed, 3 skipped, 2 xfailed; ruff F821/F401 clean on new modules; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** `.discovery/logs/<run>.log` file logging is wired in `configure_logging(log_file=)` but not yet enabled per run (needs a run id → M2.3 scan v1).

## 2026-10-09 — M2.5 — status + doctor --repair
- **Branch/commit:** rm/M2.5-status-doctor @ 1188374
- **Changes:** `cli/commands/{status,doctor,_index}.py` (new), registered in `cli/commands/__init__.py`; `core/lock.py` `lock_status`/`remove_lock`; `core/paths.py` `legacy_db_candidates`; `tests/cli/test_status_doctor.py` (13); `tests/cli/test_cli_smoke.py` command-set guard includes v1 commands.
- **Decisions:** until scan v1 (M2.3) writes `.discovery/`, status/doctor fall back to a legacy `data/discovery-output/output-<slug>/` DB (under the repo or cwd) whose latest `scan_runs.repo_path` is this repo. Doctor exit 1 when any check fails. Foreign-host locks are removed only with `--repair --yes` (closes the M1.4 follow-up). FTS rebuild repair is added when FTS tables exist (M1.5/M3.5).
- **Verify:** `pytest -q tests/cli -k "status or doctor"` → 13 passed; real run on spring-petclinic: status (248 nodes, 1278 edges, up to date) and doctor (9 checks, 1 legacy-layout warning) both exit 0 — the scan's `data/` dir written into the sample app was removed afterwards; `--help` still imports nothing heavy; full `pytest -q` → 1359 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** M2.7 removes `test-llm` (superseded by `doctor --llm`). Status gains stale-page and degradation counts with M1.8/M3.5.

## 2026-10-09 — M1.5 — current_scan + v1 tables
- **Branch/commit:** rm/M1.5-current-scan-tables @ fca86f3
- **Changes:** `store/migrations/m0002_v1_tables.py`, `store/{scans,fts,vectors}.py` (new); `pipeline.py` `_finalise_scan` flips `current_scan` + GC on `completed`; `config` new `store.keep_snapshots` (template + checked-in YAMLs regenerated); `cli/commands/status.py` reads `current_scan`; `rag/embedder.py` (cross-lane) embedder identity pin + reindex on change; `tests/test_store_v1_tables.py` (11); `tests/golden/test_golden.py` skips `pages_fts*`.
- **Decisions:** `pages_fts` is created only when FTS5 exists (migration never fails for it; `store.fts.create_fts` adds it later). GC counts only finished scans toward `keep` (running + current always kept). Embedder change → automatic full re-embed (never a silent mix). `evidence_rows` and `scan_runs` extra columns deferred to M8 / M1.8 as designed.
- **Bug avoided:** pin read/write must use the embedder's `pysqlite3` connection — mixing it with a stdlib `sqlite3` connection on the same WAL DB gave `disk I/O error` (audit #87). `pinned_embedder`/`get_current_scan` now only treat "no such table" as absent and re-raise other errors.
- **Migration numbers:** 0002 used.
- **Verify:** `pytest -q tests -k store` → all pass (11 new); real eShop scan: migrations 0001+0002, `current_scan` = completed run; full `pytest -q` → 1370 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** `table_counts` gain the new (empty) v1 tables, `current_scan: 1`, `schema_migrations: 2`; `pages_fts*` excluded (FTS5 shadow-table counts depend on the SQLite build).
- **Follow-ups:** `doctor --repair` FTS rebuild when pages exist (M3.5); audit #87 single connection factory.

## 2026-10-09 — M2.2 — init + first-run menu
- **Branch/commit:** rm/M2.2-init-menu @ e9228ea
- **Changes:** `cli/commands/init.py` (new; `detect_stack`, menu, `write_mcp_entry`); legacy `init` removed from `cli/legacy.py`; `config` new `prose` setting (schema + template; checked-in YAMLs regenerated); `tests/cli/test_init.py` (15); smoke-test command set updated.
- **Decisions:** `--mcp` writes `.mcp.json` only when a `discover mcp` command is registered (M5.3) — writing an entry for a server that doesn't exist would give Claude Code a broken MCP server; the merge writer is implemented and tested now. Prompts use typer's public API only: typer 0.27 vendors click (`typer._click`), so `import click` fails.
- **Verify:** `pytest -q tests/cli -k init` → 15 passed; real run on eShop with `--state-dir` (sample app untouched): detection printed, config written, exit 0; same without a TTY and without a choice → exit 2 `init_needs_choice`; full `pytest -q` → 1383 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** audit #88 framework detector reads only the first .csproj (eShop shows no framework). Advanced menu will offer excludes/encodings once those config keys exist (M6.1).

## 2026-10-09 — M2.3 — scan v1
- **Branch/commit:** rm/M2.3-scan-v1 @ 131d914
- **Changes:** `cli/commands/scan.py`, `core/hooks.py` (new); legacy `scan` removed from `cli/legacy.py`; `pipeline.run_pipeline(..., layout=)`; `cli/output.ProgressEmitter` silent under `-q`; `tests/cli/test_scan_v1.py` (20); legacy-syntax CLI tests migrated (test_budget, test_config_v2, test_no_llm, test_resume, test_store_and_lock, test_cli_smoke).
- **Decisions:** `--only` maps to `_SKIPPABLE_PHASES` until the stage engine (M1.8). "What's next" only suggests registered commands (no `serve`/`mcp` until M4.5/M5.3). `--source URL` and `--dry-run` deferred (M2.4 / follow-up) — not in this item's acceptance. `-c/--config` and `--provider` are gone from scan (config comes from `.discovery/config.yaml`; set via `discover init`).
- **Verify:** `pytest -q tests/cli -k scan` → 20 passed; real keyless scans with `--state-dir` (sample apps untouched): spring-petclinic 43 files / 248 symbols / 1278 edges, eShop 378 / 1261 / 3297, both exit 0; full `pytest -q` → 1397 passed, 3 skipped, 2 xfailed; ruff clean on new modules; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Measured (baseline for M1.12/M6):** unresolved call edges — petclinic 1014/1278 (79%), eShop 2155/3297 (65%).
- **Follow-ups:** README/CLAUDE.md still show `discover scan repo -p …` (M2.7 regenerates the CLI reference); `--source URL` (remote clone into state dir).

## 2026-10-09 — M3.1 — page model, registry, notes
- **Branch/commit:** rm/M3.1-page-model @ f005eda
- **Changes:** `pages/{__init__,model,registry,notes}.py`, `pages/builders/__init__.py` (new, empty package); `tests/test_pages_model.py` (18).
- **Decisions:** `facts_json` stores `{facts, sections}`; `body_md` is rendered from sections (Jinja templates come with M3.2). Links/citations are de-duplicated and sorted, and JSON is canonical (sorted keys), so identical inputs give identical rows — the basis for the M3.5 idempotence test. Renames are `page_tombstones` rows with a successor; `notes_for` gathers notes from every id that resolves to the page. Builders whose module name starts with `_` are not imported.
- **Verify:** `pytest -q tests -k pages_model` → 18 passed; full `pytest -q` → 1415 passed, 3 skipped, 2 xfailed; ruff clean on `pages/`; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** none.

## 2026-10-09 — M3.2 — code page builders
- **Branch/commit:** rm/M3.2-code-builders @ 538bead
- **Changes:** `pages/builders/code.py` (new: `Snapshot` loader cached per build, four builders); `tests/test_builders_code.py` (9).
- **Decisions:** sections are built in Python rather than Jinja templates (`pages/templates/` not created) — simpler, deterministic, and the page body is already composed from `Section`s; templates can come with the SPA if a format needs them. Entry points = `endpoint`/`batch_job` nodes, `main` methods and `domains.entry_points`. Duplicate qualified names get `<qn>@<path>:<line>` keys. The overview shows the 30 largest domains (eShop has 319, audit #86).
- **Verify:** `pytest -q tests -k builders_code` → 9 passed (all citations resolve on disk; byte-identical rebuilds; caps; link consistency); built for real scans: spring-petclinic 111 pages / 275 citations in 0.04 s, eShop 601 pages / 1273 citations in 0.21 s; full `pytest -q` → 1424 passed, 3 skipped, 2 xfailed; ruff clean; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** pages are not yet persisted by scans — the post-scan page-build hook is M3.5.

## 2026-10-09 — M3.3 — business page builders
- **Branch/commit:** rm/M3.3-business-builders @ d16f258
- **Changes:** `pages/builders/business.py` (new: screens, endpoints, entities, tables, interfaces); `tests/test_builders_business.py` (8, incl. a synthetic FSM with guarded transitions and an external system).
- **Decisions:** screen data comes from `screen_specs.spec_json` (the `screens`/`screen_mappings` tables are never written — audit #82). Entity pages reuse `generators/dmn_generator.py` and `ears_generator.py` per entity (cross-condition correlations from `entity_conditions.json` are not included yet) and render their own `stateDiagram-v2` with sanitized state ids. Table set = db_model tables ∪ FK endpoints ∪ screen tables ∪ SQL-table FSMs.
- **Verify:** `pytest -q tests -k builders_business` → 8 passed (all links resolve to built pages; citations on disk; deterministic); real builds: fixture 63 pages, spring-petclinic 144 (7 entities, 2 interfaces) in 0.04 s, eShop 640 in 0.2 s; full `pytest -q` → 1432 passed, 3 skipped, 2 xfailed; ruff clean; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** audit #89 coarse screen→table attribution (every fixture screen reads all tables); include `entity_conditions` in DMN context; M3.5 persists pages after each scan.

## 2026-10-09 — M3.5 — page build hook + FTS + idempotence
- **Branch/commit:** rm/M3.5-page-build @ a07771f
- **Changes:** `pages/build.py` (new: `build_pages`, `nearest_parent`, `stale_pages`, `pages` hook); `core/hooks.py` `HOOK_MODULES` + `is_registered`; `cli/commands/status.py` (cross-lane) page + stale counts; `tests/test_page_build.py` (6); `tests/cli/test_scan_v1.py` hook assertion includes the built-in `pages` hook.
- **Decisions:** structural pages are rebuilt every scan (ADR-0002), so freshness is computed on read from `source_hashes` (sha256 of every cited file, stamped at build time) instead of being written back. A page that reappears drops its tombstone. Tombstone successor = nearest surviving ancestor (symbol → its file → modules → root → overview).
- **Verify:** `pytest -q tests -k "page_build or idempotence"` → 6 passed (byte-identical rows across two `--full` scans; tombstone + note follow-through; staleness file → modules → overview); real scans: spring-petclinic 144 pages / 144 FTS rows, eShop 640 / 640, sample apps untouched; full `pytest -q` → 1438 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** none (golden uses the legacy run_pipeline path, which doesn't run post-scan hooks).
- **Follow-ups:** `doctor --repair` FTS rebuild now has data to rebuild (add in a doctor touch-up); M3.6 parity tests can build on these fixtures.

## 2026-10-09 — M4.1 — FastAPI server + API v1
- **Branch/commit:** rm/M4.1-api-v1 @ 8f5c365
- **Changes:** `server/{__init__,app,api_v1}.py` (new); `pyproject.toml` + `fastapi>=0.110`, `uvicorn>=0.29`; `tests/test_server_api.py` (18).
- **Decisions:** `impact` endpoint waits for M5.2 as specified. `/status` stages come from `phase_checkpoints` until the stage runner (M1.8) fills `stage_runs`. `/tables` `procs` is `[]` until M6.10. Search wraps every user token in quotes with a prefix `*`, so FTS operators in input can't inject query syntax; falls back to LIKE when FTS5 is missing (`meta.degraded: fts_unavailable`). `meta` is cached 5 s per scan (git + staleness are not free). Starlette's TestClient peer `testclient` counts as loopback.
- **Verify:** `pytest -q tests -k server` → 18 passed (envelope on every endpoint; paging/totals; tombstone successor; FTS injection-safe; graph caps; screens menu; notes; remote refused (403); token-guarded writes (401/200); bind check; static path traversal blocked); smoke run of every endpoint on the spring-petclinic v1 index; `discover --help` still imports neither fastapi nor tree-sitter; full `pytest -q` → 1454 passed, 3 skipped, 2 xfailed; ruff clean; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** `discover serve` (M4.5) wires `check_bind` + uvicorn; SPA build (M4.2); `/impact` (M5.2).

## 2026-10-09 — M4.2 — web/ scaffold + wheel build
- **Branch/commit:** rm/M4.2-web-scaffold @ 7cc69a4
- **Changes:** `web/` (package.json with exact versions + package-lock.json, tsconfig, vite.config.ts, index.html, src/{main,App,api,vendors,vendor-types.d,styles,api.test}); `scripts/build_web.py` (new); `.gitignore` (static output, node_modules, web/dist); `pyproject.toml` package-data `server/static/**/*`; `tests/test_build_web.py` (11).
- **Decisions:** renderer libraries are dynamic imports referenced from the entry (`window.__discoveryRenderers`) so they are emitted as local chunks but loaded only when a view needs them. The offline check flags *loads* (src/href, CSS url()/@import, JS import/from) of non-local hosts, not plain URL strings (XML namespaces and doc links inside libraries). Versions are 2024 releases (all verified on the registry). `python -m build` isn't installed in the dev venv; the wheel was built with `uv build --wheel`.
- **Verify:** `python scripts/build_web.py` → vitest 2 passed, tsc clean, vite build ok, "80 files, 3.6 MiB, no external loads"; `uv build --wheel` → wheel contains `ai_discovery/server/static/index.html` + 79 assets (fonts, renderer chunks); `pytest -q tests/test_build_web.py` → 11 passed (incl. serving the real build); full `pytest -q` → 1465 passed, 3 skipped, 2 xfailed; `roadmap_lint` ok.
- **Bench/golden changes:** none.
- **Follow-ups:** CI step to run `scripts/build_web.py` before building the wheel (no CI config in repo yet); API type generation (`scripts/gen_api_types.py`, design §5) when the SPA grows (M4.3).
