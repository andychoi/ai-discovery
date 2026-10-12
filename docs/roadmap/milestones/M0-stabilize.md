# M0 — Stabilize & measure (current architecture)

**Goal:** fix the defects that would survive any rewrite, make the current tool trustworthy for the owner meanwhile, and record a measured baseline before anything changes.
**Exit criteria:** all M0 items Done; baseline report committed; `pytest -q` green.
**Refs:** `docs/roadmap/research/ai-discovery-audit.md`, `docs/roadmap/design/benchmark-protocol.md`

> Status of each item lives HERE. See `docs/roadmap/README.md` for the protocol.

### M0.1 Repo hygiene
- **Status:** Done (05032f5)
- **Size:** S
- **Lane:** A
- **Depends on:** —
- **Owns:** repo root files, `ai_discovery.egg-info/`, `src/ai_discovery/shared/README.md`, `README.md`, `CLAUDE.md`, `docs/integrations/`, `docs/archived/`
- **Acceptance:**
  - `ai_discovery.egg-info/` untracked (`git rm -r --cached`); `PM4PY_QUICKSTART.txt`, `SESSION_SUMMARY.md`, `demo.sh` removed or moved to `docs/archived/`; `docs/integrations/PM4PY_*` archived; stale `shared/README.md` rewritten to describe what exists.
  - README/CLAUDE.md stop promising artifacts that no writer produces (`IMPACT/`, `database/`, `batch-jobs/`, per-entity DMN/EARS) — mark as roadmap items instead.
  - `.env.sample` lists only variables the code reads.
- **Verify:** `pytest -q`; `git ls-files | grep -c egg-info` → 0
- **Out of scope:** license decision (open item in ROADMAP.md); code changes.

### M0.2 Golden characterization harness ★
- **Status:** Done (8226863)
- **Size:** M
- **Lane:** A
- **Depends on:** —
- **Owns:** `tests/golden/` (new), `tests/golden/stub_llm.py` (new)
- **Acceptance:**
  - A recorded-response stub LLM (keyed by prompt hash; unknown prompt → deterministic placeholder), injected from `tests/golden/` by patching `ai_discovery.ai.llm_client.LLMClient` (same seam as `tests/test_integration.py`) — no edit to lane-B files; a real config-selectable stub provider arrives with M1.10.
  - 2 composed fixtures scanned end-to-end: `java-spring-jsp` (`projects/spring-boot-app` + `jsp/`) and `dotnet-webforms` (`projects/aspnet-core-app` + `webforms/`); snapshot = per-table row counts, sorted call-edge tuples (repo-relative), domains, screen ids (from `screen_specs`), output file paths, LLM call counts per method/tier.
  - Two consecutive runs produce identical snapshots; snapshot update requires `UPDATE_GOLDEN=1`.
- **Verify:** `pytest -q tests/golden`
- **Out of scope:** asserting LLM prose content.

### M0.3 CLI smoke tests + tree-sitter floor ★
- **Status:** Done (78b8cc8)
- **Size:** S
- **Lane:** C
- **Depends on:** —
- **Owns:** `tests/cli/` (new), `tests/test_ts_floor.py` (new), `pyproject.toml` (grammar pins only)
- **Acceptance:**
  - Typer `CliRunner` test per existing command (13) — at least `--help` and one happy/failure path each (documents current behaviour; `ingest-docs` crash captured as xfail until M0.9).
  - Every tree-sitter query string in `src/ai_discovery/parsers/` compiles against the pinned minimum grammar versions; test fails loudly on a compile error (no silent zero-symbol language).
- **Verify:** `pytest -q tests/cli tests/test_ts_floor.py`
- **Out of scope:** new CLI (M2).

### M0.4 Encoding fallback ★
- **Status:** Done (39b2b9a)
- **Size:** M
- **Lane:** C
- **Depends on:** —
- **Owns:** `src/ai_discovery/core/encoding.py` (new), `src/ai_discovery/parsers/*` (decode call sites), `tests/fixtures/encoding/` (new)
- **Acceptance:**
  - Source bytes decoded via: BOM sniff (UTF-8-sig/UTF-16) → strict UTF-8 → declared hint (JSP `pageEncoding`/`charset`, `<?xml encoding?>`) → CP949 → EUC-KR → lossy replace, then re-encoded to UTF-8 bytes **before** tree-sitter so offsets stay consistent.
  - Fixtures in cs/java/jsp/aspx with Korean comments and string literals parse with the same node count as their UTF-8 twins; Korean text preserved in extracted strings.
  - Per-scan count of fallback-decoded files reported (console + log after parse); each affected node carries `framework_hints.source_encoding` (persisted); no file silently dropped for decoding. (DB-level `degradations` table arrives with M1.5/M1.8.)
