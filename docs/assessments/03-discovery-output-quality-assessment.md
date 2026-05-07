# Discovery Output Quality — Improvement Plan

**Date:** 2026-05-07
**Scope:** Fix accuracy, completeness, and structure of `discover scan` output, evaluated on the Java Spring Boot e-commerce corpus at `data/java-springboot-ecommerce-application/` against generated docs in `data/java-springboot/`.
**Status:** Plan only — no code changes yet.

---

## 1. Context

The current scan output for the Spring Boot corpus has four artifact families: `ASIS/`, `ASD/`, `ASSC/`, `PF/` (22 process flows). Auditing these against the source code reveals three classes of problems:

1. **Accuracy** — the ASIS/ASD endpoint table is largely hallucinated. Almost every non-GET endpoint path is wrong (`/user/register` vs actual `/users/signup`; `/order/place` vs `/order/add`; `/cart/{id}` DELETE vs `/cart/delete/{id}`; etc.). The Java parser extracts the correct routes; they just aren't reaching the LLM as verified facts.
2. **Completeness** — Phase 3 generators (`bpmn_generator.py`, `dmn_generator.py`, `ears_generator.py`) ship in `src/ai_discovery/generators/` but their output is missing from `data/java-springboot/`. No BPMN, no DMN decision tables, no EARS requirements, no entity-impact docs, no cross-artifact link graph. ~40% of the documented Phase 3 capability is absent.
3. **Structure** — output filenames are 60–80 chars long with redundant prefixes/suffixes (project + "scenario-" + name + step-id + "-process-flow.md"), the data root folder isn't named after the project, scenario step-ids collide across scenarios (`signin-42` and `addtocart-42`), and confidence is reported as a single global 0.7 / 1.0 with no per-claim attribution.

Outcome of this plan: a follow-up scan should produce accurate, complete, well-named, cross-linked output that a reader can trust at the row level.

---

## 2. Findings

### 2.1 Accuracy bugs

| # | Bug | Evidence | Severity |
|---|---|---|---|
| A1 | Endpoint paths hallucinated (~10 of 20 wrong) | ASIS table claims `/user/register`, `/product/{id}` PUT, `/order/place`, `/cart/{id}` DELETE; controllers actually use `/users/signup`, `/product/update/{id}` POST, `/order/add`, `/cart/delete/{id}` | **Critical** — readers using this as API doc will fail every call |
| A2 | HTTP verbs inferred from REST convention, not source | `ProductController.updateProduct` is `POST /product/update/{id}` not `PUT /product/{id}`; `CategoryController.updateCategory` same pattern | **Critical** |
| A3 | Endpoint base prefix inconsistency | ASIS uses `/user/...`; actual `@RequestMapping` is `/users` | **High** |
| A4 | `placeOrder` documented as atomic, code is not `@Transactional` | `OrderController.placeOrder` has no transaction boundary; PF `placeorder-48` doc treats Order + N OrderItems + cart-clear as atomic | **High** — misleads reliability planning |
| A5 | `Wish` schema annotated `@OneToOne` to `User` but used as 1:N | ASSC flags this but doesn't separate "current annotation" from "intended cardinality" | **Medium** |
| A6 | `WishtNotExistException` typo propagated as a documented exception name | ASD references the typo class verbatim | **Low** — but indicates passive copy from source rather than verification |

### 2.2 Completeness gaps

