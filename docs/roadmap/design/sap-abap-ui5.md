# Design: SAP S/4HANA — ABAP (abapGit) and Fiori/UI5

- **Status:** Proposed. M7.1 spike decides the parser tier.
- **Last updated:** 2026-10-08
- **Related items:** M7.1–M7.5. Depends on M1.12 (edge contract) and M3.3 (screen pages).
- **Scope (owner decision):** ABAP source exported with **abapGit**, plus **Fiori/SAPUI5** apps. CDS/AMDP/SQLScript is not in initial scope.

Neither ai-discovery nor repowise handles ABAP or UI5 today; both were checked by grep. The closest analogue is repowise's COBOL handling: literal calls resolve, **dynamic calls deliberately emit no edge**, and dead-code claims are suppressed because entry points live outside the repo. We adopt that rule as a design principle. The rule is described in our own words, and no code is copied (ADR-0006).

---

## 1. Input model: abapGit repositories

abapGit serializes each repository object as one or more files, named `<object_name>.<object_type>[.<subpart>].<ext>`. The naming details below are **(Unverified)** until the M7.1 spike checks them against real repos.

| Object type | Typical files | Becomes |
|---|---|---|
| CLAS (class) | `zcl_foo.clas.abap`, `zcl_foo.clas.locals_imp.abap`, `zcl_foo.clas.testclasses.abap`, `zcl_foo.clas.xml` | class node; methods are symbols; local/test includes are linked as part of the class |
| INTF (interface) | `zif_foo.intf.abap`, `.xml` | interface node |
| PROG (report/program) | `zfoo.prog.abap`, `zfoo.prog.xml` (may contain **dynpro** screens and GUI status) | program node; FORM routines are symbols; dynpros become screen candidates |
| FUGR (function group) | `zfg.fugr.<FM_NAME>.abap`, `zfg.fugr.l<...>top.abap`, `.xml` | function modules are symbols; RFC-enabled flag comes from XML metadata |
| TABL (DDIC table/structure) | `ztab.tabl.xml` | table node (fields, keys). Feeds table pages and DB blast radius. |
| TRAN (transaction code) | `ztcode.tran.xml` | **entry point**: TCODE → program/dynpro or class method |
| DDLS (CDS view) | `.ddls.asddls` | Out of initial scope. Recorded as an inventory node only. |
| MSAG, DOMA, DTEL, … | `.xml` | Inventory nodes only (later: domain value lists for DMN) |

The `.abapgit.xml` file at the repo root names the starting folder and the folder logic. Read it to resolve package (devclass) folders.

**Encoding:** abapGit writes UTF-8 **(Inference)**. The shared fallback (`graph-accuracy.md` §2) still applies.

## 2. ABAP extraction targets (M7.2 inventory, M7.3 edges)

**Parser tier** is decided in the M7.1 spike:
- **Option A:** a tree-sitter ABAP grammar. Whether one exists, its maturity, license and availability on PyPI are all **(Unverified)**.
- **Option B:** a statement-level tokenizer plus regex rules. ABAP statements end with `.`, `*` in column 1 and `"` start comments, and keywords are case-insensitive. Option B is the expected fallback. It follows the existing import-map tier pattern in `src/ai_discovery/parsers/import_map.py`.

Symbols come from `CLASS … DEFINITION/IMPLEMENTATION`, `METHODS`/`METHOD … ENDMETHOD`, `FORM … ENDFORM`, `FUNCTION … ENDFUNCTION`, `MODULE … OUTPUT/INPUT` (dynpro PBO/PAI modules) and `INTERFACE`.

### Edge rules: literal vs dynamic

| Statement | Literal form, which **emits an edge** | Dynamic form, which **emits no edge** (a `dynamic_hint` only) |
|---|---|---|
| Function call | `CALL FUNCTION 'Z_FOO'` | `CALL FUNCTION lv_name` |
| Method call | `obj->meth( )`, `zcl_x=>meth( )`, `CALL METHOD obj->meth`. Receiver type comes from `DATA obj TYPE REF TO zcl_x`. | `CALL METHOD (lv_meth)`, `CALL METHOD obj->(lv)` |
| Subroutine | `PERFORM foo` / `PERFORM foo IN PROGRAM zprog` | `PERFORM (lv) IN PROGRAM (lv2)` |
| Report | `SUBMIT zreport` | `SUBMIT (lv)` |
| Transaction | `CALL TRANSACTION 'ZT01'`, `LEAVE TO TRANSACTION` | variable TCODE |
| Instantiation | `NEW zcl_x( )`, `CREATE OBJECT o TYPE zcl_x` | `CREATE OBJECT o TYPE (lv)` |
| RFC destination | `CALL FUNCTION 'X' DESTINATION 'D'` | — |

The RFC destination row is an edge **plus** an `external_boundary` interface node.

Standard SAP objects (`BAPI_*`, `SAPGUI`, standard classes with no `Z`/`Y` or customer namespace prefix) become **external interface nodes**, never unresolved edges. The customer namespace prefixes (`Z`, `Y`, `/NAMESPACE/`) are configurable.

