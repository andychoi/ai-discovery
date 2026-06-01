# Reverse-Spec Solution Review — Architecture, Design & Implementation

**Date:** 2026-05-31
**Reviewer:** Multi-agent objective review (9 independent dimension reviewers + adversarial verification of every source-checkable finding against current code), synthesized with manual ground-truth spot-checks.
**Lens:** Output **trustworthiness / faithfulness** (does generated documentation accurately reflect the source code?), weighted above performance and ergonomics.
**Tone:** Brutally objective, by request.
**Scope:** Full current state of `ai-discovery` on `main` — not a re-litigation of the stale 2026-04-16 / 2026-05-07 assessments, which cite paths (`src/ai_discovery/output/*`) that have since moved to `generators/*`. Every claim below was verified against the code as it exists today.

---

## 0. Implementation Status (updated 2026-05-31)

Findings from this review were implemented in waves (each its own commit, full suite green at every step). Status:

| Finding | Status | Notes |
|---|---|---|
| **CRIT-1** screen-phase crash | ✅ Done | `llm_client` constructed before Phase 2; AST regression guard. |
| Screen-spec fence/truncation | ✅ Done | Durable structured output (Bedrock tool use) + shared fence-tolerant fallback. |
| **CRIT-2** PF docs over-confident | ◑ Interim | Confidence capped to 0.5 + provenance banner. *Remaining:* source-fed prompts + per-step citations + verification pass. |
| **CRIT-3** screen specs over-confident | ◑ Interim | Confidence capped + `content_provenance` + banner. *Remaining:* same as CRIT-2. |
| **HIGH-1** blend_confidence 1.0 trap | ✅ Done | Unverifiable docs → 0.3, not 1.0. |
| **HIGH-2** self-review trust holes | ✅ Done | Evidence-gated verdicts (abstain on weak/absent RAG), re-verify regenerated prose, claim cap 10→50. |
| **HIGH-3** no DI/receiver-type resolution | ✅ Done (Java/C#/Python) | Stage-3 `receiver_type` resolution consumes parser-extracted field/ctor types: `orderService.process()` → `OrderService.process` (0.93), no fan-out. `di_resolution`=1.0 hard target on Java + C# + Python collision fixtures; framework-inherited calls still fall through unresolved. (JS field types remain a follow-up.) |
| **HIGH-4** C# route prefix dropped | ✅ Done | Composes `[Route]` + `[controller]`/`[action]` tokens. |
| **HIGH-5** Python Flask/CBV endpoints | ✅ Done | `methods=[]`, default GET, class-based views; Python endpoint tests added. |
| **HIGH-6** no FK/relationship extraction | ✅ Done | FK extraction (SQL + JPA) + `db_relationship` table + `.sql`/migrations reading (FK-aware docs Phase 1–2). |
| **HIGH-7** non-code artifacts ignored | ☐ Deferred | OpenAPI/proto/IaC ingestion — large, new subsystem. |
| **HIGH-8** external systems / federation | ◑ Partial | Relabeled "External Dependencies". *Remaining (large):* first-class external-system nodes; cross-repo integration correlator. |
| **HIGH-9** inferred USER_TASK steps | ✅ Done | `⚠ inferred` marker in BPMN/Mermaid; transition-provenance doc reconciled. |
| **HIGH-10** faithfulness untested / no corpus | ◑ Phases 1–2 done | `tests/corpus/` accuracy gate over 4 fixtures (endpoints/entities/FK/call-edges/di-resolution), deterministic, baseline-recorded; test-strategy.md fiction replaced. Surfaced two measured gaps: JS Mongoose entities (0.0) and DI fan-out (`di_resolution`=0.0). *Remaining:* Phase 3 external repos. |
| **MED-1** confidence doc≠code | ✅ Done | decisions.md/CLAUDE.md reconciled to the 4-stage resolver + raw ranking score. |
| **MED-3** edge dedup | ✅ Done | Edges deduped per (caller, callee, edge_type), max confidence. |
| triggers min_support=1 | ✅ Done | Raised to 2 (single co-occurrence ≠ causal). |
| **LOW** ✓1.00 label / PF H1 / etc. | ✅ Done | `✓ AST` provenance label; PF body H1 uses scenario. |
| **LSP/compiler resolver tier** | ☐ Deferred | The biggest architectural shift; multi-day, own effort. |

**Legend:** ✅ done & tested · ◑ interim/partial (active harm reduced; deeper fix scoped) · ☐ deferred (large architectural effort, documented for a focused future pass).

The deferred items share a precondition the review itself names: a **corpus-based accuracy harness** (HIGH-10) should land first, so DI resolution (HIGH-3), external-system modeling (HIGH-8), and artifact ingestion (HIGH-7) can be measured rather than asserted. Rushing them at the tail of this pass would reproduce the very "under-tested sprawl" the review warns against.

---

## 1. Executive Verdict

**Is this the best reverse-spec solution?** The *architecture* is the right primary bet and contains one genuinely best-in-class idea. The *implementation* is half-finished against its own design intent, and the unfinished half is the half that determines whether a reader can trust the output.

The single most important design decision in this codebase is correct and verified working: **demote the LLM from author-of-facts to author-of-prose-around-facts.** For the rollup layer (ASIS/ASD/ASSC), AST-extracted endpoints and entities are injected as a non-overridable `## VERIFIED FACTS (do not modify)` block and rendered deterministically, with per-row `file:line` citations and `✓ 1.00` confidence. I independently confirmed this end-to-end: the generated endpoint table in `data/output-sena/ASIS/sena.md` matches the source `@*Mapping` annotations **exactly**, including the hard case of composing class-level `@RequestMapping("/cart")` with method-level `@PostMapping("/add")` → `/cart/add`. The 2026-05-07 assessment's worst finding — ~10 of 20 endpoints hallucinated — is **fully resolved**. This is a stronger faithfulness guarantee than pure-LLM or naive-RAG approaches offer, and it is real, wired, and tested.

**But that machinery is an island, and the product is risk-inverted.** The anti-hallucination controls are applied to the artifacts *least* prone to mislead (structured tables) and are *absent* from the artifacts *most* prone to mislead and most visible to users:

- **Process-flow docs (`PF/`)** are free-form LLM narrative fed no source code, carry no citations, bypass self-review, and are published at `discovery_confidence: 1.0` — while describing features (inventory, tax, notifications) that provably do not exist in the target source.
- **Screen-centric specs** — explicitly designated "the user entry point to the entire system" — free-generate field lists, types, sources, and business rules with zero grounding and zero verification, published at a flat `0.8`. This reintroduces the exact hallucination class Track 1 was built to kill, one layer up.
- And the **flagship screen-centric feature crashes** (`UnboundLocalError`) the moment it detects screens on a real repo — see CRIT-1.

**Net verdict:** A strong, defensible architecture with one excellent, proven faithfulness control — over-trusted for everything that depends on resolved call edges or LLM prose. The tool currently earns trust where it needs it least and asserts trust where it has earned it least. It is **adequate-trending-weak on its own core promise** until process-flow and screen-spec content is grounded and verified the way rollups already are.

| | |
|---|---|
| **Architecture (the bet)** | Sound. Static-analysis-first is correct for brownfield; evidence/narrative separation is best-in-class. |
| **Rollup faithfulness (ASIS/ASD/ASSC)** | Best-in-class. Verified working. |
| **Process-flow & screen-spec faithfulness** | Weak. Ungrounded, uncited, over-confident, untested. |
| **Call-graph resolution** | Adequate-but-overstated. No type/DI resolution; documented formula ≠ code. |
| **Enterprise / cross-repo** | Largely a single-repo summarizer wearing an enterprise label. |
| **Testing of faithfulness** | Structurally absent. Accuracy targets are asserted, never measured. |

---

## 2. How This Review Was Conducted

Nine independent reviewers each examined one dimension and were required to ground every claim in `file:line` evidence. Every *source-checkable* weakness was then handed to a separate **adversarial verifier** instructed to *refute* it by reading the actual current source — this filters out claims based on stale/moved paths (the failure mode of the prior assessments). Of ~40 source-checkable weaknesses, the large majority were **confirmed**; a handful were downgraded (e.g. "cross-repo external modeling" was confirmed-but-narrower than claimed). Findings below carry their post-verification severity. Research/opinion claims (competitive positioning) are labelled as such and were not source-verified.

The two highest-stakes claims — the verified-facts win and the screen-phase crash — I additionally confirmed by hand against the source and the committed sample output.

---

## 3. What Is Genuinely Strong (verified)

These are real, not scaffolding. Credit where due:

1. **Deterministic verified-facts injection (Track 1).** `ai/rollup.py:78-114` builds the endpoint table from parser `framework_hints`; `:352-369` prepends it under `## VERIFIED FACTS (do not modify)` with the instruction *"never infer alternatives."* The published table matches source annotations exactly. **This is the design's crown jewel.**
2. **Deterministic confidence blending (Track 4).** `rollup.py:386-409` `blend_confidence` recomputes doc confidence from AST-verified rows (1.0) + review verdicts (verified 1.0 / unverified 0.5 / contradicted 0.0), *replacing* the model's self-asserted score (`pipeline.py:1301-1304`). Trusting a computed score over the LLM's own claim is the right instinct.
3. **Cross-artifact `links_to` graph (Track 5)** is wired and avoids dangling links by only linking to rollups that exist (`doc_generator.py:373-379`).
4. **Production-grade resumability & schema discipline.** Per-phase checkpoints, `get_last_complete_phase` resume, same-SHA fast re-render, an explicit guard against the "completed scan with zero docs" sticky-cache trap (`pipeline.py:379-407`), and forward-only migrations (`db.py:395-455`, `SCHEMA_VERSION=9`).
5. **Phase ordering is correct.** The 2026-04-16 claim that execution slices are built before domain classification is **refuted** — domain classify is Phase 7 (`pipeline.py:609`), slices Phase 8 (`:711`), scenarios carry `entry.domain`.
6. **Conservative DMN generation & the 8-kind entity classifier** omit rather than fabricate; the backbone Mermaid refuses half-known edges; EARS surfaces confidence. The *purely static* half of the FSM layer is honest.
7. **Test hygiene as engineering:** 763 fast green tests, no hidden skips, honest extraction-layer assertions, and a real anti-hallucination unit test for verified-facts injection.

---

## 4. Findings by Severity

Severity is post-verification. **Trust impact**: `direct` = can cause unfaithful/hallucinated output; `indirect` = degrades quality; `none` = ergonomics/perf.

### CRITICAL

**CRIT-1 — Flagship screen-centric feature crashes on its target inputs.** `trust: indirect (availability: total)`
`run_pipeline` references `llm_client` inside Phase 2 (`pipeline.py:464` `_budget_ok(llm_client, …)`, `:479-481` `generate_all_screen_specs(… llm_client …)`), but the only binding is `llm_client = LLMClient(config)` at `pipeline.py:918` — *after* Phase 2. Because it is assigned later in the same function, Python treats `llm_client` as a function-local throughout, so the screen branch raises **`UnboundLocalError: local variable 'llm_client' referenced before assignment`** the instant `screens` is non-empty (`:454` skips when empty). `_with_checkpoint` catches, records a phase error, and re-raises — aborting the whole scan. The committed `output-sena` sample passes only because it is a backend-only Spring app with no menu, so screens are never detected. **The headline v0.3 capability, shipped 2026-05-31, fails for exactly the repos it targets.**
*Fix (small):* move `llm_client = LLMClient(config)` before Phase 2, or construct it lazily inside the phase.

> **Update 2026-05-31 — RESOLVED.** `llm_client` is now constructed up-front (`pipeline.py:443`, before Phase 2); regression guard added (`tests/test_screen_phase_regression.py`, AST use-before-assignment invariant). A live `discover scan` on the menu-bearing fixture (`tests/fixtures/projects/spring-boot-app`, 11 screens, Bedrock) confirmed Phase 2 runs past the old crash site. **Unblocking the crash exposed two latent bugs in the never-exercised screen path** (both also fixed): (1) `screen_spec_generator.py` did a bare `json.loads(response.text)`, failing on Claude's markdown-fenced JSON — the *only* LLM-JSON call site without fence handling — now uses a fence/prose-tolerant extractor mirroring `flow_analyzer`/`self_review`; (2) `max_tokens=2048` truncated content-rich specs mid-JSON — raised to 8192. Live re-scan: **11/11 screen specs generated and written.** Both latent bugs are instances of HIGH-2's root cause (*no structured-output enforcement anywhere*). **The durable fix has now landed:** `LLMClient.invoke_structured` forces Bedrock Converse tool use, so the model returns schema-validated JSON as tool input — no free text to fence-strip and nothing to truncate mid-object. Ollama-compatible providers fall back to a single shared fence-tolerant extractor (`shared/json_extract.py`, replacing the three ad-hoc strippers that were scattered across `flow_analyzer`/`self_review`/`screen_spec_generator`). Screen specs now use this path; live re-scan: **11/11 generated, zero JSON errors**, the fence + truncation failure classes eliminated structurally rather than patched. This cascade is itself evidence for HIGH-10: the screen path shipped (2026-05-31) with no test that ever exercised it against a real model.

**CRIT-2 — Process-flow docs are LLM-invented narrative stamped `confidence: 1.0`.** `trust: direct`
`PF/` docs are free-form prose with no source code fed to the prompt, no `file:line` citations, and no self-review pass; they are published at `discovery_confidence: 1.0`. Sample `PF/` output describes inventory/tax/notification behavior absent from the target source. The IPO and interface-inference prompts receive **no source code at all** — pure free-form invention from a prior LLM step. These are the highest-visibility artifacts and they are the least grounded.

**CRIT-3 — Screen specs reintroduce the Track-1 hallucination class.** `trust: direct`
The newest, most user-facing output type free-generates structured field lists, types, sources, and business rules with **no verified-facts injection and no self-review**, published at a fixed `0.8`. This is precisely the failure mode the 2026-05-07 plan diagnosed for endpoints — rebuilt one layer up, on the artifact the docs call the system's primary entry point.

### HIGH

**HIGH-1 — `blend_confidence` returns 1.0 when there is nothing to score.** `trust: direct`
`rollup.py` confidence trap: when zero claims are extractable *and* zero AST rows exist, the doc publishes as **maximally confident**. An unverifiable document scores highest. (Verified.)

**HIGH-2 — Self-review is an LLM grading an LLM, uncalibrated and capped.** `trust: direct`
The verifier is a single-token tier-1 (cheap) judgment over the top-3 RAG chunks, with no abstention threshold and a hard **10-claim-per-doc cap**. A claim whose supporting code RAG fails to retrieve is scored "unverified" (not flagged as a possible hallucination) — masking fabrications as merely-unconfirmed. `regenerate_sections` rewrites flagged sections with the cheap model and **never re-verifies**; confidence is computed from the pre-rewrite claims.

**HIGH-3 — No DI / receiver-type / interface resolution; the call graph discards type evidence its own parser extracts.** `trust: direct`
Calls on injected service instances (`this.orderService.save()` — the dominant Spring/NestJS/ASP.NET pattern) fall through to short-name matching. The final fallback (`call_graph.py:254`) deliberately emits an edge to **every** remaining candidate at confidence 0.6 — explicit fan-out, above the 0.6 review threshold. The parser captures `@Autowired` DI types but the call graph never consumes them. This is the single biggest static-only faithfulness risk: false edges propagate silently into slices, scenarios, BPMN, and process docs.

**HIGH-4 — C# ASP.NET routes drop the controller-level `[Route]` prefix yet are stamped `✓ 1.00`.** `trust: direct`
`csharp.py:581-590` captures only the method-level fragment, so the "verified" ASP.NET path is *incomplete* but published as authoritative ground truth — worse than no claim, because the confidence machinery vouches for it.

**HIGH-5 — Python misses Flask's primary idiom and all class-based views.** `trust: direct`
`python_parser.py:226` vs `:167-206`: the `methods=[...]` route form and DRF/FastAPI class views are not detected, so endpoints are silently omitted for those stacks.

**HIGH-6 — No FK / entity-relationship extraction anywhere.** `trust: direct`
No language or the SQL extractor captures relationships; FKs are explicitly discarded (`sql_extractor.py:226`), and `.sql` migration files — the most reliable schema source — are never read. Entity relationships are invisible to the engine, so schema docs and impact analysis under-represent the data model. A reader cannot tell that `order_items` is the join between `orders` and `products` — the table's purpose is only legible through its FK neighborhood. *Design proposal:* `docs/specs/2026-05-31-fk-aware-table-docs.md` (FK-aware table documentation — extract FK edges, then infer each table's purpose from its referenced-table context).