| # | Missing artifact | Why it should exist | Source of truth |
|---|---|---|---|
| C1 | No BPMN diagrams in output | `BPMNGenerator` ships in `src/ai_discovery/generators/bpmn_generator.py` and is called by `pipeline.py:763–768` writing `entity_backbone.mmd` | Phase 3 work shipped 2026-04-21 (per memory) |
| C2 | No DMN decision tables | `dmn_generator.py` called at `pipeline.py:773–778` writing `entity_decisions.md` | Same |
| C3 | No EARS requirement docs | `ears_generator.py` called at `pipeline.py:783–788` writing `entity_ears.md` | Same |
| C4 | No entity-impact analysis docs | Phase 3 entity-impact query exists; no per-entity blast-radius doc in output | Phase 3 |
| C5 | No cross-artifact `links_to` graph | All 22 PF docs have `links_to: []`; no scenario→use-case→entity traceability | `doc_generator.py:138–148` only links within-domain and as-is↔as-is |
| C6 | No per-claim source citations | No `file:line` references in any artifact, despite the Java parser tracking line numbers | Trust signal |
| C7 | Missing e-commerce flows: logout, password-reset, email-confirm, refunds, payment webhooks, order cancellation | Genuinely absent in source code (not all of these), but the gap should be **explicit** in ASIS rather than silent | Reader expectation |
| C8 | `addresourcehandlers` and `main` treated as scenarios | These are bootstrap/config, not business flows | Scenario classifier weakness |

### 2.3 Structural / UX issues

| # | Issue | Evidence |
|---|---|---|
| S1 | Filenames overlong & redundant | `java-springboot-scenario-addresourcehandlers-20-process-flow.md` (62 chars). All four parts (`java-springboot-`, `scenario-`, `-20`, `-process-flow`) duplicate context already given by folder + scenario name |
| S2 | Numeric scenario index collides | `signin-42-process-flow.md` and `addtocart-42-process-flow.md` both `42`. The number is a step-counter from upstream, not a unique scenario id |
| S3 | Output root folder bare-named | `data/java-springboot/` doesn't say "this is discovery output" or which project; should be `output-{project-or-folder-name}/` |
| S4 | Confidence is global, not per-claim | ASIS reports `confidence: 0.7` for the whole doc; reader can't tell which rows are verified vs. inferred |
| S5 | Self-review section flags "unverified claims" but score doesn't decay | ASIS says "5 unverified claims" yet still posts 0.7 |
| S6 | No cross-references between artifacts despite Phase 3 federation work | `doc_generator.py` only emits `links_to: ["{domain}-as-is"]` from `as-is-detail`; PF→use-case, PF→entity, PF→source-file all missing |

---

## 3. Improvement Plan

Five tracks, ordered by leverage (highest reader-trust impact first). Each track names exact files to modify.

### Track 1 — Inject verified endpoint facts into the LLM prompt **(fixes A1, A2, A3)**

**Root cause:** `src/ai_discovery/parsers/java.py:227–228` correctly stamps `m_hints["method"]` and `m_hints["route"]` on endpoint nodes; `src/ai_discovery/ai/chunker.py:83` preserves `framework_hints`; but `src/ai_discovery/ai/rollup.py` only mentions endpoints in prose ("All public endpoints with HTTP methods and paths" — line 51) — it never injects the verified route/method strings as a structured, non-overridable facts block.

**Changes:**
1. In `src/ai_discovery/ai/rollup.py`, before LLM call, walk chunks for `framework_hints` containing `route` + `method` and assemble a `## Verified API Surface` block:
   ```
   | Controller.Method | HTTP | Path |
   |---|---|---|
   | UserController.register | POST | /users/signup |
   | UserController.signIn   | POST | /users/signin |
   ...
   ```
   Inject this into the prompt under a heading the system message names as **non-overridable** ("the following facts are extracted from source AST; never modify HTTP verbs or paths; if a row is missing reproduce it verbatim, do not invent rows").
2. In `src/ai_discovery/generators/templates/as-is.md.j2` (and `as-is-detail.md.j2`), render the same verified table directly from rollup context — bypass the LLM for the API table entirely. The LLM still writes prose around it, but the table is deterministic.
3. Apply the same pattern for entity tables (entity name, fields, types, FK relations) — `as-is-schema.md.j2` should render from parser-extracted entity metadata, not LLM prose.