- **Verify:** `pytest -q tests -k encoding`
- **Out of scope:** the `_parse_one` call-site in `pipeline.py` belongs to lane A — coordinate (single small edit, noted in PROGRESS.md).

### M0.5 Budget for all paid providers ★
- **Status:** Done (85e03ef)
- **Size:** M
- **Lane:** A
- **Depends on:** —
- **Owns:** `src/ai_discovery/pipeline.py` (`_budget_ok`, `_budget_exhausted_fn`), `src/ai_discovery/cli.py` (scan exit code)
- **Acceptance:**
  - Budget enforced for every provider with a price (Bedrock, OpenAI, Gemini, Anthropic); local providers (ollama/mlx) exempt.
  - Budget stop marks the run `budget_exceeded` and `discover scan` exits 3.
- **Verify:** `pytest -q tests -k budget`
- **Out of scope:** pre-run cost estimate (M2.4).

### M0.6 Resume correctness ★
- **Status:** Done (cd605f3)
- **Size:** M
- **Lane:** A
- **Depends on:** M0.2, M0.5
- **Owns:** `src/ai_discovery/pipeline.py`
- **Acceptance:**
  - Repo argument canonicalized (resolved absolute path) before prior-run lookup; relative vs absolute paths resume the same run.
  - `--resume` refuses (clear message) when HEAD SHA differs from the run being resumed.
  - Phase 13 resume path rehydrates from DB without LLM calls; no bare `except: pass` in phase 13.
  - `--skip-phases` honoured for 14–19 (or rejected with a clear error) — no silent no-op.
  - Self-review per-doc timeout actually fires (`concurrent.futures.wait(timeout=…)` + cancel), not `result(timeout)` on finished futures.
  - Golden snapshot (M0.2) unchanged.
- **Verify:** `pytest -q tests/golden tests -k resume`
- **Out of scope:** stage extraction (M1.6+).

### M0.7 LLM output integrity
- **Status:** Done (a37eff1)
- **Size:** M
- **Lane:** B
- **Depends on:** —
- **Owns:** `src/ai_discovery/ai/llm_client.py`, `src/ai_discovery/ai/summarizer.py`, `src/ai_discovery/shared/llm_router.py`
- **Acceptance:**
  - `truncated` from `llm_router` propagated through `LLMClient.invoke`; truncated outputs counted and not treated as complete.
  - "Summarization failed" placeholders are never persisted as summaries and never reused by `reuse_prior_summaries`; failed nodes are retried on resume.
  - Tier-1 reuse key includes model id and prompt version.
- **Verify:** `pytest -q tests -k "summar or llm_client"`
- **Out of scope:** provider consolidation (M1.10).

### M0.8 Secret hygiene ★
- **Status:** Done (b6348b8)
- **Size:** S
- **Lane:** A
- **Depends on:** M0.6
- **Owns:** `src/ai_discovery/pipeline.py` (config_json write), `src/ai_discovery/repo/resolver.py`, `src/ai_discovery/db.py` (one migration)
- **Acceptance:**
  - `scan_runs.config_json` written via an allowlist serializer; test asserts no key matching `*key*|*token*|*secret*|*password*` is stored; migration scrubs existing rows.
  - Clone token never embedded in the persisted remote URL (use `GIT_ASKPASS` / `http.extraHeader` env); existing clones' remotes scrubbed on next scan; git subprocess calls get timeouts.
  - Error messages persisted to DB are redacted.
- **Verify:** `pytest -q tests -k "secret or resolver"`
- **Out of scope:** masking secrets inside source sent to LLMs (M1.13).