**HIGH-7 — Non-code artifacts entirely ignored.** `trust: indirect`
OpenAPI/Swagger, GraphQL, proto, Dockerfile, Helm/K8s, Terraform, and queue configs are unhandled (gap-assessment #6, unaddressed). OpenAPI in particular is often a *more complete and more authoritative* endpoint source than the AST.

**HIGH-8 — External systems are not first-class nodes; federation merges by name coincidence.** `trust: direct`
Unresolved external calls degrade to 0.5-confidence strings indistinguishable from parse misses (`call_graph.py:102-111`); they never become DB/API/queue nodes. `discover federate` stitches entity FSMs by **name+field overlap** (`federation.py:134-176`), not by integration edges — two services that actually call each other are not linked unless they happen to model a same-named entity. The "External Dependencies" section in domain rollups actually lists *intra-repo cross-domain* calls, mislabeling internal coupling as external integration.

**HIGH-9 — LLM-invented `USER_TASK` steps render into BPMN as fact.** `trust: direct`
The LLM is prompted to invent approval/review steps that render into BPMN identically to code-grounded steps — **no inferred marker, no confidence decay**. The `decisions.md` design for flagged MANUAL nodes (0.3–0.6 confidence, "⚠ Inferred") is documented but **not implemented**. Relatedly, the documented explicit-vs-name-inferred transition confidence split does not exist — all transitions are confidence 1.0.

**HIGH-10 — Faithfulness is structurally untested; accuracy targets are fiction.** `trust: direct`
The integration test feeds the LLM canned strings, so a model inventing endpoints/entities/rules in published docs **cannot be caught by the suite**. CLAUDE.md's load-bearing targets (≥85% call resolution, ≥80% parser accuracy) have *no* measuring harness — `test-strategy.md` documents a `CallGraphResolver` / `expected_calls.json` corpus API that **does not exist in code**. "Real-project validation" runs on 28–64 KB synthetic toy apps asserting existence/non-error, not correctness.

### MEDIUM (selected)

- **MED-1 — Documented confidence formula ≠ code.** `decisions.md:239` claims `min(1.0, …)/10` normalization; `_score_node` returns an **unnormalized 0–17 raw sum** stored in the same `confidence` field used by call edges (0.5–1.0). Two different scales, one field name — a latent correctness hazard for any threshold logic. The "7-level scoring" table is also stale vs the implemented 4-stage resolver.
- **MED-2 — Phase 8 FSM/BPMN/DMN/EARS is a 170-line inline block outside any checkpoint** (`pipeline.py:722-889`); a mid-block crash leaves Phase 8 marked complete with artifacts missing — silent partial output.
- **MED-3 — No edge deduplication;** the same `(caller, callee)` pair can be emitted multiple times, inflating apparent coupling.
- **MED-4 — `EntityServiceResolver` is regex+glob string matching,** not AST, contradicting the "static-analysis-first / tree-sitter" framing, and is disconnected from the call graph.
- **MED-5 — Cross-entity `triggers` edges emitted at `min_support=1`** — a single coincidence rendered as a causal sequence.
- **MED-6 — Screen interface `links_to` point to interface docs that are never generated** — dangling links.
- **MED-7 — Verified claims discard their `file:line`;** only failures retain a (line-less) source reference, so passing rollup prose is not independently auditable.

### LOW (selected)

- `✓ 1.00` conflates "deterministically extracted" with "correct" (e.g. HIGH-4 inherits this).
- PF document body H1 uses domain, not scenario — identical across all PF docs.
- No structured-output/tool-use enforcement anywhere; all model calls are free text with lossy fallbacks. (`converse_bedrock` already supports `toolConfig` — unused.)
- No `conftest.py` / pytest markers to separate fast mocked tests from accuracy tests.

---

## 5. Is Static-Analysis-First the Best Approach?

**Yes, as the primary bet.** Brownfield/legacy code usually cannot be run, has no tests, and generates no traffic — so dynamic tracing, process-mining-from-real-logs, and test-driven extraction are non-starters as the *primary* engine; they can only ever be optional enrichments. The deterministic AST-fact injection is a genuinely best-in-class move that makes the highest-frequency hallucination class structurally impossible.

**Where the bet is fundamentally limited: it is static *without types*.** Name/prefix heuristic resolution cannot resolve polymorphic or interface dispatch — and the tool discards the DI type information its own parser already extracts (HIGH-3). Everything downstream of the call graph inherits silently-wrong edges with confident-looking scores, and the prose-level self-review does not reliably catch it.

**The two architectural shifts that would most improve faithfulness:**
1. **Convert static-only into static-with-types.** Consume the already-extracted DI/constructor/base-class types in call resolution; add an optional LSP / compiler-index / SCIP / stack-graphs resolver tier *above* the name heuristics, falling back only when unavailable. This directly attacks the biggest risk.
2. **Make self-review honest about its own blind spots.** Distinguish "unverified-because-not-retrieved" from "unverified-because-ambiguous," require a minimum RAG similarity before a "verified" verdict, and remove/raise the 10-claim cap.

A pure-LLM large-context or graph-RAG hybrid would require far less bespoke machinery and is closing the gap on the *comprehension* task — but neither offers the deterministic faithfulness guarantee this design already has for endpoints/entities. That guarantee is this project's defensible moat; the recommendation is to **widen its coverage, not abandon the approach.**

---

## 6. Prioritized Recommendations

Ordered by reader-trust leverage per unit effort.

### Do first (small effort, high leverage)
1. **Fix CRIT-1** — move/lazy-construct `llm_client` before Phase 2. Restores the flagship feature. *(One-line move; add a regression test that scans a repo with a menu.)*
2. **Fix HIGH-1** — when zero claims and zero AST rows, publish low/"unverifiable" confidence, never 1.0.
3. **Stop stamping `discovery_confidence: 1.0` on unverified PF/screen docs** — default to an explicit "unverified" sentinel until a verification pass runs. *(Mitigates CRIT-2/CRIT-3 immediately, before the deeper fix lands.)*
4. **Compose the C# controller `[Route]` prefix** (HIGH-4) — mirror `java.py:164-169` — before injecting ASP.NET paths as authoritative.
5. **Reconcile `decisions.md`/CLAUDE.md with code** (MED-1): collapse "7-level" to the real 4-stage resolver; either normalize `_score_node` to 0–1 or rename the field.

### Do next (medium effort, high leverage)
6. **Extend verified-facts + self-review to PF and screen specs** (fixes CRIT-2, CRIT-3): feed execution-slice *source code* (not just node names) into PF/IPO/interface prompts; run PF and screen specs through the same claim-verification + `blend_confidence` pass already built for rollups; add per-step `file:line` and a provenance field.
7. **Tag LLM-originated/`USER_TASK` steps as inferred** (HIGH-9) and render them visibly distinct (dashed, ⚠) in BPMN/Mermaid; stamp real transition provenance and confidence < 1.0.
8. **Add Flask `methods=[...]` + class-based view parsing** (HIGH-5) with regression tests; Python endpoint extraction currently has none.
9. **Re-verify regenerated sections** (HIGH-2) and recompute confidence from the rewritten text; add a RAG-similarity abstention threshold to the verifier.
10. **Build a real faithfulness regression test** (HIGH-10): VCR-style recorded LLM output run through verified-facts injection, asserting no endpoint/entity appears in a doc that is absent from the parsed graph.

### Do when investing in enterprise/scale (large effort)
11. **Promote unresolved external calls to typed first-class external-system nodes** (HIGH-8) with stable identity keys; add a cross-repo integration correlator that matches outbound interface descriptors (REST URL, Kafka topic) against inbound endpoints/listeners — the actual enterprise deliverable.
12. **DI/receiver-type resolution + interface→impl binding** (HIGH-3); then an optional LSP/index resolver tier.
13. **Ingest OpenAPI + `.sql` migrations + FK/JPA relationships** (HIGH-6, HIGH-7) as authoritative ground-truth feeding the verified-facts table.
14. **A pinned external-repo corpus with human-labeled ground truth** to actually measure the 85%/80% targets (HIGH-10).

---

## 7. Dimension Scorecard

| Dimension | Grade | One-line verdict |
|---|---|---|
| Architecture & approach | **B+** | Sound bet, correct ordering, best-in-class evidence/narrative split — one critical regression + one un-checkpointed block. |
| Rollup faithfulness (ASIS/ASD/ASSC) | **A−** | Verified-facts injection works end-to-end; the project's strongest asset. |
| Process-flow & screen-spec faithfulness | **D** | Ungrounded, uncited, over-confident, untested; reintroduces the hallucination class Track 1 killed. |
| Call-graph resolution | **C** | Import-scoped resolution is real, but no type/DI resolution, explicit fan-out, doc≠code. |
| Parser extraction | **C+** | Endpoint verbs/routes faithful (Java/Express); broken for C#/Python; no relationships/artifacts. |
| Enterprise / cross-repo | **C−** | Single-repo summarizer with name-coincidence federation; external systems not modeled. |
| FSM / BPMN / DMN | **B−** | Honest where purely static; scenario BPMN can present invented steps as fact. |
| Testing of faithfulness | **D+** | Healthy plumbing tests; cannot detect the product's core failure mode. |
| Competitive positioning | **B** | Right primary bet; defensible moat in AST-fact determinism; over-trusts resolved edges and prose. |

**Overall: B− as engineering, C+ as a *trustworthy* reverse-spec product** — held back almost entirely by the risk-inversion described in §1. The fixes are well-scoped and mostly small; the gap is one of coverage discipline, not architectural soundness. Close §6 items 1–7 and this becomes a genuinely best-in-class tool.

---

## 8. Relationship to Prior Assessments

- **2026-04-16 gap assessment:** "slices before domains" — *refuted/fixed.* "Cross-application dependencies not modeled" — *still true* (HIGH-8). "Non-code artifacts ignored" — *still true* (HIGH-7). "Tests fail at collection" — *fixed* (763 green), but accuracy still unmeasured (HIGH-10).
- **2026-05-07 output-quality plan:** T1 (verified facts) — **landed and verified working.** T3 (filenames) — landed (`output-sena/PF/sena-addtocart.md`). T4 (per-claim confidence) — landed *for rollups only* (MED-7, CRIT-2/3 are the uncovered remainder). T5 (`links_to`) — landed. The plan's own §7 skills (`/discover-triage`, `/discover-consistency`) exist and are the right complementary layer for the judgment-shaped checks this batch pipeline cannot do.

The trajectory is good: every prior *structured-data* finding was addressed. The remaining frontier is **narrative faithfulness** — PF docs, screen specs, scenario BPMN — which the verified-facts pattern has not yet reached.