### Table DML edges
- `SELECT … FROM ztab` / `JOIN` produce `reads_table`.
- `INSERT ztab`, `UPDATE ztab`, `MODIFY ztab`, `DELETE FROM ztab` produce `writes_table`.
- The table resolves to a TABL inventory node when it is in the repo. Otherwise it becomes an external SAP standard table node, e.g. `VBAK`, `MARA`.
- Open SQL with a dynamic table `FROM (lv_tab)` gets a hint only.

These edges drive the DB blast radius exactly as sqlglot edges do for other stacks (`graph-accuracy.md` §7).

## 3. Entry points and screens (M7.4)

**External entry points:**
- TCODEs;
- RFC-enabled function modules;
- reports run as background jobs (SM36 variants are outside the repo);
- BAdI and user-exit implementations;
- OData service implementation classes (DPC_EXT), when present.

Rule: **no dead-code or "unreachable" claims for ABAP objects**, because callers live outside the repository. That is the COBOL lesson.

**Screens:**
- TCODE → program + dynpro number → dynpro screen page. Fields come from `.prog.xml` dynpro metadata **(Unverified structure)**.
- Flow logic `PROCESS BEFORE OUTPUT` / `PROCESS AFTER INPUT` → `MODULE` calls → PBO/PAI module symbols → their calls and table DML.
- The screen page (M3.3 builder `sap.py`) shows Purpose, User Actions (from the PAI `OK_CODE` `CASE` branches), Data (tables read/written) and Downstream Effects. This is the same section model as web screen specs.

## 4. Fiori / SAPUI5 (M7.5)

UI5 apps are JS + XML. The chain to extract:

```
manifest.json
  sap.app.dataSources       → OData service URI (+ annotations)
  sap.ui5.routing.routes    → pattern + target(s)
  sap.ui5.routing.targets   → viewName / viewPath  ─┐
                                                    ▼
webapp/view/<Name>.view.xml   controllerName="ns.controller.<Name>"
  control events: press="onSave", items="{/SalesOrderSet}"  (bindings)
                                                    ▼
webapp/controller/<Name>.controller.js   sap.ui.define([...deps], ...)
  onSave: function(){ this.getModel().create("/SalesOrderSet", ...) }
                                                    ▼
OData entity set → backend (in the same repo: SEGW DPC_EXT class / CDS / RAP; otherwise an external interface)
```

- **Routes → screen pages.** A route plus its target view is one screen. A Fiori launchpad tile/intent from `crossNavigation.inbounds` is the menu entry **(Unverified field names)**.
- **XML view events** (`press="onX"`, `change=`, `liveChange=`) → controller method `framework_binds` (markup-event edge, same as WebForms `OnClick`).
- **Bindings** `{/EntitySet}` and `{path: '/EntitySet'}`, plus controller `oModel.read/create/update/remove("/EntitySet…")` and OData V4 `bindList`/`bindContext`, produce `reads_entityset` / `writes_entityset` edges to OData entity-set nodes. Literal paths only; computed paths get hints.
- **Controller JS** is parsed with the existing JS parser. `sap.ui.define` dependency arrays become import edges.
- **Backend linking:** when the ABAP backend is in the same workspace, the entity set links to the DPC_EXT method (`<ENTITYSET>_GET_ENTITYSET`, `_CREATE_ENTITY`, …), following SEGW naming conventions **(Unverified)**. Otherwise it is an external interface page.

## 5. M7.1 spike: questions to answer (all Unverified)
1. Does a maintained tree-sitter ABAP grammar exist? What are its license, Python binding and parse rate on two real repos?
2. Confirm the abapGit file naming and XML structure for PROG/dynpro, FUGR, TRAN and TABL on pinned repos. Candidates are `abapGit/abapGit` and `abap2xlsx/abap2xlsx` (see `benchmark-protocol.md`).
3. Pick one public UI5 app, e.g. `SAP-samples/openui5-sample-app`. Confirm that the `manifest.json` routing and OData data-source fields exist as described.
4. Customer-namespace configuration, and how to treat `/NS/` objects.
5. Ground truth: hand-label about 50 call/DML edges per repo for the bench (`tests/corpus/external` pattern).

**Output:** `docs/roadmap/adr/0007-abap-parser-tier.md` (new) with the decision, plus pinned SHAs in `tests/corpus/external/manifest.json`.

## 6. Acceptance summary

| Item | Acceptance |
|---|---|
| M7.1 | ADR-0007 written; 2 ABAP repos and 1 UI5 repo pinned; questions 1–5 answered with evidence |
| M7.2 | Every abapGit object in the pinned repos becomes a typed inventory node; unknown types are counted, not dropped |
| M7.3 | Literal edges for every statement in §2. Dynamic forms produce no edge plus a hint. Table DML edges. Precision ≥0.9 on the hand-labelled sample (target, to be confirmed against M0.12 numbers). |
| M7.4 | TCODE entry points; dynpro screen pages with PBO/PAI links; no dead-code claims for ABAP |
| M7.5 | Route → view → controller → entity-set chain on the pinned UI5 app; bench rows added |