**Files:** `src/ai_discovery/ai/rollup.py`, `src/ai_discovery/generators/templates/as-is.md.j2`, `src/ai_discovery/generators/templates/as-is-detail.md.j2`, `src/ai_discovery/generators/templates/as-is-schema.md.j2`.

**Verification:** Re-scan corpus; diff the resulting endpoint table against `grep -rn "@.*Mapping" data/java-springboot-ecommerce-application/src/main/java/com/sena/tecmiecommercebackend/controller/` — every endpoint row must match a real annotation.

---

### Track 2 — Wire Phase 3 generators into the published output tree **(fixes C1, C2, C3, C4)**

**Root cause:** `pipeline.py:763–788` writes `entity_backbone.mmd`, `entity_decisions.md`, `entity_ears.md` to the **scan** `output_dir` (`Path(output) / project_slug`, per `cli.py:265`), but the published `data/java-springboot/` tree only contains the DocHub-folder docs (ASIS/ASD/ASSC/PF). The Phase 3 artifacts never get copied/published.

**Changes:**
1. Add new DocHub-style folder prefixes to `_DOC_TYPE_PREFIXES` in `src/ai_discovery/generators/doc_generator.py:20–41`:
   - `entity-backbone` → `BPMN/`
   - `entity-decisions` → `DMN/`
   - `entity-ears` → `EARS/`
   - `entity-impact` → `IMPACT/`
2. In `src/ai_discovery/pipeline.py`, after the Phase 3 generators write their raw output, also emit a `RollupResult`-shaped record per entity (per BPMN lane, per DMN table, per EARS requirement set) so they flow through the unified `write_docs` path with proper filenames + frontmatter + confidence.
3. Per-entity decomposition for BPMN/DMN/EARS:
   - One BPMN file per **business actor lane** (per memory: lanes are actors, not entities; all entity kinds appear in the BPMN). Filename: `{actor-slug}.md` under `BPMN/`.
   - One DMN file per **decision-bearing entity** (any entity with guarded transitions). Filename: `{entity-slug}.md` under `DMN/`.
   - One EARS file per **entity** with state transitions. Filename: `{entity-slug}.md` under `EARS/`.

**Files:** `src/ai_discovery/generators/doc_generator.py`, `src/ai_discovery/pipeline.py:763–788`, plus three new templates `bpmn.md.j2` / `dmn.md.j2` / `ears.md.j2` under `src/ai_discovery/generators/templates/`.

**Verification:** Re-scan; assert `BPMN/`, `DMN/`, `EARS/` folders exist with at least 1 `.md` file each, and that each file has valid frontmatter and is referenced from at least one PF or ASD doc via `links_to`.

---

### Track 3 — Restructure output folder + filenames (domain-grouped scheme) **(fixes S1, S2, S3)**

**Root cause:** Two places construct paths:
- `src/ai_discovery/cli.py:265` — `output_dir = Path(output) / project_slug` (root folder name)
- `src/ai_discovery/generators/doc_generator.py:55–56, 246–247` — `_make_doc_id(project_slug, domain, doc_type)` and the scenario variant `_slugify(f"{project_slug}-{scenario_slug}-process-flow")` (filename construction)

**Changes:**
1. **Root folder** — `cli.py:265`: change to `output_dir = Path(output) / f"output-{project_slug}"`. The `output-` prefix makes the folder self-identifying when copied/extracted.
2. **Domain-grouped scenario filenames** — `doc_generator.py:246–247`: change scenario filename construction so PF/ files are `{domain-slug}-{scenario-slug}.md` (no project prefix, no `scenario-` prefix, no step-counter, no `-process-flow` suffix). Folder `PF/` already implies process-flow; domain prefix groups related scenarios alphabetically.
   - Before: `java-springboot-scenario-addtocart-42-process-flow.md`
   - After:  `cart-addtocart.md`
