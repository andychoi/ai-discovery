# ai-discovery Audit — Bugs and Gaps (2026-10-08)

- **Status:** Accepted (baseline for M0; findings come from reading the code and were **not reproduced by running it** unless noted)
- **Last updated:** 2026-10-08
- **Related items:** M0.*, M1.*, M2.*, M6.*

**Severity scale:**
- **S1:** wrong results, crash or data leak.
- **S2:** silent degradation or cost.
- **S3:** UX or consistency.

File paths are relative to `src/ai_discovery/` unless they start with a top-level directory.

## 1. Correctness and crashes

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 1 | `ingest-docs` always raises `NameError`: `_VALID_PUSH_MODES` is never defined (only `_VALID_INGEST_TARGETS`) | `cli.py:756`, `cli.py:20` | S1 | M0.9 (then removed in M2.6) |
| 2 | Viewer docs and diagrams read the legacy `<out>/<slug>/` layout. The scan writes `output-<slug>`. DB discovery handles both layouts | `viewer/server.py:67-68`, `viewer/dashboard.py:109,253` | S1 | M0.9 |
| 3 | `chat` indexes `<out>/output-<slug>/docs`, which nothing writes. It skips silently, so the chat only sees code chunks | `cli.py:307`, `rag/chat.py:86` | S1 | M0.9 |
| 4 | `verify-drift` default dir `./data/specs` is wrong. When not found it **exits 0**, so a CI gate passes | `cli.py:945-948` | S1 | M0.9 |
| 5 | Strict UTF-8 decode: any CP949/EUC-KR byte drops the whole file | `parsers/*` `.decode()`; `pipeline.py:599` `_parse_one` | S1 | M0.4 |
| 6 | C# and Java tree-sitter queries capture `class_declaration` only (no interface/record/struct/enum) | `parsers/csharp.py:16-18`, `parsers/java.py:26-28` | S1 | M6.2, M6.3 |
| 7 | Java `_unwrap_type` used for receiver typing turns `List<Item>` into `Item`, giving false `Item.remove` edges at 0.93. It is correct for JPA targets (`:617`) | `parsers/java.py:523,556,573` | S1 | M6.3 |
| 8 | C# namespace query misses file-scoped `namespace X;` and single-segment namespaces | `parsers/csharp.py` | S2 | M6.2 |
| 9 | Ambiguous calls fan out to ≤8 candidates; unresolved calls become edges to bare names at 0.5 | `graph/call_graph.py:15,196` | S1 | M1.12 |
| 10 | Short-name stage has no language or builtin guard (JS `save()` binds to Java `save()`) | `graph/call_graph.py` (stage 4) | S1 | M1.12 |
| 11 | C#/Java `using`/`import` bind only the first segment, so the import stage rarely fires | `graph/call_graph.py:300` | S2 | M6.2, M6.3 |
| 12 | Entry-point detection uses substring hints (`cmd`, `handler`) that over-trigger | `graph/call_graph.py:558` | S2 | M6.12 |
| 13 | Flow BFS (depth 5) enqueues unresolved and fanned-out edges; no stop reason | `graph/call_graph.py` (`ExecutionSliceBuilder`) | S2 | M1.12 |
| 14 | `.sql` skip check runs on the absolute path, so a repo under e.g. `/build/` loses all SQL | `extractors/sql_extractor.py` | S2 | M6.8 |
| 15 | Self-review 300 s timeout never fires: `result(timeout=)` is called on futures already yielded by `as_completed` | `pipeline.py:1535-1541` | S2 | M0.6 |
| 16 | `--skip-phases` has no effect on phases 14–19 (no `_phase_should_run` check) | `pipeline.py` (phases 14–19) | S3 | M0.6 |
| 17 | Phase 13 resume re-calls the LLM (`infer_flow`, 3 tier-2 calls per scenario) and swallows errors with `except Exception: pass` | `pipeline.py:1308-1316` | S2 | M0.6 |
| 18 | Resume and "already scanned" compare the raw REPO argument with the stored absolute path. `./repo` vs an absolute path triggers a full rescan | `pipeline.py:273-283,359` vs `repo/resolver.py:198` | S2 | M0.6 |
| 19 | `--resume` falls back to the latest same-branch run with no HEAD SHA check, so it can mix commits | `pipeline.py:262-290,361-380` | S2 | M0.6 |
| 20 | Uncommitted changes are ignored: same HEAD means "already scanned" | `pipeline.py:401-436`, `repo/resolver.py:200-205` | S3 | M2.3 |
| 21 | Re-scanning a remote clone after a force-push fails: `git pull` with `check=True`; no git timeouts | `repo/resolver.py:108-160` | S2 | M0.6 |
| 22 | Self-review per-doc timeout ineffective, so one stuck review stalls the phase | same as #15 | S2 | M0.6 |

