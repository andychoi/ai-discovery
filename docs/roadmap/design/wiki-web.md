# Wiki web (SPA) design

- **Status:** Accepted
- **Last updated:** 2026-10-08
- **Related items:** M4.1–M4.6, M3.1, M3.5, M3.10, M5.1, M2.5
- **Replaces:** `src/ai_discovery/viewer/`: stdlib `ThreadingHTTPServer` + Jinja, CDN-loaded mermaid/bpmn-js/dmn-js, and a legacy-path bug (`viewer/server.py:67-68`).

## 1. Constraints
- **Stack:** Vite + React + TypeScript SPA in `web/` (new). It is built at release time into `src/ai_discovery/server/static/` (new, gitignored) and shipped as wheel package-data.
- **No Node at runtime.** `pip install ai-discovery` followed by `discover serve` must work on a machine without Node (M4.5 smoke test).
- **Offline / air-gapped.** No CDN, web fonts or remote images. mermaid, bpmn-js, the graph library, fonts and icons are vendored into the bundle. CI fails if `server/static/**` contains `http://` or `https://` asset references, other than the allowlisted `localhost` (M4.2).
- **One port.** FastAPI serves `/api/v1/*` and the SPA, with history fallback to `index.html`.
- **Read-only, `current_scan`, one project per process** (ADR-0002).

## 2. Routes (SPA)

| Route | Content | Item |
|---|---|---|
| `/` | Overview page: system summary, stats tiles (files, symbols, screens, entities, tables), freshness (indexed vs HEAD), degradations, "start here" links | M4.3 |
| `/docs/*pageId` | Docs explorer: left tree (by type: Screens / Domains / Modules / Entities / Tables / Flows / Interfaces), reader (markdown + mermaid + BPMN), right rail (backlinks, citations as file:line, confidence badge, freshness badge, human notes) | M4.3, M4.6 |
| `/screens` | Menu tree → screen; each screen shows the downstream chain: UI events → endpoints → services → tables, plus batch jobs and interfaces | M4.4 |
| `/graph?focus=…` | Graph explorer: neighborhood of a node (depth 1–2), evidence-kind legend and filters, path finder A→B | M4.4 |
| `/impact?target=…` | Ranked blast radius (same data as MCP `get_impact`), the unknowns section, and a "copy as IMPACT_REPORT.md" button | M4.4 (needs M5.2) |
| `/entities/:name` | FSM diagram, DMN rules, EARS requirements, writers/readers | M4.4 |
| `/flows/:id` | Flow / BPMN view (bpmn-js vendored) | M4.4 |
| `/db` | Tables, FK ERD (mermaid `erDiagram`), table → readers/writers, stored procedures | M4.4 |
| `/search?q=` and ⌘K palette | Hybrid FTS5 + vector search over pages and symbols | M4.3 |
| `/status` | Scan history, stage table, spend, stale pages, degradations | M4.6 |

**UI rules:**
- Every list says "showing N of TOTAL".
- Empty, loading and error states exist for every view.
- An "index is N commits behind" banner appears when `meta.behind_by > 0`.
- Stub pages (confidence 0.3) show a banner, and prose pages show a "model-written" marker.

## 3. API v1 (FastAPI, `server/api_v1.py`, new)

All responses use the `Answer` envelope from `design/architecture-v1.md` §6:

```json
{"status":"ok","data":{},"totals":{},"truncated":false,"omitted":[],"meta":{"scan_id":12,"indexed_commit":"abc123","head_commit":"def456","behind_by":3,"dirty":false,"stale_pages":4,"degraded":["encoding_fallback"],"contract_version":"1"}}
```

| Method & path | Query | `data` shape |
|---|---|---|
| `GET /api/v1/status` | — | `{project, scans:[{scan_id,commit,status,started_at,ended_at,spend_usd}], stages:[{stage,status,fingerprint}], degradations:[{stage,kind,count}]}` |
| `GET /api/v1/pages` | `type`, `prefix`, `limit`, `offset` | `{items:[{page_id,type,title,confidence,freshness}]}` + `totals.items` |
| `GET /api/v1/pages/{page_id}` | — | `{page_id,type,title,body_md,facts,links,backlinks,citations,confidence,freshness,stamp,notes:[…]}`; a tombstone gives `status:"not_found"` + `data.successor` |
| `GET /api/v1/tree` | — | nav tree `{type:[{page_id,title,children}]}` |
| `GET /api/v1/search` | `q`, `kinds`, `limit` | `{hits:[{page_id,title,type,snippet,score,source:"fts"\|"vec"\|"both"}]}`; `snippet` is plain text (markdown syntax stripped) with matches wrapped in `[` `]` — the only brackets left |
| `GET /api/v1/source` | `path`, `start`, `end`, `context` | `{path,start,end,first_line,lines,total_lines}` — only files cited by some page of the current scan, inside the repo; anything else is `not_found` (added in M4.3 for the citation source view) |
| `GET /api/v1/graph/neighborhood` | `node`, `depth≤2`, `kinds`, `limit≤500` | `{nodes:[{id,label,type,file,line}], edges:[{src,dst,edge_kind,origin,confidence,evidence_kind}]}` + `totals.nodes/edges` |
| `GET /api/v1/graph/path` | `from`, `to`, `max_depth≤6` | `{paths:[[node_id…]]}` |
| `GET /api/v1/impact` | `target`, `depth`, `include` | as MCP `get_impact` output |
| `GET /api/v1/screens` / `/screens/{id}` | — | menu tree / screen chain |
| `GET /api/v1/entities/{name}` | — | FSM states/transitions, DMN, EARS, readers/writers |
| `GET /api/v1/tables/{name}` | — | columns, FKs, readers/writers, procs |
| `POST /api/v1/pages/{page_id}/notes` | body `{author, body}` | the only write endpoint; loopback only unless `--allow-remote` with token |