3. **ASIS/ASD/ASSC filenames** — `doc_generator.py:55–56, 159–161`: drop project prefix. Folder is already typed (`ASIS/`, etc.), and the new root folder names the project. Use `{domain-slug}.md`.
   - Before: `ASIS/java-springboot-sena-as-is.md`
   - After:  `ASIS/sena.md`
4. **Step-counter collision** — `doc_generator.py:246`: drop the numeric component from `scenario_slug` derivation. If two scenarios genuinely share a name, append a stable disambiguator (entry-point file path hash, not step count).
5. **Filter non-business scenarios** — `addresourcehandlers` and `main` should not produce PF docs. Add a denylist of bootstrap-scenario names (or a positive filter requiring the scenario to traverse a controller → service → repo path) in the upstream `flow_analyzer.py` or `flow_clustering.py`. Files: `src/ai_discovery/ai/flow_analyzer.py` or `src/ai_discovery/ai/flow_clustering.py` (whichever currently emits scenarios).

**Target layout after re-scan:**
```
data/output-java-springboot-ecommerce-application/
├── ASIS/sena.md
├── ASD/sena.md
├── ASSC/sena.md
├── PF/
│   ├── cart-addtocart.md
│   ├── cart-deletecartitem.md
│   ├── cart-getcartitems.md
│   ├── cart-updatecartitem.md
│   ├── category-createcategory.md
│   ├── category-getcategories.md
│   ├── category-updatecategory.md
│   ├── order-checkoutlist.md
│   ├── order-getallorders.md
│   ├── order-getorderbyid.md
│   ├── order-placeorder.md
│   ├── product-createproduct.md
│   ├── product-getproducts.md
│   ├── product-updateproduct.md
│   ├── user-findalluser.md
│   ├── user-register.md
│   ├── user-signin.md
│   ├── wishlist-addtowishlist.md
│   ├── wishlist-deletewish.md
│   └── wishlist-getwishlist.md
├── BPMN/{actor}.md       # new
├── DMN/{entity}.md       # new
├── EARS/{entity}.md      # new
└── IMPACT/{entity}.md    # new
```

**Files:** `src/ai_discovery/cli.py:265`, `src/ai_discovery/generators/doc_generator.py:55–56, 159–161, 246–247`, `src/ai_discovery/ai/flow_analyzer.py` or `flow_clustering.py` (scenario filter).

**Verification:** Re-scan; `ls data/output-java-springboot-*/PF/ | wc -l` returns scenario count without `addresourcehandlers`/`main`; no filename exceeds 40 chars; no two filenames in `PF/` share a prefix-up-to-domain that suggests collision.

---

### Track 4 — Per-claim confidence with source citations **(fixes A4, S4, S5, C6)**

**Root cause:** `RollupResult.confidence` is a single float; templates render it once at the doc level. Self-review (`src/ai_discovery/ai/self_review.py`) categorizes claims as verified/contradicted/unverified but the count only appears in a footer — it doesn't decay the confidence score nor attach to specific rows.

**Changes:**
1. Extend the rollup data model so each emitted **row** (endpoint, entity field, business rule, scenario step) carries `{value, source: file:line, confidence: 0..1, status: verified|inferred|llm-prose}`. Files: `src/ai_discovery/ai/rollup.py` (data shape), `src/ai_discovery/ai/self_review.py` (verification pass).
2. Templates render confidence as a column or inline badge:
   ```
   | Controller.Method | HTTP | Path | Source | Conf |
   |---|---|---|---|---|
   | UserController.signIn | POST | /users/signin | UserController.java:34 | ✓ 1.00 |
   ```
3. Doc-level confidence becomes a **weighted aggregate** of row confidences (weighted by criticality — endpoints/entities high, prose summaries low) instead of an LLM-asserted single number.
4. Self-review must **decay** the doc score when contradictions or unverified critical claims exist. If `placeOrder` is documented atomic but no `@Transactional` is found, the row gets `status: contradicted`, `confidence: 0.3`, and a callout: "⚠ Source contradicts: `OrderController.java:31` — no @Transactional".