## 2. Cost and LLM

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 23 | Budget enforced **only for Bedrock**; OpenAI, Gemini and Anthropic are unlimited | `pipeline.py:229-259` | S1 | M0.5 |
| 24 | Budget stop exits 0 | `pipeline.py` / `cli.py` scan | S2 | M0.5 |
| 25 | Failed Tier-1 summary placeholder ("Summarization failed") is persisted, treated as done on resume, and reused on the next scan | `ai/summarizer.py:143-155,368-392,453,531-586`; `pipeline.py:1226` | S1 | M0.7 |
| 26 | `truncated` (≥95% of `max_tokens`) is computed but dropped | `shared/llm_router.py:460`, `ai/llm_client.py:137-157` | S2 | M0.7 |
| 27 | Tier-1 reuse key lacks model and prompt version | `ai/summarizer.py:566-572` | S2 | M0.7 |
| 28 | Tier-2 flows and Tier-3 rollups are never reused across scans | `ai/rollup.py:631-660`, `ai/flow_analyzer.py:547-584` | S2 | M3.7 |
| 29 | Oversized non-class functions are sent to the LLM whole | `ai/chunker.py:94-127` | S3 | M3.7 |
| 30 | Retry is substring-based, 3 tries, 0.5 s base; Retry-After ignored | `shared/llm_invoke.py:26-40,363` | S3 | M1.11 |
| 31 | No pre-run cost estimate; no prompt caching; no response cache | — | S2 | M1.11, M2.4 |
| 32 | `shared/llm_router.configure()` sets a **module-global** config, so a server can't safely serve two projects | `shared/llm_router.py:289-297` | S3 | M1.10 |
| 33 | Three LLM layers (~1,985 LOC): `ai/llm_client.py` → `shared/llm_router.py` → `shared/llm_invoke.py`; two tier-naming schemes (`tier1/2/3d/3p` vs `fast/standard/expert/heavy/deep` in `shared/model_defaults.py`) | — | S3 | M1.10 |

## 3. Security

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 34 | API keys (openai/gemini/anthropic/mlx `api_key`, filled from env) are serialized via `json.dumps(asdict(config))` into `scan_runs.config_json` | `pipeline.py:452-462`, `config.py:203-209,340-356` | S1 | M0.8 |
| 35 | Clone token is injected into the clone URL and persists as the remote URL in `.git/config`; it may echo in git errors (Low confidence) | `repo/resolver.py:28-42,163-177` | S1 | M0.8 |
| 36 | Raw provider error text persisted (may quote keys) | `db.py` (~671) | S2 | M0.8 |
| 37 | Viewer has no auth and no warning for `--host 0.0.0.0`; traversal guard is untested | `viewer/server.py:97-120,212,353` | S3 | M4.1 |

