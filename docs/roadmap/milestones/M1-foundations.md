# M1 — Foundations (strangler)

**Goal:** one path resolver, one config resolver, a migratable store with a run lock and page tables, the pipeline split into named stages *without behaviour change*, one LLM package, and a closed edge contract.
**Exit criteria:** `run_pipeline` is a stage list; no path built outside `core/paths.py`; no `shared.llm_*` imports; edge contract measured against M0.12 baseline.
**Refs:** `docs/roadmap/adr/0001-project-layout.md`, `0002-snapshot-and-incremental.md`, `0003-stage-contract-strangler.md`, `0004-edge-origin-vocabulary.md`, `docs/roadmap/design/architecture-v1.md`

> Rule for M1.6–M1.8: **move code verbatim first, change behaviour in a later PR.** Golden snapshot (M0.2) must be unchanged by M1.6/M1.7.

### M1.2 ProjectLayout (`core/paths.py`) ★
- **Status:** Done (48cc2d2)
- **Size:** M
- **Lane:** A
- **Depends on:** —
- **Owns:** `src/ai_discovery/core/paths.py` (new), every path-building call site (`cli.py`, `pipeline.py`, `viewer/`, `rag/`, `drift_checker.py`, `generators/`)
- **Acceptance:**
  - `ProjectLayout.for_repo(path, state_dir=None)` → `<repo>/.discovery/{discovery.db,cache/,logs/,exports/,lock,config.yaml,.gitignore}`; `--state-dir` for read-only/remote sources; repo root via `git rev-parse --show-toplevel` (worktree-safe) with non-git fallback.
  - `.discovery/.gitignore` = `*` + `!config.yaml` + `!.gitignore`.
  - `rg "output-" src/` returns hits only in `core/paths.py` (legacy-layout reader kept there for `doctor`).
  - Current CLI/pipeline use `ProjectLayout.legacy` (no behaviour change, golden unchanged); `.discovery/` becomes the scan default with the v1 CLI (M2.3).
- **Verify:** `pytest -q tests -k paths`; `rg -n '"output-' src/ai_discovery | grep -v core/paths.py` → empty
- **Out of scope:** CLI flags (M2.1).

### M1.3 Config v2 ★
- **Status:** Done (54dba00)
- **Size:** M
- **Lane:** A
- **Depends on:** —
- **Owns:** `src/ai_discovery/config/` (new), `src/ai_discovery/config.py` (shim), `discovery.yaml`, `src/ai_discovery/discovery.yaml.example`
- **Acceptance:**
  - Pydantic v2 schema; precedence CLI > env > project `.discovery/config.yaml` > user `~/.config/ai-discovery/config.yaml` > defaults — tested.
  - Auto-load without `-c`; unknown key → error with "did you mean"; one template (generated from schema).
  - `prod:` honoured from YAML (no longer overwritten by the CLI default); env vars documented and all read. (Model roles moved to M1.10, which owns the tier→role rename across the LLM layer.)
  - Secrets never serialized by `model_dump()` (SecretStr).
- **Verify:** `pytest -q tests -k config`
- **Out of scope:** provider internals (M1.10).

### M1.4 Store: numbered migrations + run lock ★
- **Status:** Done (57f5828)
- **Size:** M
- **Lane:** A
- **Depends on:** M1.2
- **Owns:** `src/ai_discovery/store/` (new), `src/ai_discovery/store/migrations/NNNN_*.py` (new), `src/ai_discovery/core/lock.py` (new), `src/ai_discovery/db.py` (shim)
- **Acceptance:**
  - Every DB is at migration 0001 = baseline (legacy schema v15): new DBs are created by it, existing v15 DBs are adopted in place (no hard break needed). Legacy `SCHEMA_VERSION` is frozen; all later changes are numbered files.
  - Migrations are ordered files tracked in `schema_migrations`; empty DB migrates to head in a test; WAL + busy_timeout kept.
  - Single-flight run lock (exclusive create, pid + token; dead/recycled pid reclaimed; live owner never evicted on age); second writer exits 4 with reason `lock_held`.
- **Verify:** `pytest -q tests -k "store or lock"`
- **Out of scope:** new tables (M1.5).

### M1.5 Store: `current_scan` + new tables ★
- **Status:** Done (fca86f3)
- **Size:** M
- **Lane:** A
- **Depends on:** M1.4
- **Owns:** `src/ai_discovery/store/migrations/`, `src/ai_discovery/store/*.py`
- **Acceptance:**
  - `current_scan` pointer flipped atomically at scan end; readers (server/MCP) read only `current_scan`; GC keeps last N snapshots (config).
  - Tables (schema in `design/architecture-v1.md` §5): `pages`, `page_links`, `page_tombstones`, `pages_fts` (FTS5, rebuildable), `page_notes`, `file_parse_cache`, `llm_cache`, `degradations` (`sample_json`), `unresolved_calls`, `stage_runs`, `vec_meta`; embedder model+dim pinned in metadata (mismatch → reindex required, never silent mix).
  - `doctor` (later) can verify FTS5 + sqlite-vec availability; a test skips cleanly if FTS5 missing and reports it.
- **Verify:** `pytest -q tests -k store`
- **Out of scope:** writing pages (M3.5).

### M1.6 Stages — front half (verbatim move)
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M0.2, M0.6, M1.2
- **Owns:** `src/ai_discovery/stages/` modules per the ADR-0003 stage list — `context.py` + `resolve, walk, parse, graph, sql, domains, entities, slices` (new); `src/ai_discovery/pipeline.py`
- **Acceptance:**
  - `RunContext` dataclass carries what phases currently pass via locals; phases 5–8 and the unnumbered 2e–4 backbone block moved verbatim into stage functions, each with `rehydrate(ctx)` replacing ad-hoc `else:` reload branches.
  - Golden snapshot unchanged.