**Files:** `src/ai_discovery/ai/rollup.py`, `src/ai_discovery/ai/self_review.py`, `src/ai_discovery/generators/templates/*.j2`.

**Verification:** Re-scan; spot-check 10 rows across artifacts — every row must have a `file:line` source citation, and any row claiming behavior contradicted by source must be flagged. Run `discover query "SELECT ... FROM doc_row WHERE confidence < 0.65 AND criticality = 'high'"` to surface the audit list.

---

### Track 5 — Cross-artifact `links_to` graph **(fixes C5, S6)**

**Root cause:** `src/ai_discovery/generators/doc_generator.py:120–125` (`_WITHIN_DOMAIN_LINKS`) only encodes `as-is-detail → as-is` and `as-is-schema → as-is`. PF scenarios always emit `links_to: []` (line 256). There's no traceability from scenario → use-case → entity → source-file.

**Changes:**
1. In `doc_generator.py`, extend the link graph computation:
   - PF doc → ASD use-case (resolve by scenario_id ↔ UC-N matching)
   - PF doc → ASSC schema (resolve by entities touched in scenario, from `flow.entities_touched` or similar)
   - PF doc → BPMN actor lane (resolve by actor of scenario trigger)
   - PF doc → EARS requirement (resolve by transitions in scenario)
   - ASD use-case → PF doc (reverse of above)
   - ASSC entity → ASD business rule (resolve by rule subject)
2. Render a "Related Documents" section in every template that consumes `links_to`, showing each link with its type (use-case, schema, BPMN-actor, etc.) so a reader can navigate.
3. Persist the link graph to the discovery DB (new table `doc_link(src_doc_id, dst_doc_id, kind)`) so `discover query` and `discover impact` can traverse it.

**Files:** `src/ai_discovery/generators/doc_generator.py:118–148`, `src/ai_discovery/generators/templates/*.j2` (Related-docs partial), `src/ai_discovery/state/db.py` or wherever schema is defined (new `doc_link` table).

**Verification:** Re-scan; assert every PF doc has `len(links_to) >= 2` (one use-case + at least one entity); `discover query "SELECT COUNT(*) FROM doc_link WHERE kind='scenario_to_usecase'"` returns ≥ N where N = scenario count.

---

## 4. Critical Files (concentrated reference list)

| File | Change tracks | What changes |
|---|---|---|
| `src/ai_discovery/cli.py:265` | T3 | Root folder rename to `output-{slug}` |
| `src/ai_discovery/ai/rollup.py:51, 73, 140` | T1, T4 | Inject verified-fact blocks; extend RollupResult with row-level confidence |
| `src/ai_discovery/ai/self_review.py` | T4 | Decay confidence on contradicted/unverified critical rows |
| `src/ai_discovery/ai/flow_analyzer.py` (or `flow_clustering.py`) | T3 | Filter bootstrap scenarios (`main`, `addresourcehandlers`) |
| `src/ai_discovery/generators/doc_generator.py:20–41, 55–56, 118–148, 159–161, 246–247` | T2, T3, T5 | Filename scheme; new doc-type prefixes; cross-artifact link graph |
| `src/ai_discovery/generators/templates/as-is.md.j2` | T1, T4 | Render verified API table from data, not LLM |
| `src/ai_discovery/generators/templates/as-is-detail.md.j2` | T1, T4 | Same |
| `src/ai_discovery/generators/templates/as-is-schema.md.j2` | T1, T4 | Render verified schema table from data |
| `src/ai_discovery/generators/templates/process-flow.md.j2` | T4, T5 | Per-step source citations; Related-docs section |
| `src/ai_discovery/generators/templates/{bpmn,dmn,ears}.md.j2` (new) | T2 | New templates for Phase 3 artifacts |
| `src/ai_discovery/pipeline.py:763–788` | T2 | Route Phase 3 generator output through `write_docs` |