## 4. File discovery

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 38 | Skips any directory named `packages`, `migrations`, `assets`, `bin`, `out`, `env`, `build`, `vendor` (JS monorepo `packages/*` lost) | `repo/lang_detector.py:7-20` | S1 | M6.1 |
| 39 | No `.gitignore` support, although test `test_file_walker_respects_gitignore` suggests otherwise | `repo/file_walker.py`; `tests/test_lang_detector.py:82` | S2 | M6.1 |
| 40 | No size cap; no binary/generated detection (C#/Java) | `repo/file_walker.py` | S2 | M6.1 |
| 41 | Follows symlinked dirs (`is_dir()`) with no cycle or depth guard | `repo/file_walker.py:58-69` | S2 | M6.1 |
| 42 | No submodule / nested-repo handling | — | S3 | M6.1 |

## 5. Config

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 43 | `discovery.yaml` is read **only when `-c` is passed**, although README Quick Start says "no config needed" | `config.py:259-388` | S2 | M1.3 |
| 44 | `prod:` key ignored; scan always overwrites it from `--prod` | `config.py` | S3 | M1.3 |
| 45 | Obsolete `process_mining` keys (pm4py removed) warn on every load | `discovery.yaml` | S3 | M0.1, M1.3 |
| 46 | Three templates disagree (root `discovery.yaml`, `discovery.yaml.example`, `init` output) | root, `discovery.yaml.example`, `cli.py init` | S3 | M1.3 |
| 47 | `.env.sample` lists 7 variables nothing reads; `DISCOVERY_LLM_PROVIDER=bedrock` silently overrides the YAML provider (Unverified in this session's re-check) | `.env.sample` | S3 | M1.3 |
| 48 | `DISCOVERY_TIER3*` env overrides affect Bedrock only | `config.py` | S3 | M1.10 |

## 6. CLI / UX

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 49 | 13 commands, 69 options; 7 commands require `-p`; no "current project" | `cli.py` (967 lines) | S3 | M2.* |
| 50 | Inconsistent flags: `-s` = `--slugs`/`--spec-dir`; `-o` has 3 meanings; `--out` vs `-f/--output-file`; `--tier2` vs `--tier`; `--target` vs `--push` | `cli.py` | S3 | M2.1 |
| 51 | `--provider` help lists 2–4 of 7 providers | `cli.py` | S3 | M2.1 |
| 52 | No `--version`, `status`, `doctor`, `--verbose`; no logging setup, so `logger.info` never appears | `cli.py`, whole package | S3 | M2.1, M2.5 |
| 53 | Overlapping commands: five ways to ask "what touches X" (`impact`, `query`, `view`, `chat`, `export-graph`); `ingest` vs `ingest-docs` | `cli.py` | S3 | M2.6, M2.7, M5.1 |
| 54 | Scan help text describes the docs path wrongly (`{docs_root}/{project_slug}/{PREFIX}`) | `cli.py` | S3 | M2.3 |
| 55 | No CLI tests (no `CliRunner` usage) | `tests/` | S2 | M0.3 |

## 7. Pipeline and storage structure

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 56 | `run_pipeline` is ~1,470 lines passing ~15 locals between phases; resume is handled by ad-hoc `else:` branches | `pipeline.py:293-1760` | S3 | M1.6–M1.8 |
| 57 | Two numbering schemes collide (pipeline phases vs backbone spec "Phase 1–4" inside phase 8) | `pipeline.py`; `docs/INDEX.md:56` | S3 | M1.6 |
| 58 | Sub-phases 2e-1…4 have no checkpoints; phase 2 (LLM) runs before parse | `pipeline.py:~491, ~941-1088` | S3 | M1.6, M1.7 |
| 59 | Schema migrations are forward-only if-blocks (`SCHEMA_VERSION=14`) with no upgrade-path tests | `db.py:20,430-530` | S3 | M1.4 |
| 60 | No run lock: two scans on the same slug both insert into `scan_runs` | — | S2 | M1.4 |
| 61 | Viewer histogram, quality and weakest-docs queries don't filter by scan, so counts accumulate | `viewer/dashboard.py` | S3 | M4.1 |
| 62 | `pysqlite3` is a hard dependency (native build); the Dockerfile strips it; Windows/air-gapped installs may fail (Unverified) | `pyproject.toml`, `rag/embedder.py:16-18` | S2 | M2.5, M4.5 |

## 8. Outputs vs documentation

| # | Finding | Location | Sev | Item |
|---|---|---|---|---|
| 63 | IMPACT, `database/` and `batch-jobs/` writers don't exist, yet README/CLAUDE.md promise them; `discover impact` prints to stdout only | `generators/doc_generator.py:52`; README:166-171,569-572,772-778; CLAUDE.md:94-96 | S3 | M0.1 (docs); writers not built — SDLC tree dropped |
| 64 | DMN/EARS/BPMN are single files in the intermediate dir, not per entity; no `.bpmn`/`.dmn` files, so bpmn-js/dmn-js routes render nothing | `generators/{dmn,ears,bpmn}_generator.py` | S3 | M3.3 (per-entity on wiki pages) |
| 65 | Unused templates: `data-model.md.j2`, `interface.md.j2`, `spec.md.j2` | `templates/` | S3 | M3.7 (deleted) |
| 66 | "Legacy mirror" duplicate files at the root of the intermediate dir | `pipeline.py` | S3 | M3.7 (deleted) |
| 67 | Viewer loads markdown/mermaid/bpmn-js/dmn-js from jsdelivr CDN, so it breaks offline | `viewer/templates/` | S3 | M4.2 |

## 9. Repo hygiene

| # | Finding | Location | Item |
|---|---|---|---|
| 68 | Stale files: `PM4PY_QUICKSTART.txt`, `SESSION_SUMMARY.md`, `DEMO_QUICKSTART.txt`, `demo.sh` (broken `--quick`, wrong module path), `run-local.sh` | repo root | M0.1 |
| 69 | `ai_discovery.egg-info/` committed despite `.gitignore` | repo root | M0.1 |
| 70 | `shared/README.md` describes modules from another project | `shared/README.md` | M0.1 |
| 71 | `docs/integrations/PM4PY_*` (3 files) not archived; pm4py mentioned in 19 files | `docs/` | M0.1 |
| 72 | CLAUDE.md/README advertise 5 skills that don't exist in the repo; triage step filters a non-existent `criticality` column | CLAUDE.md, README | M0.1, M5.4 |
| 73 | README documents `DISCOVERY_BUDGET_LIMIT_USD`, which nothing reads | README | M0.1 |
| 74 | Two overlapping profiling guides | `docs/guides/pipeline/profiling.md`, `docs/guides/PERFORMANCE_PROFILING.md` | M0.1 |
| 75 | `scikit-learn` used only by the broken `ingest-docs` dedup; `networkx` only by semantic batching | `pyproject.toml` | M2.6 |
| 76 | **License metadata mismatch:** `pyproject.toml:19` says MIT, `README.md:868` says AGPL-3.0, and there is no LICENSE file. **Owner decision; out of scope for agents** | — | open item (ROADMAP) |

## 10. Tests and benchmark harness

| # | Finding | Location | Item |
|---|---|---|---|
| 77 | Corpus baseline saturated (mostly 1.0), so it can't discriminate | `tests/corpus/baseline.json` | M6.13 |
| 78 | Call edges scored on recall only; no precision sample | `tests/corpus/metrics.py:75-83` | M0.11 |
| 79 | Spec says corpus Phase 3 is "pending", but `tests/corpus/external/` (manifest, fetch, ground_truth) exists | `docs/specs/2026-05-31-corpus-accuracy-harness.md` | M0.11 |
| 80 | Viewer tests build the old layout, so they pass while the product breaks | `tests/test_viewer_dashboard.py:29,90` | M0.9 |
| 81 | Domain classifier can name a domain after a segment of the repo's *absolute* path (e.g. `private` from macOS `/private/var/...`), so domain names and doc filenames depend on checkout location. Found by the M0.2 golden harness (WebForms node in `dotnet-webforms` fixture) | `graph/domain_classifier.py` (Unverified exact line) | M6.1 (path handling) — golden normalizes it meanwhile |
| 82 | `screens` table is defined but never written; screen identity only lands in `screen_specs`/`screen_mappings` | `db.py:170` (schema), no `INSERT INTO screens` in `src/` | M3.3 |