### M0.9 Interim path fixes
- **Status:** Done (568149d)
- **Size:** S
- **Lane:** A
- **Depends on:** —
- **Owns:** `src/ai_discovery/viewer/server.py`, `src/ai_discovery/viewer/dashboard.py`, `src/ai_discovery/rag/chat.py`, `src/ai_discovery/drift_checker.py`, `src/ai_discovery/cli.py`
- **Acceptance:**
  - ~~Viewer finds docs/diagrams under the `output-<slug>` layout~~ and ~~`chat` indexes the docs tree the scan writes~~ — done by M1.2 (all readers use `ProjectLayout`). Remaining: dashboard queries filter by latest scan.
  - `verify-drift` exits non-zero when the spec dir is missing/empty and defaults to the real screens dir.
  - `ingest-docs` no longer raises NameError (`_VALID_PUSH_MODES`).
  - Each fix has a regression test; viewer tests build the new layout.
- **Verify:** `pytest -q tests -k "viewer or chat or drift or ingest"`
- **Out of scope:** these commands are replaced in M2/M4; keep fixes minimal.

### M0.10 Keyless (`--no-llm`) mode in current pipeline ★
- **Status:** Done (6dcea40)
- **Size:** S
- **Lane:** A
- **Depends on:** M0.6
- **Owns:** `src/ai_discovery/pipeline.py`, `src/ai_discovery/config.py`, `src/ai_discovery/cli.py` (scan flag)
- **Acceptance:**
  - `discover scan --no-llm` skips phases 2, 10–14, 17 (all LLM/embedding), completes with status `completed`, persists graph, screens (mapping without LLM spec), FSM/DMN/EARS, dependency catalog.
  - No provider credentials required; no network.
- **Verify:** `pytest -q tests -k no_llm` (scan fixture with no provider configured)
- **Out of scope:** structural wiki pages (M3).

### M0.11 Benchmark harness
- **Status:** Done (a9acafe)
- **Size:** M
- **Lane:** Q
- **Depends on:** —
- **Owns:** `bench/` (new), `tests/corpus/`
- **Acceptance:**
  - `python -m bench run --repo <id>` scans a pinned repo (from `bench/repos.toml`, SHA-pinned) and emits JSON: wall time, $ cost, files indexed/skipped by reason, nodes by kind, edges by `resolved_by`, unresolved count, screens, recall vs ground truth (per repo, never pooled).
  - Seeded stratified precision sample generator (30 edges per language × origin) producing a CSV for hand grading; graded CSVs re-scored by script.
  - Candidate repos from `design/benchmark-protocol.md` verified to exist and SHA-pinned.
- **Verify:** `python -m bench run --repo spring-petclinic --dry-run`; `pytest -q tests/corpus`
- **Out of scope:** CI gating (M6.13).

### M0.12 Baseline: ai-discovery vs repowise
- **Status:** Done (f833097)
- **Size:** M
- **Lane:** Q
- **Depends on:** M0.11
- **Owns:** `docs/roadmap/benchmarks/` (new)
- **Acceptance:**
  - Required: the owner's local apps `../sample-apps/spring-petclinic` (@ `500158f`) and `../sample-apps/eShop` (@ `dc7ea49`); plus the existing corpus repos (node-express-realworld, microblog). WebForms/JSP/ABAP rows recorded as "no repo available" scanned by ai-discovery (`--no-llm` and with LLM) and by repowise (`repowise init --no-prose -y`, black box, outputs only).
  - Report `docs/roadmap/benchmarks/YYYY-MM-DD-baseline.md` with the protocol's metrics + precision sample results for ai-discovery; UX observations (commands to first useful output, time-to-wiki).
- **Verify:** report exists and links raw JSON under `docs/roadmap/benchmarks/raw/`
- **Out of scope:** reading repowise source (ADR-0006).

### M0.13 Test-file detection uses repo-relative paths
- **Status:** Planned
- **Size:** S
- **Lane:** B
- **Depends on:** —
- **Owns:** `src/ai_discovery/ai/summarizer.py`, `src/ai_discovery/pipeline.py` (`_count_tier1_targets` call site only)
- **Acceptance:**
  - `_is_test_chunk` classifies by the path relative to the scanned repo root, so a checkout under any `test`/`tests` directory still gets Tier-1 summaries (audit #90).
  - Regression test: the same fixture under `<tmp>/tests/app` and `<tmp>/app` yields the same Tier-1 target count; a real `tests/` dir inside the repo is still skipped.
  - When Tier 1 has chunks but 0 targets, the scan records a degradation instead of completing silently.
- **Verify:** `pytest -q tests -k "summar or tier1"`
- **Out of scope:** making `code_nodes.file_path` repo-relative everywhere (M1.6+).