## 5. Reusable utilities to lean on (don't reinvent)

- `_slugify` (`doc_generator.py:44–48`) — already strips non-alphanumerics; reuse for all filename construction.
- `_make_doc_id` (`doc_generator.py:55–56`) — adapt by removing project_slug arg (passed in via folder name now).
- `framework_hints` (`chunker.py:83, 181, 213, 249`) — already plumbed through chunks; just need to surface in rollup.
- `_phase_should_run` / `_with_checkpoint` (`pipeline.py`) — wrap any new generator phase with these so re-runs are incremental.
- `RollupResult` (`ai/rollup.py`) — extend rather than create a parallel structure.

## 6. Verification — end-to-end smoke test

After implementing all five tracks, run:

```
discover scan repo --project-slug sena \
  --src data/java-springboot-ecommerce-application \
  --output data \
  --profile

# Expected:
# - data/output-java-springboot-ecommerce-application/ exists
# - PF/ has ~20 scenarios named {domain}-{action}.md (no main, no addresourcehandlers)
# - ASIS/sena.md API table matches `grep -rn "@.*Mapping" .../controller/`
# - BPMN/, DMN/, EARS/ each have ≥1 file
# - Every endpoint row shows source as file:line
# - Doc confidence < 1.0 anywhere a contradicted claim exists
# - PF docs have links_to populated (use-case + entity references)

discover view -p sena
# Quality dashboard shows: confidence histogram, weakest-doc ranking,
# artifact presence (BPMN/DMN/EARS all green), clickable cross-references.

discover impact Order -p sena
# Returns: PF placeorder + getorderbyid + getallorders + checkoutlist,
# entities User + OrderItem + Product + Cart, with confidence per edge.

# Spot-check the two highest-impact accuracy fixes:
diff <(grep -E "POST|PUT|DELETE|GET" data/output-*/ASIS/sena.md | grep -oE '/[a-z/{}-]+') \
     <(grep -rhoE '@(Get|Post|Put|Delete)Mapping\("[^"]*"\)' data/java-springboot-ecommerce-application/src/main/java/com/sena/tecmiecommercebackend/controller/ | grep -oE '"/[^"]*"' | tr -d '"')
# Should show every actual @-Mapping route appearing in the doc and no extras.
```

## 7. Triage & Consistency as Claude Code Skills (complementary layer)

The five tracks above harden the **batch pipeline** so its output is correct and well-structured by default. The remaining ~5% — judgment-shaped tasks like "is this row plausible given the rest of the doc?" or "are there flows missing for this domain?" — fits better as **user-initiated Claude Code skills** running over the already-published output, not as more steps in the batch pipeline.

This matches the pattern already established in this repo: `/parser-extension`, `/call-graph-debug`, `/pipeline-analyze` are documentation-first skills (CLAUDE.md entry + `docs/guides/<topic>/` reference, no SKILL.md files yet — wiring is via CLAUDE.md mentions). The two new skills follow the same shape.

### 7.1 `/discover-triage` — Low-confidence row review

**When:** After a batch scan completes, before treating its docs as authoritative.
**Inputs (skill prompts user):** source repo path, discovery output path (or project slug to resolve via `discover view`).
**What it does:**
1. Query the discovery DB for rows with `confidence < 0.65 AND criticality = high` (depends on Track 4 row-level confidence landing).
2. For each flagged row, read source at the cited `file:line`, verify the claim, and decide accept / correct / mark-for-human.
3. Emit a `triage-report.md` with proposed corrections, each with source citation and a confidence delta.
4. Apply corrections only on user confirmation; never edit `.md` artifacts in place without an audit trail.

**Reference doc to create:** `docs/guides/output-review/triage-workflow.md`.
**CLAUDE.md update:** add the skill under "Using Skills" alongside `/parser-extension`.

### 7.2 `/discover-consistency` — Cross-artifact consistency check

