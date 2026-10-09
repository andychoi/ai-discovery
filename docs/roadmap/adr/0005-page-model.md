# ADR-0005: Page model — one canonical page store behind wiki, MCP, CLI and exports

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related items:** M1.5, M3.1–M3.10, M4.1–M4.6, M5.1, M2.6

## Context
Docs today are markdown files written straight to disk by several generators, across two trees: the intermediate output dir and the docs root. They also have legacy mirror copies (`entity_backbone.mmd`, etc.).
- **Promised artifacts are missing:** `IMPACT/`, `database/` and `batch-jobs/` have no writers. DMN, EARS and BPMN are single files.
- **Overlapping readers:** the viewer, `chat`, `export-graph` and `verify-drift` each re-read a different subset.
- **No keyless wiki:** without an LLM there are only partial outputs.

repowise renders one wiki from one page store. Structural pages are deterministic (confidence 1.0), and only a few page types get LLM prose (0.8); a failed LLM call becomes a 0.3 stub that never overwrites prose. Human notes survive regeneration.

## Decision
1. **Page record** (table `pages`, new; see `design/architecture-v1.md`):
   - `page_id`: stable, derived from the subject, e.g. `screen:customer-search`, `symbol:<qualified_name>`, `table:dbo.orders`, `file:src/x.cs`.
   - `type`
   - `title`
   - `facts_json`: structured facts used for rendering and grounding.
   - `body_md`: rendered markdown.
   - `links`: outgoing `page_id`s.
   - `citations`: `[{path, line_start, line_end}]`.
   - `confidence`: 1.0 structural / 0.8 prose / 0.3 stub.
   - `stamp`: `{extractor_version, renderer_version, config_hash, commit, dirty}`.
   - `source_hashes`: `{path: sha256}`, used for drift.
   - `freshness`: `fresh|stale|unknown`.
   - `prose_ref`: llm_cache key or null.
2. **Page types (initial):** `overview`, `domain`, `module`, `file`, `symbol`, `endpoint`, `screen`, `menu`, `entity` (FSM + DMN + EARS sections), `table`, `stored_proc`, `batch_job`, `interface`, `flow`, `onboarding`, plus SAP `tcode`/`dynpro` (M7.4).
3. **Builders** (`pages/builders/*.py`, new) are pure functions of a DB snapshot to `Page`. They are auto-discovered by registry, rendered via Jinja templates, and **byte-identical across runs**. Every citation must resolve to an indexed file and line, or the build fails the test.
4. **Prose is opt-in** (`--prose`) for `overview`, `domain`, `screen`, `flow`, `entity` and `onboarding`. Prose is keyed by a hash of the facts (ADR-0002). It is grounded: unverifiable backtick references and paths are flagged, which lowers confidence. On failure a stub is written at 0.3 that never overwrites existing prose.
5. **Notes:** `page_notes(page_id, author, body, created_at)` live outside the page row and are rendered beside it. They survive every rebuild. A note on a removed page stays attached to its tombstone.
6. **Staleness propagates up:** if a file page changes, its module, domain and overview pages are marked stale until rebuilt. The `/api/v1/status` freshness counts include them.
7. **Exports are views over `pages`:** `export --format markdown|html|json`. `push` sends the markdown export (one file per page) to DocHub/Gitea. No SDLC folder-tree profile (owner decision 2026-10-08).

## Consequences
- The SPA, MCP, `ask` and exports all read one store, so their contents cannot disagree.
- The legacy SDLC generators (`src/ai_discovery/generators/*` tree output, `ai/rollup.py`) are deleted in M3.7; the SDLC export profile is not built (owner decision 2026-10-08).
- `verify-drift` becomes `drift`, computed from `pages.source_hashes` for all page types, not just screens.
