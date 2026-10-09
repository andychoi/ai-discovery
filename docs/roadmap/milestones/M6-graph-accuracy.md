# M6 — Graph accuracy for target stacks

**Goal:** close the parsing/resolution gaps that matter for ASP.NET/WebForms, Java/Spring/JSP, TS/JS SPAs and Python, measured by the benchmark protocol.
**Exit criteria:** accuracy gates in CI; per-language precision/recall reported vs M0.12.
**Refs:** `docs/roadmap/design/graph-accuracy.md`, `docs/roadmap/research/edge-case-catalog.md`

> Every item: add fixtures for the edge cases it fixes, and record bench deltas in PROGRESS.md.

### M6.1 Walker v2
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M0.12
- **Owns:** `src/ai_discovery/repo/walker.py` (new; merges `repo/lang_detector.py` + `repo/file_walker.py`)
- **Acceptance:**
  - Git repos: `git ls-files -co --exclude-standard`; non-git: `.gitignore` via pathspec; plus `.discoveryignore` and `scan.exclude`.
  - Path-pattern excludes (not bare dir names — `packages/` no longer skipped); size caps via `scan.max_file_kb_source` (2048) and `scan.max_file_kb_other` (500), skipped files recorded as `file_too_large`; binary + minified detection; generated-file tags (`*.designer.cs`, `*.g.cs`, banners); symlinks not followed (realpath containment); submodules/nested repos opt-in (`scan.submodules`).
  - Every skip reason counted in degradations.
- **Verify:** `pytest -q tests -k walker`
- **Out of scope:** encoding (M0.4).

### M6.2 C# coverage
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M0.3
- **Owns:** `src/ai_discovery/parsers/csharp.py`
- **Acceptance:**
  - interface/record/struct/enum nodes; file-scoped and single-segment namespaces; partial classes merged by full name; `generic_name` and `?.` call capture; same-namespace + `global using` scope.
- **Verify:** `pytest -q tests -k csharp`
- **Out of scope:** VB.NET (M6.4).

### M6.3 Java coverage
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M0.3
- **Owns:** `src/ai_discovery/parsers/java.py`
- **Acceptance:**
  - interface/enum/record nodes; `_receiver_type` split from `_unwrap_type` (receiver keeps outer type `List`, JPA association keeps inner); local-variable and parameter receiver typing; same-package scope.
  - spring-petclinic call-edge recall improves vs baseline.
- **Verify:** `pytest -q tests -k java`
- **Out of scope:** DI (M6.7).

### M6.4 VB.NET code-behind
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M6.1
- **Owns:** `src/ai_discovery/parsers/vbnet.py` (new)
- **Acceptance:**
  - Spike first: tree-sitter VB.NET grammar availability/quality (Unverified); fall back to import-map tier if none.
  - Classes, Sub/Function, `Handles` clauses, `Imports`; `.aspx` `On*` events resolve to VB handlers.
- **Verify:** `pytest -q tests -k vbnet`
- **Out of scope:** —

### M6.5 Vue SFC + TS barrels
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M0.3
- **Owns:** `src/ai_discovery/parsers/vue.py` (new), `src/ai_discovery/parsers/javascript.py`
- **Acceptance:**
  - `.vue` `<script>`/`<script setup>` parsed with byte offsets preserved (non-script regions blanked); template component tags and `@event` handlers become edges.
  - TS re-exports/barrels (`export * from`, `export {a as b}`) resolved to a fixed point; aliases preserved.
- **Verify:** `pytest -q tests -k "vue or barrel"`
- **Out of scope:** —

### M6.6 SPA routing + Angular DI
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M6.5
- **Owns:** `src/ai_discovery/route_parser.py`, `src/ai_discovery/extractors/` (new adapters)
- **Acceptance:**
  - Vue/React/Angular routes (incl. lazy `import()`) → screens with component file; Angular `@Injectable` constructor DI edges.
- **Verify:** `pytest -q tests -k "route or angular"`
- **Out of scope:** —