- **Verify:** `pytest -q tests/golden && pytest -q`
- **Out of scope:** behaviour changes, fingerprints.

### M1.7 Stages — back half (verbatim move)
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M1.6
- **Owns:** `src/ai_discovery/stages/` modules per the ADR-0003 stage list — `screens, embed, summaries, flows, rollup, visuals, review, render, finalise` (new); `src/ai_discovery/pipeline.py`
- **Acceptance:**
  - Phase 2 and phases 9–19 moved; `run_pipeline` ≤ 150 lines (a list of stage calls); numeric phase names kept only as aliases in checkpoint rows.
  - Golden snapshot unchanged.
- **Verify:** `pytest -q tests/golden && pytest -q`
- **Out of scope:** behaviour changes.

### M1.8 StageRunner + fingerprints
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M1.7, M1.5
- **Owns:** `src/ai_discovery/stages/runner.py` (new), `src/ai_discovery/pipeline.py`
- **Acceptance:**
  - Stage fingerprint = (stage version, config hash of consumed keys, input hash); matching fingerprint → skip; `--only <stage,...>` supported.
  - Degradations (skipped files, fallback decodes, failed LLM items, truncations) recorded in `degradations` and summarised at run end.
  - Ruff `BLE001` (blind except) clean in `stages/`.
- **Verify:** `pytest -q tests -k stage_runner`; `ruff check --select BLE001 src/ai_discovery/stages`
- **Out of scope:** per-file parse cache (M1.9).

### M1.9 Per-file parse cache
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M1.8
- **Owns:** `src/ai_discovery/stages/parse.py`, `src/ai_discovery/store/` (cache access)
- **Acceptance:**
  - Parse results cached with the key per ADR-0002 (content hash, parser version, encoding decision); unchanged files load from cache; graph fully re-resolved every scan (ADR-0002).
  - Test: scan → edit one file → scan; output equals a clean full scan of the edited tree.
- **Verify:** `pytest -q tests -k parse_cache`
- **Out of scope:** page-level parity (M3.6).

### M1.10 `llm/` package: registry, roles, capabilities
- **Status:** Planned
- **Size:** M
- **Lane:** B
- **Depends on:** M0.7
- **Owns:** `src/ai_discovery/llm/` (new), `src/ai_discovery/ai/llm_client.py`, `src/ai_discovery/shared/llm_router.py`, `src/ai_discovery/shared/llm_invoke.py`, `src/ai_discovery/shared/model_defaults.py` (shims)
- **Acceptance:**
  - One invoke path; provider registry; roles fast/standard/deep resolved from config v2; capability matrix (temperature constraints, structured output, max tokens field, timeout class).
  - Config: `models.fast|standard|deep` + `quality: draft|final` replace tier1/2/3d/3p and `--prod`/`prod:` (moved here from M1.3; cross-lane edit of `config/`).
  - No module-global config (`llm_router.configure()` removed); client object passed explicitly.
  - Old modules are thin shims re-exporting the new API.
- **Verify:** `pytest -q tests -k "llm or provider"`
- **Out of scope:** budget/cost/cache (M1.11).

### M1.11 `llm/` budget, cost, retry, cache, estimator
- **Status:** Planned
- **Size:** M
- **Lane:** B
- **Depends on:** M1.10, M1.5, M1.7
- **Owns:** `src/ai_discovery/llm/{budget,cost,cache,estimate,retry}.py` (new); shim removal across `src/ai_discovery/`
- **Acceptance:**
  - Cost accounting sums all usage fields incl. cache read/write tokens; per-model price table tested.
  - Retry: 429 honours Retry-After (capped), 5xx/network bounded retries, 4xx fail fast; per-item retry, never whole-batch.
  - Response cache in `llm_cache`, key per ADR-0002; repeat → cache hit, $0.
  - `estimate(stages)` returns a cost range used by M2.4; Anthropic prompt caching on stable system prefixes.
  - `rg "shared.llm_|shared import llm_" src/` → empty.
- **Verify:** `pytest -q tests -k "budget or cost or llm_cache"`
- **Out of scope:** cost gate UI (M2.4).

### M1.12 Edge contract
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M0.12
- **Owns:** `src/ai_discovery/graph/origins.py` (new), `src/ai_discovery/graph/call_graph.py`, `src/ai_discovery/graph/` slice/BFS code, migration file (number reserved in PROGRESS.md)
- **Acceptance:**
  - Closed origin enum (ADR-0004) with exactly one confidence per origin; `resolved_by` only takes enum values.
  - Ambiguity → no edge (no fan-out); unresolved calls → `unresolved_calls` rows (columns + reason enum per ADR-0004), never edges to bare names.
  - Short-name tier requires same language, non-builtin, callable target.
  - `evidence_kind` and `edge_kind` on edges, values per ADR-0004.
  - Execution slices/BFS follow only resolved static edges above a floor, with depth cap, visited set, and stop reasons.
  - Bench: precision ≥ M0.12 baseline per language; recall delta and unresolved counts reported in PROGRESS.md. ADR-0004 status → Accepted.
- **Verify:** `pytest -q tests -k "call_graph or origins"`; `python -m bench run --all`
- **Out of scope:** new language coverage (M6).

### M1.13 Mask secrets in source before prompts
- **Status:** Exploring
- **Size:** M
- **Lane:** B
- **Depends on:** M1.10
- **Owns:** `src/ai_discovery/llm/redact.py` (new)
- **Acceptance:**
  - High-confidence secret patterns (keys, tokens, connection strings with passwords) masked in any source excerpt sent to an LLM; count recorded as a degradation-free metric.
- **Verify:** `pytest -q tests -k redact`
- **Out of scope:** secret scanning reports.
