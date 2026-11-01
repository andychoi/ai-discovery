# M4 — Wiki web (React SPA in the wheel)

**Goal:** repowise-class wiki UX served by `discover serve` from one port, offline, with no Node at runtime.
**Exit criteria:** install-from-wheel smoke passes without Node; `viewer/` deleted.
**Refs:** `docs/roadmap/design/wiki-web.md`

### M4.1 FastAPI server + API v1 ★
- **Status:** Done (8f5c365)
- **Size:** M
- **Lane:** D
- **Depends on:** M3.1, M1.2
- **Owns:** `src/ai_discovery/server/` (new), `pyproject.toml` (append fastapi, uvicorn)
- **Acceptance:**
  - API v1 exactly as listed in `design/wiki-web.md`: `status`, `pages`, `pages/{id}`, `tree`, `search`, `graph/neighborhood`, `graph/path`, `screens`, `screens/{id}`, `entities/{name}`, `tables/{name}`, `POST pages/{id}/notes`; `impact` added once M5.2 is Done; read-only connection on `current_scan`; one project per process.
  - Binds 127.0.0.1 by default; non-loopback bind requires `--allow-remote` (plus `--host`) and prints a warning; path traversal tests for any file route.
- **Verify:** `pytest -q tests -k server`
- **Out of scope:** MCP (M5.3).

### M4.2 `web/` scaffold + wheel build ★
- **Status:** Done (7cc69a4)
- **Size:** M
- **Lane:** D
- **Depends on:** —
- **Owns:** `web/` (new), `src/ai_discovery/server/static/` (build output, gitignored), `pyproject.toml` / build hook, `Makefile` or `scripts/build_web.py` (new)
- **Acceptance:**
  - Vite + React + TypeScript; mermaid, bpmn-js, dmn-js, graph lib, fonts and icons bundled locally.
  - `scripts/build_web.py` builds into `server/static/`; wheel includes it via package-data.
  - Check fails if built assets reference external `http(s)://` URLs.
- **Verify:** `python scripts/build_web.py && python -m build && unzip -l dist/*.whl | grep server/static/index.html`
- **Out of scope:** feature pages.

### M4.3 SPA shell, nav tree, page reader, search ★
- **Status:** Done (40b5f3e)
- **Size:** M
- **Lane:** D
- **Depends on:** M4.1, M4.2
- **Owns:** `web/src/`
- **Acceptance:**
  - Layout: left nav tree (Overview, Screens by menu, Domains, Entities, Tables, Code), page reader (markdown + mermaid, backlinks, confidence + freshness badges, citations open source view), ⌘K search over `/api/v1/search`, deep links.
  - Empty/partial-index/error states explicit.
- **Verify:** `cd web && npm test`; manual: `discover serve` on fixture
- **Out of scope:** graph explorer.

### M4.4.a Graph explorer + impact view
- **Status:** Done (5a57c16)
- **Size:** M
- **Lane:** D
- **Depends on:** M4.3
- **Owns:** `web/src/`, `src/ai_discovery/server/api_v1.py` (`/impact`)
- **Acceptance:**
  - Neighborhood view capped (server returns true totals; "showing 200 of 3,412"); evidence-kind legend; low-confidence edges visually distinct; path finder A→B.
  - Impact view (M5.2): ranked blast radius, unknowns, "copy as IMPACT_REPORT.md".
- **Verify:** `cd web && npm test`
- **Out of scope:** drilldowns and BPMN/DMN rendering (M4.4.b).

### M4.4.b Drilldowns + evidence-backed process views
- **Status:** Done (e25a3ab)
- **Size:** M
- **Lane:** D
- **Depends on:** M4.4.a
- **Owns:** `web/src/`, `src/ai_discovery/pages/builders/`, `docs/roadmap/adr/0007-evidence-backed-process-views.md` (new)
- **Acceptance:**
  - Screen → flow → entity → table drilldown from stored facts only. Screen pages list the flows they start
    (`routes_to`; the same mapping rule as impact's "screen uses node") and the business state those flows can
    change. Flow pages list the literal state assignments their trace reaches (`writes` → entity) and the
    screens that start them. Entity pages have a "What drives each change" table (trigger, guard, flows,
    screens) plus a stated limit: actors, waits, frequencies and off-system steps are not visible from code.
  - The wiki rail groups links and backlinks by level (↓ deeper, ↑ shallower).
  - **No BPMN rendering** (ADR-0007, which replaces the original "BPMN/DMN rendering" scope). DMN stays a markdown
    decision table built from parsed guards. bpmn-js and dmn-js are removed from the web bundle.
- **Verify:** `cd web && npm test`; `pytest -q tests/test_builders_drilldown_m44b.py`
- **Out of scope:** retiring phase-15 BPMN generation (M3.11); observed process discovery (M8.6).

### M4.5 `serve` + install-from-wheel smoke ★
- **Status:** Done (2006bd6)
- **Size:** M
- **Lane:** D
- **Depends on:** M4.3, M2.1
- **Owns:** `src/ai_discovery/cli/commands/serve.py` (new), `tests/smoke/` (new), CI config
- **Acceptance:**
  - `discover serve [--port] [--open] [--host --allow-remote]`; one port for API + SPA.
  - Smoke test: fresh venv from the wheel, Node removed from PATH → `discover init --yes --no-prose`, `discover scan` on fixture, `discover serve` → `/` and `/api/v1/status` return 200. Linux required; Windows if CI available.
- **Verify:** `pytest -q tests/smoke` (marked slow)
- **Out of scope:** —

### M4.6 Retire viewer; notes UI; staleness badges
- **Status:** Done (c8b72c6)
- **Size:** S
- **Lane:** D
- **Depends on:** M4.5
- **Owns:** `src/ai_discovery/viewer/` (delete), `web/src/`
- **Acceptance:**
  - `viewer/` package and its tests deleted; human notes editable in UI (POST to `page_notes`, local-only by default); stale badge with "run discover scan".
- **Verify:** `pytest -q`; `test ! -d src/ai_discovery/viewer`
- **Out of scope:** —
