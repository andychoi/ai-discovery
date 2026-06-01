# Architecture & Production-Readiness Audit

**Date:** 2026-05-31
**Auditor role:** Principal Architect / Staff Engineer — Enterprise Reverse-Engineering, AI/LLM systems, SDLC documentation platforms
**Audit basis:** `README.md` (29 KB) + `CLAUDE.md` (15 KB) vs. actual implementation (84 `.py` files, ~25,118 LOC)
**Method:** Five independent code-reading passes (parallel) + direct re-verification of every high-impact claim. Every finding is backed by a `file:line` citation. This is an architecture audit ("can a real customer run this in production?"), not a style review.

> **Central tension surfaced by this audit.** The codebase is a dual-layer design: a **deterministic fact layer** (parser → AST → endpoint/entity tables) and a **non-deterministic prose layer** (LLM-generated narrative). The fact layer is solid. The safeguards that are supposed to validate the prose layer — grounding, confidence scoring, self-review — are **broadly bypassed or stubbed**. The gap between output that *looks* trustworthy and output that is *actually verified* is the core product risk.

---

## 1. README / CLAUDE Consistency

| Issue | Severity | Evidence | Fix |
|-------|----------|----------|-----|
| **Phase 2 (`screen_llm_specs`) undocumented** — code runs it first (before phase 5) and checkpoints it, but neither README's phase table (5–19) nor CLAUDE.md mentions it | Medium | `pipeline.py:54-71` defines `2: screen_llm_specs`; run at `:455-519` | Add Phase 2 row to README phase table |
| **"Stored procedures" overstated** — README:456 and the screen-centric spec doc mark it "✅ fully implemented", but there is **no PL/SQL / package / trigger parsing code** | **High** | `sql_extractor.py:33-64` = 6 regexes (CREATE TABLE/INSERT/UPDATE/SELECT/ALTER/VIEW) only. `grep procedure\|PL/SQL\|trigger` → 0 hits. Own docs admit "Phase 5+" (`docs/specs/2026-04-20-state-first-backbone-plan.md:166`) | Remove the SP claim or mark "roadmap" |
| **"Kafka/MQ data lineage" overstated** — README:540-552 diagram implies event-flow tracing; reality is producer boundary tagging + grep-based consumer labels, no producer↔consumer correlation | **High** | `external_system_extractor.py:53-63` (producers only); consumers via `screen_mapper.py:1037` grep. `event_consumer` node_type declared at `call_graph.py:426` but **no parser ever emits it** (dead branch) | Constrain diagram to "outbound boundary detection only" |
| **"Multi-Framework menu detection" overstated** — README:528-534 claims TS const / Vue / React / Angular / server-side; all are stubs or absent. Only JSON/YAML menus work | **High** | `menu_detector.py:235-237` (TS `return items=[]`), `:276/:291/:304` (`pass` → `None`). No server-side detector class exists | Correct README to "JSON/YAML menu files (others: experimental/stub)" |
| **`test-llm` command undocumented** | Low | `cli.py:615` implemented, not in README | Add to CLI table |
| **`tier3` model description mismatch** — README:115 table says "Tier3 = Haiku(dev)/Sonnet(prod)" but discovery.yaml sample (:228) sets tier3d=Sonnet | Low | `config.py:164-180` | Reconcile table/sample |

**Consistent (good):** Phases 5–19 numbers/names/order match the code **exactly** (`pipeline.py:54-71`). All 11 CLI commands are implemented (`cli.py`). Absence of phases 1/3/4 is an intentional number migration (`db.py:432-441`), not a bug.

---

## 2. Missing-Requirements Analysis

