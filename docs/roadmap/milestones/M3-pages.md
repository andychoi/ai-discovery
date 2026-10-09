# M3 — Page model & keyless structural wiki

**Goal:** every scan produces a complete, deterministic wiki with no LLM; prose is an opt-in layer. The legacy SDLC folder tree (ASIS/ASD/ASSC/PF/…) is **not** carried forward (owner decision 2026-10-08).
**Exit criteria:** two full runs byte-identical; incremental == full; legacy SDLC generators removed.
**Refs:** `docs/roadmap/adr/0005-page-model.md`, `docs/roadmap/design/architecture-v1.md`, `docs/roadmap/design/wiki-web.md`

> Builders read the **existing** DB tables (`code_nodes`, `call_edges`, `screens`, `screen_mappings`, `entity_state_machines`, `db_relationship`, `domains`, …). They do not wait for M1.6–M1.9.

### M3.1 Page model, registry, notes ★
- **Status:** Done (f005eda)
- **Size:** M
- **Lane:** D
- **Depends on:** M1.5
- **Owns:** `src/ai_discovery/pages/{model,registry,notes}.py` (new)
- **Acceptance:**
  - `Page` = stable id (`<type>:<key>`), title, type, facts, sections, links (typed), citations (`file:line`), confidence, stamp (builder version, config hash, commit), source hashes, freshness.
  - Builders auto-discovered (`pkgutil`), each declares the page types it emits and its inputs.
  - `page_notes` keyed by page id survive regeneration and id aliases (renames).
- **Verify:** `pytest -q tests -k pages_model`
- **Out of scope:** concrete builders.

### M3.2 Builders: overview, module, file, symbol ★
- **Status:** Done (538bead)
- **Size:** M
- **Lane:** D
- **Depends on:** M3.1
- **Owns:** `src/ai_discovery/pages/builders/code.py` (new), `src/ai_discovery/pages/templates/` (new)
- **Acceptance:**
  - System overview (languages, entry points, domains, screens count, external systems), module/dir pages, file pages (top-N by in-degree for big repos, cap configurable), symbol pages for entry points + hubs.
  - 100% of citations resolve to existing file:line; footer states "derived from structure".
- **Verify:** `pytest -q tests -k builders_code`
- **Out of scope:** prose.

### M3.3 Builders: screen, endpoint, entity/FSM, table, interface ★
- **Status:** In progress (rm/M3.3-business-builders, 2026-10-09)
- **Size:** M
- **Lane:** D
- **Depends on:** M3.1
- **Owns:** `src/ai_discovery/pages/builders/business.py` (new)
- **Acceptance:**
  - Screen page: menu path, route/file, actions → APIs → services → tables (from `screen_mappings`), permissions if known, source hashes.
  - Endpoint, entity (FSM mermaid + DMN table + EARS list per entity), table (columns, FKs, readers/writers), external interface pages; all cross-linked.
- **Verify:** `pytest -q tests -k builders_business`
- **Out of scope:** LLM screen narrative (M3.7).

### M3.4 Builders: flow, domain, onboarding, batch job, stored proc
- **Status:** Planned
- **Size:** M
- **Lane:** D
- **Depends on:** M3.2, M1.12
- **Owns:** `src/ai_discovery/pages/builders/flows.py` (new)
- **Acceptance:**
  - Flow pages from static execution slices (resolved edges only, stop reasons shown); domain pages; onboarding tour (entry point first); batch-job and stored-proc pages render when M6.10 data exists, otherwise omitted (not empty).
- **Verify:** `pytest -q tests -k builders_flows`
- **Out of scope:** —

### M3.5 Page build hook + FTS + idempotence ★
- **Status:** Planned
- **Size:** M
- **Lane:** D
- **Depends on:** M3.2, M3.3, M2.3
- **Owns:** `src/ai_discovery/pages/build.py` (new)
- **Acceptance:**
  - Post-scan hook builds all pages into `pages` + `pages_fts` for the new snapshot; deleted subjects tombstoned (redirect to nearest parent).
  - Two full scans of the same tree → byte-identical page bodies (test).
  - Staleness propagates up the hierarchy (file stale → module/overview stale).
- **Verify:** `pytest -q tests -k "page_build or idempotence"`
- **Out of scope:** web rendering.

### M3.6 Incremental parity
- **Status:** Planned
- **Size:** M
- **Lane:** D
- **Depends on:** M3.5, M1.9
- **Owns:** `tests/parity/` (new)
- **Acceptance:**
  - 3 scripted commit sequences (edit, rename, delete+recreate) on a fixture git repo: incremental pages == clean full-scan pages.
- **Verify:** `pytest -q tests/parity`
- **Out of scope:** —

### M3.7 Prose layer
- **Status:** Planned
- **Size:** M
- **Lane:** B
- **Depends on:** M3.5, M1.11
- **Owns:** `src/ai_discovery/pages/prose.py` (new), `src/ai_discovery/prompts/` (new), `src/ai_discovery/ai/rollup.py` (delete), `src/ai_discovery/generators/` (SDLC tree parts, delete; coordinate with lane D)
- **Acceptance:**
  - `--prose` adds narrative sections to overview, domain, screen, flow, entity, onboarding pages; deterministic grouping decides coverage, LLM only names/narrates.
  - Reuse via `llm_cache`, key per ADR-0002; unchanged → no call.
  - Provider failure → stub at confidence 0.3 that never overwrites existing prose; truncation → invalid, not retried identically.
  - Legacy SDLC output removed: ASIS/ASD/ASSC rollup stage, `generators/doc_generator.py` SDLC tree, legacy-mirror files and unused templates deleted; `scan --prose` uses only the page prose layer.
- **Verify:** `pytest -q tests -k prose` (stub LLM)
- **Out of scope:** —

### M3.8 Groundedness
- **Status:** Planned
- **Size:** M
- **Lane:** B
- **Depends on:** M3.7
- **Owns:** `src/ai_discovery/pages/grounding.py` (new); reuses `src/ai_discovery/ai/prose_validator.py`, `src/ai_discovery/ai/self_review.py`
- **Acceptance:**
  - Backticked symbols/paths and links in prose validated against the index; unverifiable claims flagged and reflected in page confidence; one corrective re-ask then stub.
  - LLM-written mermaid sanitized or dropped.
- **Verify:** `pytest -q tests -k grounding`
- **Out of scope:** —

### M3.10 JSON + static HTML export
- **Status:** Planned
- **Size:** M
- **Lane:** D
- **Depends on:** M3.5, M4.3
- **Owns:** `src/ai_discovery/export/{json,html}.py` (new)
- **Acceptance:**
  - `json`: versioned knowledge-graph schema (supersedes `export-graph`), no source bodies.
  - `html`: SPA + data inlined as JS (works from `file://`, no fetch, no network).
- **Verify:** `pytest -q tests -k "export_json or export_html"`
- **Out of scope:** —