**Errors:** HTTP 200 with `status != "ok"` for domain outcomes (`not_found`, `unknown`, `not_indexed`); HTTP 4xx/5xx only for protocol errors. This matches the MCP shield behaviour.

## 4. Large-graph caps
- **Server side:** neighborhood requests are capped at 500 nodes, ranked by (degree in focus subgraph, confidence). `totals` and `truncated` are always returned.
- **Client side:**
  - auto layout (ELK or dagre) for 300 nodes or fewer;
  - force layout for 301–500;
  - above that, the user must filter (by kind, depth or domain).
  - Edge labels are hidden above 200 edges.
- **Hub dampening:** nodes with in-degree above the 99th percentile (logging, utils) are collapsed into a "hub" chip unless they are the focus.

## 5. Build and packaging
- **Layout:** `web/package.json` (pinned versions, no `^` ranges). `npm ci && npm run build` writes to `../src/ai_discovery/server/static/`.
- **Packaging:** `pyproject.toml` package-data includes `server/static/**`. The release workflow builds the SPA first, then the wheel.
- **Dev mode:** `npm run dev` proxies `/api` to `discover serve --port 7341`.
- **API types:** TS types are generated from the FastAPI OpenAPI schema into `web/src/api/types.ts` (`scripts/gen_api_types.py`, new). A CI check fails if they are stale.
- **Smoke test** (M4.5): fresh venv, `pip install dist/*.whl`, scan the fixture repo, start `serve`, then GET `/` and `/api/v1/status` must both return 200. It runs on Linux and, if CI allows, Windows. No Node is on PATH.

## 6. Static export (M3.10)
- **Command:** `discover export --format html` copies the SPA bundle plus `data.js`, which inlines all pages, the tree and the search index as JS (`window.__DISCOVERY_DATA__`). It does not use `fetch()`, because `file://` blocks it.
- **Search:** client-side, over a prebuilt lunr-style index (choose a small, vendorable library at implementation time; Unverified which).
- **Graph view:** limited to precomputed neighborhoods of the top-N nodes.
- **As built (M3.10):**
  - **Answers:** `export/html.py` records every GET answer the wiki reads by calling the real API in-process (`TestClient`, no socket): `/status`, `/tree`, `/screens` and `/pages/<id>` for every page. The export shows exactly what `serve` would; local paths in `/status` are blanked.
  - **Bundle:** `file://` refuses module scripts and lazily loaded chunks, so the export uses its own bundle (`web/vite.static.config.ts` → `server/static_export`): one classic IIFE script with dynamic imports inlined and relative paths. `index.html` loads `data.js` first. `scripts/build_web.py` builds and checks both bundles.
  - **Routing:** in a static export the SPA routes through the hash (`#/docs/<id>`).
  - **Search:** a dependency-free token match over an inlined index (title and plain page text, every token must match, title hits first).
  - **Read-only:** notes and reviews are read-only, and source snippets are not included.
  - **Verified:** a headless Chrome load from `file://` renders the overview, a hash-routed page and search results.
- **JSON** (`export/json.py`): schema `ai-discovery/knowledge-graph` v2 supersedes `export-graph` (v1). It has repo-relative paths, no source or page bodies, no export timestamp (re-exports are byte-identical), plus unresolved calls and the page index with links.
- **Markdown** (`export/markdown.py`): one file per page (`<type>/<key>.md`) with JSON-valued front matter. This is what `push` delivers once it lands.

## 7. Retirement
M4.6 deletes `src/ai_discovery/viewer/` and its tests (`tests/test_viewer_dashboard.py`). The quality widgets move to `/status` and the overview: confidence histogram, weakest docs, scoped to `current_scan`.