| Missing capability | Why it's needed | Why the current design falls short | Difficulty | Priority |
|--------------------|-----------------|------------------------------------|------------|----------|
| **Interface→Impl resolution (DI)** | Real Spring/.NET fields are interface-typed; without this, the whole service layer goes unresolved | Stage 3 only matches `{declared-type}.{method}` by name → stops at the (bodyless) interface node `call_graph.py:210-215`. No bean/qualifier or `AddScoped<I,T>()` modeling | Medium | **P0** |
| **Stored Procedure / PL/SQL analysis** | In Oracle-SP-centric legacy systems, 70 %+ of business logic lives in the DB | Inline-DML regex only (`sql_extractor.py`). No CALL/EXEC/package/trigger | High | **P0** (if target is SP-centric) |
| **Cross-service event lineage (Kafka/MQ)** | Core data flow in MSA environments | Correlator matches HTTP path only (`integration_correlator.py:80-93`). No topic matching | Medium | **P1** |
| **Non-literal API URL resolution** | Real FE uses `${baseURL}/${path}` | `_extract_target` returns `""` for non-literals `external_system_extractor.py:91-100`; `_extract_api_calls` forces a literal `/api/` `screen_mapper.py:734` | Medium | **P1** |
| **Legacy UI (JSP/ASPX/WebForms)** | Most screens in .NET ERP / legacy Java | `file_walker.py:10-15` never walks `.jsp/.aspx/.ascx` | High | **P1** (P0 depending on target) |
| **RBAC / permission inference** | Screen spec has a "Permissions & Roles" section | No `@PreAuthorize/[Authorize]`/route-guard parsing; writer hardcodes `permissions=[]` `screen_doc_writer.py:124` → section always empty | Medium | **P1** |
| **Screen-to-screen navigation inference** | "Downstream Effects (screens)" is promised | `related_screens` field exists but **no code populates it**; no router-link extraction → LLM guesses | Medium | **P2** |
| **Multi-repo dependency resolution (build deps)** | 20-MSA environments | Federation is artifact-level merge only (name+field Jaccard). Call graph is intra-repo | High | **P2** |

---

## 3. Architecture-Risk Analysis

### A. Scalability — **P0 risk**
- **Root cause:** the entire call graph is one in-memory list, and each `CodeNode` carries the **full `source_code` string** (`java.py:193,251`). `build_call_graph(all_nodes)` is single-threaded (`pipeline.py:713`).
- **Scenario:** 500k methods × average source → tens of GB of RAM. **Guaranteed OOM.**
- **Additional hotspots:** on short-name collisions (`save`/`execute`/`handle`), `_resolve_contextual_targets` **fans out a 0.6-confidence edge to every candidate** (`call_graph.py:357`) → quadratic edge blow-up. `_suffix_matches` is O(N) per dotted unresolved call (`call_graph.py:378-387`). The project's own plan flags this fan-out as a defect (`docs/specs/2026-04-20-state-first-backbone-plan.md:37`).
- **Fix:** (1) detach source from nodes into a disk/DB reference, (2) back the call graph with SQLite or shard it, (3) cap short-name fan-out + collapse to a single "ambiguous" node.