### M6.7 DI adapters
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/extractors/di_*.py` (new), `src/ai_discovery/graph/` (dispatch post-pass)
- **Acceptance:**
  - Spring annotations (constructor/field injection, `@Bean`, `@Qualifier`, `@Primary`) + Spring XML beans; ASP.NET `AddScoped/Transient/Singleton<I,T>`; Unity/Autofac registrations (Unverified prevalence).
  - `dispatches_to` interface→impl edges, refused above 24 implementations, private/ctor excluded; ambiguous type → no edge.
- **Verify:** `pytest -q tests -k di`
- **Out of scope:** —

### M6.8 SQL via sqlglot
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/extractors/sql/` (new), `src/ai_discovery/extractors/sql_extractor.py`, `pyproject.toml` (append sqlglot)
- **Acceptance:**
  - DDL + SQL-in-strings parsed with sqlglot (tsql/oracle/mysql/postgres dialects), per-statement table read/write edges; regex fallback counted as degradation; interpolated table names skipped.
  - `.sql` skip check uses repo-relative path.
- **Verify:** `pytest -q tests -k sql`
- **Out of scope:** stored procs (M6.10).

### M6.9 MyBatis
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M6.8
- **Owns:** `src/ai_discovery/extractors/mybatis.py` (new)
- **Acceptance:**
  - Mapper XML `namespace` + statement `id` ↔ mapper interface method; statement SQL → table edges (dynamic `<if>/<foreach>` flattened conservatively). Prevalence in target estates Unverified — measure on owner repos.
- **Verify:** `pytest -q tests -k mybatis`
- **Out of scope:** —

### M6.10 Stored procedures + EXEC edges
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M6.8
- **Owns:** `src/ai_discovery/extractors/sql/procs.py` (new)
- **Acceptance:**
  - Proc nodes; code → proc edges (ADO.NET `CommandType.StoredProcedure`, JDBC `CallableStatement`, MyBatis `statementType=CALLABLE`); proc → table read/write and proc → proc `EXEC` (sqlglot procedural coverage Low confidence → regex fallback).
- **Verify:** `pytest -q tests -k proc`
- **Out of scope:** —

### M6.11 WebForms/JSP/Struts extras
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M6.1
- **Owns:** `src/ai_discovery/parsers/webforms.py`, `src/ai_discovery/parsers/jsp.py`, `src/ai_discovery/webforms_extractor.py`, `src/ai_discovery/jsp_extractor.py`, `src/ai_discovery/extractors/webconfig.py` (new)
- **Acceptance:**
  - `.ascx`/`.master`/`.asmx`/`.ashx`/`Global.asax`, `<%@ Register %>`, `Page_Load` auto-wiring, `SqlDataSource`; `web.config`/`web.xml` servlet mappings; `struts-config.xml` actions; `markup_event` edges.
- **Verify:** `pytest -q tests -k "webforms or jsp or struts"`
- **Out of scope:** —

### M6.12 Entry-point scoring
- **Status:** Planned
- **Size:** M
- **Lane:** C
- **Depends on:** M6.7, M6.11
- **Owns:** `src/ai_discovery/graph/entry_points.py` (new), `src/ai_discovery/graph/call_graph.py`
- **Acceptance:**
  - Evidence-based score (framework registration, route/screen binding, fan-out, in-degree 0, visibility) replaces substring name hints; test/demo paths excluded; precision measured on bench repos.
- **Verify:** `pytest -q tests -k entry_point`
- **Out of scope:** —

### M6.13 Accuracy gates in CI
- **Status:** Planned
- **Size:** M
- **Lane:** Q
- **Depends on:** M0.11, M1.12
- **Owns:** `bench/`, `tests/corpus/`, CI config
- **Acceptance:**
  - Per language × origin precision (graded sample) and per-repo recall tracked; regression beyond tolerance fails CI; saturated fixtures (all 1.0) replaced with hard cases from the edge-case catalog; sealed split never used for tuning.
- **Verify:** `python -m bench gate`
- **Out of scope:** —