**When:** After triage, or as a periodic audit on shipped output.
**Inputs (skill prompts user):** discovery output path (or project slug).
**What it does:**
1. Build the cross-artifact link graph from the published `links_to` frontmatter (depends on Track 5 landing).
2. Verify: every endpoint in `ASIS/` API table appears in at least one `PF/` doc; every entity in `ASSC/` appears in at least one `EARS/` requirement; every PF scenario references a use-case in `ASD/`; every `IMPACT/` doc reaches entities that exist in `ASSC/`.
3. Detect **domain-pattern absences**: e.g., for `tags=[ecommerce]`, expected scenarios include logout / password-reset / order-cancel / refund / payment-webhook — flag any that have no PF doc *and* no source-code evidence (the latter being a code gap, not a doc gap; the report distinguishes the two).
4. Emit `consistency-report.md` with a severity-ranked list of inconsistencies and missing-flow callouts. No in-place edits.

**Reference doc to create:** `docs/guides/output-review/consistency-workflow.md`.
**CLAUDE.md update:** same.

### 7.3 Why skills, not pipeline phases

| Concern | Pipeline phase | Skill |
|---|---|---|
| Determinism / reproducibility | required | inherently judgment-based |
| Cost per CI run | Pays every run | Pays only when invoked |
| Audit trail | Tied to scan run | Skill emits review reports as separate artifacts |
| Scope of correction | Whole-doc regeneration | Targeted: low-confidence rows or cross-doc checks |
| Tooling reuse | Re-runs LLM phases | Calls existing `discover query`, `discover impact`, `discover view` |

Skills are the right primitive for this layer because the work is **reactive and bounded** (only flagged rows; only consistency-graph edges) rather than **exhaustive and forward-pass** (every chunk must be summarized).

### 7.4 New files to create

| File | Purpose |
|---|---|
| `docs/guides/output-review/triage-workflow.md` | Step-by-step triage workflow (mirrors `extension-checklist.md` shape) |
| `docs/guides/output-review/consistency-workflow.md` | Cross-artifact consistency checks |
| `CLAUDE.md` (existing — edit) | Add `/discover-triage` and `/discover-consistency` to "Using Skills" section |
| `README.md` (existing — edit) | Document the production-grade workflow that combines batch pipeline + skill layer |

### 7.5 Optional CLI affordances to make the skills easier

A few small CLI additions would let the skills do their work without ad-hoc SQL:

- `discover review --confidence-below 0.65 --criticality high -p <slug>` → returns a JSON list of flagged rows with source citations. Saves the skill from inventing its own SQL each invocation.
- `discover consistency -p <slug>` → runs the link-graph checks programmatically and emits a JSON report; the skill consumes + summarizes it.

These would live in `src/ai_discovery/cli.py` alongside `discover impact` and `discover query`. Building them is optional — the skills can fall back to raw SQL via `discover query` — but they make the skill workflow much cleaner and more testable.

---

## 8. Out of scope (deliberate)

- **Adding missing e-commerce flows** (logout, password-reset, refunds, webhooks) — these are absent in the **source code**, not just the docs. Discovery should *flag* their absence, not invent them. Consider a future Track 6: ASIS includes a "Notably absent flows expected for domain=ecommerce" section sourced from a domain-pattern library.
- **Rewriting MD5 password hashing or other code-level smells** — discovery's job is to surface them in ASIS § Technical Debt (already done), not to fix them.
- **Java parser improvements beyond endpoint extraction** — the parser is correct; the bug is downstream. Parser work belongs to a separate `/parser-extension` track if Java accuracy needs broader improvement.

---

**Estimated effort:** Track 1 + 3 are the highest leverage and smallest changes (~1 day each). Tracks 2 + 4 + 5 are medium (~2–3 days each). Total ~10 dev-days for the full plan; T1 + T3 alone (~2 days) eliminates the most reader-trust-damaging issues.