### B. Accuracy — **P0 risk**
- **Root cause:** there is **no structural check** that the file:line / symbols in Tier-3 prose actually exist in the parsed graph. All prose outside the AST fact tables (endpoints/entities) is free-form LLM generation.
- **Scenario:** the LLM plausibly describes non-existent endpoints/entities/classes → BAs trust them. Self-review is budget-skippable and samples only 50 claims (`self_review.py:208`) via LLM judgment.
- **Fix:** add a deterministic membership check of cited symbols against `code_nodes` (auto-flag symbols that don't exist).

### C. Cost — **P1 risk**
- **Three root causes:** (1) Tier-3 fans out (domain × doc_type) **without a cap** at 16-wide concurrency (`rollup.py:578-657`); (2) `doc_generation` is `complexity="high"`, so it **always escalates to the advisor**, and the cost gate (`max_advisor_cost_pct=0.5`) is **bypassed** at `:265-266` → **every doc becomes Opus-on-Opus, two calls**; (3) self-review runs an extract + 50-verify + regen + full re-verify loop per doc.
- **Budget limitation:** `_budget_ok` checks **only at phase boundaries** (`pipeline.py:1123` etc.); within a phase it's unbounded. Non-Bedrock providers are never checked (`:214-215`). A single phase can substantially overshoot the limit.
- **(+) Good:** embeddings are content-hash incremental (`embedder.py:166-222`); context is capped (40 nodes / 50 edges / 800-char snippets).
- **Fix:** in-phase token-accumulation callback + apply the advisor cost gate to `high` too + cap the doc fan-out.

### D. Reliability — **P1 risk**
- **Critical silent success:** even if Tier-3 rollup fails entirely, the exception is swallowed (`rollup.py:651-652`) and phase 14 is recorded complete (`pipeline.py:1255`) → **exit 0, ASIS/ASD/ASSC missing**. This is exactly the mode CLAUDE.md warns about. There is no "0/N succeeded = fatal" guard.
- **Partial resume:** the inline 8a/8b block (FSM·BPMN·DMN·EARS, `pipeline.py:842-1028`) is not checkpointed → on resume into phase 9+ it is **silently skipped**.
- **(+) Good:** Tier-1/3/embed have per-item data-driven resume (`summarizer.py:200-225`). WAL + `synchronous=NORMAL` keeps DB-corruption risk low.
- **Fix:** non-zero exit when all rollups fail + promote 8a/8b to a formal phase.

### E. Maintainability — **P2 risk**
- **Root cause:** confidence scores are magic literals scattered across `call_graph.py` (0.95/0.93/0.9/0.85…), violating the CLAUDE.md principle ("heuristics documented in decisions.md"). The dead vendored `shared/llm_router.py` imports a non-existent `shared.db.connection` (`:198,216,460`).
- **Fix:** named constants + a single table for confidence; delete `llm_router.py`.

---

## 4. AI/LLM Design Validation

**Tier structure:** only Tier-1 receives the **actual source** (`summarizer.py:77-79`). **Tier-2 has no grounding** — it gets Tier-1 summaries + graph metadata, no source/RAG (`flow_analyzer.py:228-362`). Tier-3 gets summaries + flows plus **best-effort RAG, ≤5×800 chars** (silently `""` on failure, `rollup.py:184-186,462-466`). So **all prose outside the AST fact tables is "summary of summaries"** = a hallucination-amplification path.

**Self-review limits:** it does not re-read source from disk; it does a **RAG index search, then asks an LLM to grade itself** (`self_review.py:132-173`). A contradicted claim is not deleted — it's counted + flagged + regenerated. The CRIT-3 fix (no evidence → cannot be "verified") **is confirmed present** (`self_review.py:24,137-139,182-190`; `flow_analyzer.py:96-100`).

**Confidence design defect:** the initial value is **LLM self-reported** (`rollup.py:389-395`, the prompt asks the model to write "Confidence: X.XX"). If self-review runs, a deterministic blend overwrites it (AST-row + verdict weighting, `rollup.py:400-425`). **But if self-review is budget-skipped, the LLM's self-rated number (default 0.7) is published as-is.** The vector similarity score is **never used as a numeric signal** (only a >1.0 binary gate).

**JSON parsing:** the core doc tiers **bypass** the schema-enforced `invoke_structured` (`llm_client.py:154-210`) and parse free text manually. On parse failure, flow_analyzer **silently drops a whole domain's flows to `[]`** (`flow_analyzer.py:223-225`). No retry.

**Prompt caching:** **none** (verified by grep). The same domain block is re-sent uncached across all three doc_types (`rollup.py:532-538`).

### Trust rating: **48 / 100**

**Rationale:**
- (+) The endpoint/entity fact tables are parser-derived, deterministic, trustworthy (`rollup.py:83-159`). Row-level `file:line` citations exist.
- (−) Everything else — **prose is weakly grounded, inventable, and carries self-graded confidence.** Tier-2 is fully ungrounded. Self-review is an LLM grading an LLM and is budget-skippable.
- (−) The confidence number may be the LLM's self-report → the trust signal itself is untrustworthy.
- **Conclusion:** useful if a BA/PM treats it as **"tables (endpoint/schema) = trust, prose = pre-verification draft."** Not sufficient to trust the prose as authoritative. Output that has not gone through `/discover-triage` is unfit for stakeholder submission.

---

## 5. Screen-Centric Design Validation

**The philosophy ("Screen = Entry Point") is sound, but the implementation depends on one menu-file format.**

| Item | Current state | Evidence |
|------|---------------|----------|
| Fit for large ERP/WMS/MES | **Unfit** — only JSON/YAML static-menu leaves become screens | `menu_detector.py:342-370` |
| Screens without menus (server-rendered / permission-built) | **Entirely undetected**, silently returns 0 screens then clean exit | `pipeline.py:463-464` |
| Popups / modals | Undetected | no AST/component scan |
| Wizards (multi-step) | Undetected | same |
| Dynamic routing | **Stub** (Vue/React/Angular all `pass`) | `menu_detector.py:276,291,304` |
| Role-based screens | No permission inference; `permissions=[]` hardcoded | `screen_doc_writer.py:124` |
| Screen→Backend mapping | Filename guessing + regex, **not data-flow**; repo-global ETL/interfaces attached to every screen | `screen_mapper.py:576-640, 1050-1184` ("skeleton" comment `:580`) |
| Drift | SHA256 compare works, but **only a shallow 1-hop file set** is hashed → transitive/shared/entity changes are missed | `screen_mapper.py:597-637` |

**Remediation:** (1) when no static menu exists, fall back to extracting screens from route/component AST; (2) connect screen→backend via a real call-graph slice (replacing string heuristics); (3) fill RBAC from a `@PreAuthorize`/route-guard parser; (4) expand the drift hash set to the call-graph transitive closure.

---

## 6. Brownfield Analysis-Quality Validation

Evidence-based estimates. Accuracy is measured against "endpoint/entity facts the parser captures" and "call-graph resolution rate."

| System type | Estimated accuracy | Primary failure factor (evidence) | Remediation |
|-------------|--------------------|-----------------------------------|-------------|
| **Java Spring Monolith** | endpoint/entity ~85 %, **call graph ~55 %** | interface-typed @Autowired unresolved (`call_graph.py:210-215`); interface→impl broken | add bean/qualifier resolution |
| **.NET ERP** | ~70 % | DI container (`AddScoped<I,T>`) unparsed (`csharp.py:548-562`); WebForms (.aspx) not walked | parse container registration + .aspx support |
| **React + Node** | endpoint ~75 %, screen mapping ~40 % | non-literal URL dropped (`screen_mapper.py:734`); functional DI out of scope (`javascript.py:471`) | resolve variable URLs |
| **Legacy JSP** | **~10 %** | `.jsp` not walked (`file_walker.py:10-15`); handlers/scriptlets invisible | new JSP parser |
| **Legacy ASP.NET WebForms** | **~10 %** | `.aspx/.ascx` unsupported; controller-annotation only | WebForms code-behind parser |
| **Oracle-SP-centric** | **~15 %** | no SP/PL-SQL/trigger parsing (`sql_extractor.py`), most business logic uncaptured | PL/SQL parser (high difficulty) |

**Key point:** for framework-conventional single-language Spring/FastAPI apps (the case README claims "~95 %"), the fact tables are good, but the **call-graph resolution rate is markedly lower than claimed because of the interface-DI defect.** Accuracy collapses as you move toward legacy/SP/MSA.

---

## 7. Implementation Code Audit (evidence-based)

| Item | Finding | Evidence |
|------|---------|----------|
| **Silent-failure hotspot** | **13 `except: pass`** in `screen_mapper.py` (no log/warn) → services/tables dropped with no user signal | `screen_mapper.py:187,327,412,436,470,500,529,806,911,951,969,1045,1181` |
| **Silent success (worst)** | rollup fails entirely → exit 0, empty docs | `rollup.py:651-652` + `pipeline.py:1255,1597` |
| **Dead code** | vendored `llm_router.py` imports a missing module, unused by CLI | `shared/llm_router.py:198,216,460` |
| **DB idempotency defect** | `call_edges` plain INSERT + no UNIQUE → re-running phase 7 on the same scan_id duplicates edges | `pipeline.py:802`, `db.py:62-70` |
| **Race (low severity)** | `_track_cost` does non-atomic multi-field updates on a shared dict, zero locks | `llm_client.py:524-533` |
| **N+1** | **None** (in-memory list ops, batched IN queries) | clean |
| **Resource leak** | **None** (all sqlite conns closed, `with` open) | clean |
| **Unnecessary LLM calls** | forced advisor per doc (Opus ×2), 50-claim self-review loop | `rollup.py:471-474`, `self_review.py:208` |
| **Hardcoding** | `/tmp/` paths (Windows-incompatible) | `ingest/runner.py:269,291` |
| **Tests** | 75 test files / 840 tests collected. No dedicated unit tests for `screen_mapper.py` (~1200 LOC) or `drift_checker.py` (integration-only) | `pytest --collect-only` |

**Clean areas:** README↔CLI 100 % parity, zero bare `except:`, centralized model IDs (`model_defaults.py:40-44`), WAL transaction safety.

---

## 8. Top 20 Riskiest Problems

| Priority | Problem | Impact | Fix |
|----------|---------|--------|-----|
| **P0** | rollup fails entirely → exit 0 + empty docs | operator never notices failure | "0/N success = fatal" guard, non-zero exit (`rollup.py:651`/`pipeline.py:1255`) |
| **P0** | interface→impl unresolved → Spring/.NET call graph ~55 % | core accuracy defect | bean/qualifier + DI container resolution (`call_graph.py:210-215`) |
| **P0** | 500k-method in-memory OOM + short-name fan-out quadratic | crashes on large customers | detach source + SQLite backing + fan-out cap (`call_graph.py:357`) |
| **P0** | no structural symbol check on prose → invented endpoints/entities | document trust collapse | `code_nodes` membership check |
| **P0** | stored procedure unimplemented yet advertised as "implemented" | sales expectation mismatch | correct docs + roadmap a PL/SQL parser |
| **P1** | Tier-2 fully ungrounded | flow-prose hallucination | inject RAG into Tier-2 (`flow_analyzer.py:228`) |
| **P1** | confidence is LLM self-report (when self-review skipped) | the trust metric is itself untrustworthy | derive from deterministic signals only (`rollup.py:389`) |
| **P1** | budget checked only at phase boundaries, unbounded within | cost explosion | in-phase token callback (`pipeline.py:1123`) |
| **P1** | every doc forces advisor (Opus ×2), cost gate bypassed | 2×+ cost | apply gate to `high` too (`llm_client.py:265`) |
| **P1** | 13× silent except in screen_mapper | incomplete output, no warning | logging + surface partial failure |
| **P1** | 4 menu detectors stubbed (TS/Vue/React/Angular) | screen mode is effectively JSON-only | implement route-AST parsing (`menu_detector.py:235`) |
| **P1** | RBAC inference = 0 + `permissions=[]` hardcoded | screen-spec permissions section always blank | `@PreAuthorize`/guard parser |
| **P1** | non-literal URL dropped | real FE mapping fails | resolve variable URLs (`screen_mapper.py:734`) |
| **P1** | JSP/ASPX not walked | legacy screens invisible | legacy parser |
| **P1** | call_edges duplicates (no UNIQUE) | graph pollution on resume | add UNIQUE constraint (`db.py:62`) |
| **P2** | inline 8a/8b uncheckpointed, skipped on resume | FSM/BPMN silently missing | promote to a formal phase (`pipeline.py:842`) |
| **P2** | zero prompt caching | 30 %+ wasted Bedrock cost | introduce cachePoint |
| **P2** | flow JSON parse failure → whole domain `[]` | silent data loss | use invoke_structured (`flow_analyzer.py:223`) |
| **P2** | Kafka/MQ lineage unimplemented yet diagrammed | expectation mismatch | constrain docs + implement topic correlation |
| **P2** | `_track_cost` race + dead llm_router | cost miscount / confusion | add lock, remove dead module |

---

## 9. Final Verdict

| Dimension | Score | Rationale |
|-----------|------:|-----------|
| Architecture | 72 | Deterministic+LLM dual structure, phases, resume design are solid. In-memory graph scaling limit and missing prose validation drag it down |
| Scalability | 38 | Guaranteed OOM at 500k methods, quadratic fan-out, single-threaded graph build |
| Accuracy | 52 | Endpoint/entity facts good, but call graph ~55 %, prose inventable, SP/legacy ~10 % |
| Maintainability | 70 | Good module separation, 840 tests, centralized model IDs. Magic numbers, dead code, silent excepts deduct |
| Cost Efficiency | 55 | Incremental embedding and context caps are excellent. Forced advisor, no caching, budget granularity deduct |
| Documentation Quality | 60 | Rich, but **overstated SP/Kafka/menu-multiframework claims** undermine trust |
| Production Readiness | 45 | Silent success (exit 0 empty docs), stub-as-advertised, scaling limits block operations |

### Overall grade: **B−**

The design philosophy and fact-layer engineering are B+ grade, but the grade is pulled down by **(1) the gap between advertising and implementation (SP / Kafka / menu multi-framework), (2) the silent-success reliability defect, (3) scalability limits, and (4) absent prose validation.**

### "Is it sellable to a customer right now?" → **CONDITIONAL YES**

**Sellable if you narrow the sales scope (YES):**
- ✅ Single-language, framework-conventional, small-to-mid scale (≤2000 classes) Spring/FastAPI/Express apps + **fact-table-centric** deliverables (endpoint/schema) + `/discover-triage` post-processing assumed → usable in practice.

**Not sellable as-is (NO):**
- ❌ **Large (100k+ classes) / 20-MSA** — OOM / scaling limits.
- ❌ **Oracle-SP-centric / legacy JSP/WebForms ERP** — accuracy ~10–15 %.
- ❌ **Stakeholder submission** where prose docs must be trusted as authoritative without verification — confidence itself is untrustworthy.
- ❌ **Unattended operational pipelines** — total rollup failure is hidden behind exit 0, dangerous for unattended runs.

**Must-fix before release (5 × P0):** silent-success guard, interface→impl resolution, scalability (detach source + fan-out cap), prose symbol validation, and **immediate correction of README's SP/Kafka/menu overstatements** (no code change, doable today, and the highest legal/trust risk).

---

## Highest-ROI Starting Points

1. **README correction** — zero code, maximum trust recovery. Remove/qualify the stored-procedure, Kafka-lineage, and multi-framework-menu claims.
2. **Rollup silent-success guard** — ~10 lines, eliminates the worst operational risk (`rollup.py:651` / `pipeline.py:1255`).

---

*Audit appendix: all findings were gathered by five parallel evidence-gathering passes over pipeline/reliability, LLM/AI design, parsers/call-graph/scalability, screen-centric design, and code quality, then independently re-verified against source. No claim in this document is speculative; each is anchored to a `file:line`.*
