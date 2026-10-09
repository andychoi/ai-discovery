# M7 — SAP S/4HANA: ABAP (abapGit) + Fiori/UI5

**Goal:** bring SAP custom code into the same graph/page/impact model. Neither ai-discovery nor repowise supports ABAP/UI5 today.
**Exit criteria:** ABAP + UI5 bench rows exist; TCODE/dynpro and UI5 route screens appear in the wiki.
**Refs:** `docs/roadmap/design/sap-abap-ui5.md`

### M7.1 SAP spike
- **Status:** Planned
- **Size:** S
- **Lane:** F
- **Depends on:** M1.12
- **Owns:** `docs/roadmap/adr/0007-abap-parsing.md` (new), `bench/repos.toml` (SAP rows)
- **Acceptance:**
  - Decision record: tree-sitter ABAP grammar (availability, license, quality — Unverified) vs regex/import-map tier; abapGit file naming verified on ≥ 2 public repos; 2 ABAP + 1 UI5 repo SHA-pinned; owner confirms whether private abapGit exports exist for the sealed split.
- **Verify:** ADR merged; `python -m bench run --repo <abap-id> --dry-run`
- **Out of scope:** implementation.

### M7.2 abapGit inventory
- **Status:** Planned
- **Size:** M
- **Lane:** F
- **Depends on:** M7.1
- **Owns:** `src/ai_discovery/parsers/abap/inventory.py` (new)
- **Acceptance:**
  - Objects from abapGit filenames + XML metadata (PROG, CLAS, INTF, FUGR/FUNC, TABL, TRAN, DDLS if present) → typed nodes with package (DEVC) as module.
- **Verify:** `pytest -q tests -k abap_inventory`
- **Out of scope:** call edges.

### M7.3 ABAP edges
- **Status:** Planned
- **Size:** M
- **Lane:** F
- **Depends on:** M7.2
- **Owns:** `src/ai_discovery/parsers/abap/calls.py` (new)
- **Acceptance:**
  - Literal `CALL FUNCTION 'X'`, `CALL METHOD`/`->`/`=>`, `NEW`, `PERFORM`, `SUBMIT`, `CALL TRANSACTION` → edges; dynamic targets (variables, `(lv_name)`) → `dynamic_hint` only, never a call edge.
  - `SELECT/INSERT/UPDATE/MODIFY/DELETE` on DDIC tables → table read/write edges.
  - External entry points (TCODE, RFC-enabled FMs, reports, BAdI impls) mark reachability; no dead-code claims for ABAP.
- **Verify:** `pytest -q tests -k abap_calls`
- **Out of scope:** —

### M7.4 TCODE + dynpro screens + SAP pages
- **Status:** Planned
- **Size:** M
- **Lane:** F
- **Depends on:** M7.2, M3.3
- **Owns:** `src/ai_discovery/parsers/abap/screens.py` (new), `src/ai_discovery/pages/builders/sap.py` (new)
- **Acceptance:**
  - TCODE → program → dynpro screens (from program XML) as screen pages, linked to PBO/PAI modules and downstream tables.
- **Verify:** `pytest -q tests -k abap_screens`
- **Out of scope:** —

### M7.5 UI5/Fiori
- **Status:** Planned
- **Size:** M
- **Lane:** F
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/parsers/ui5/` (new)
- **Acceptance:**
  - `manifest.json` routing (routes/targets) → XML views → `controllerName` → controller event handlers → OData model calls (service URL, entity set); `sap.ui.define` dependencies; OData service linked to backend when its definition is in the same repo, else external interface page.
- **Verify:** `pytest -q tests -k ui5`
- **Out of scope:** —
