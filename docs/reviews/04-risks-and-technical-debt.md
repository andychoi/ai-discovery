# Risks, Bottlenecks & Technical Debt Register

**Date**: 2026-07-12 · Reviewed at commit `d9d997b`

Numbered register of everything found. Severity: **P0** correctness/trust, **P1** structural debt taxing every change, **P2** performance/scale, **P3** hygiene. Each entry cites code so it can be actioned directly. Roadmap sequencing lives in doc 05.

---

## P0 — Correctness & trust

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| P0-1 | `discover ingest-docs` crashes: `_VALID_PUSH_MODES` referenced but never defined | `cli.py:756` (only `_VALID_INGEST_TARGETS` exists, `cli.py:20`) | Command is dead on arrival; no CLI smoke test caught it |
| P0-2 | Doc-embedder `doc_id` collision: ASIS/ASD/ASSC all write `{domain}.md`, embedder keys on `f.stem` | `doc_generator.py:73-84` vs `doc_embedder.py:111` | Resume guard (`doc_embedder.py:88-94`) never fires → full re-embed every run; chat citations ambiguous |
| P0-3 | Zero-nodes scan finalises as `completed`, exit 0 | `pipeline.py:707-710` | A repo that failed to parse is recorded as a successful scan; CI green on garbage |
| P0-4 | Budget-exceeded runs exit 0 | `pipeline.py:1189,1240,1328` (status `budget_exceeded`, `return None`) | Truncated scans indistinguishable from success to callers |
| P0-5 | `stop_reason` never inspected on any LLM response; truncation guessed by `tok_out ≥ 0.95·max_tokens` | `llm_invoke.py` (all transports), `llm_router.py:460` | Silent truncation/refusal flows into generated docs; hard blocker for Fable adoption (doc 03 §2.4) |
| P0-6 | `MODEL_RATES` substring matching under-prices unknown premium models (Fable → $3/$15 default vs real $10/$50) | `model_defaults.py:152-176` | Budget guard defeated exactly when the most expensive model is configured |
| P0-7 | Federation FSM merge is greedy first-fit, docstring claims union-find; non-transitive grouping | `federation.py:176-205` | Entities split incorrectly on 3+ repo merges |
| P0-8 | Divergent `_jaccard` empty-set semantics (0.0 vs 1.0) | `fsm_identity.py:713-715` vs `federation.py:366-368` | Latent trap for any unification/refactor |
| P0-9 | Resume-path scenario inference swallows all exceptions: `except Exception: pass` | `pipeline.py:1315-1316` | Silent data loss on resume; sibling swallowed-exception sites at `pipeline.py:878,1173,1299,1380,1449,1581,1659,2017` at least log |
| P0-10 | Read-after-write scoring signal matches field names against node *names*, not source | `call_graph.py:697-703` | Documented signal is decorative; scores mislead tuning |
| P0-11 | BPMN lanes declared but `flowNodeRef` never assigned | `bpmn_generator.py:252` | Swimlanes render empty in BPMN viewers |
| P0-12 | `screen_mapper` steps 3–4 are self-admitted placeholders; frontend-API step returns skeleton data | `screen_mapper.py:583-608` | Screen specs claim mappings that were never computed |

## P1 — Structural debt (the tax on every change)

**P1-1. `run_pipeline` god function.** ~1,466 lines (`pipeline.py:293-1759`); 18 hand-unrolled phase blocks each repeating the skip/resume/reload pattern; inline SQL throughout (node persist `:728-753`, edges `:819-863`, onboarding `:1415-1431`, doc_id sync `:1714-1723`); the ~170-line inline "8b" block (`:916-1086`) mixes FSM mining with BPMN/DMN/EARS generation and is **uncheckpointed** (re-runs on every resume past phase 8). Missing abstraction: a `Phase` protocol (`run/load_from_db`) + driver loop + DAO layer. Phase numbers are `float` with `abs(a−b)<0.01` comparisons — a vestige (`pipeline.py:75,140`; `db.py:314`).

**P1-2. Two parallel extraction stacks.** `screen_mapper.py` (1,240 ln) and `entity_service_resolver.py` (371 ln) re-open and regex files the parsers already turned into `CodeNode`s — own SQL regexes duplicating `sql_extractor`, own endpoint regexes duplicating parser endpoint detection, `if "KafkaTemplate" in content` walls (`screen_mapper.py:1075-1209`), Java-only globs (`:349,803,1100`) making screen mapping blind to C#/Python/JS backends. The graph is the product; these modules ignore it.

**P1-3. Parser-tier duplication.** Four tree-sitter parsers each carry verbatim copies of eight algorithms: `_matches/_captures` (4×), `_extract_state_transitions` (4×, same keyword filter), `_detect_boundaries` (4×), `_extract_call_sites` (4×), field-type/DI resolution (3×), class-fields/bases extraction (4×), plus re-declared constant sets. ~40–50% of each parser is per-language rebinding of shared logic. The import-map tier's `_ImportMapParser` template-method design proves the fix; the tree-sitter tier never got a `TreeSitterParser` base. Registration requires syncing three files with no single source of truth (`lang_detector.py:22`, `file_walker.py:10`, `pipeline.py:593`).

**P1-4. Missing shared primitives (graph layer).** Three divergent `normalize_stem`s + three suffix lists (`fsm_identity.py:74,395`, `entity_correlator.py:60,350`, `federation.py:350,353`); union-find implemented twice + greedy impostor (`fsm_identity.py:735`, `call_graph.py:392`, `federation.py:191`); two BFS implementations, one O(n²) (`call_graph.py:623` vs `entry_point_linker.py:64`); confidence magic numbers scattered across five modules with no tuning surface.

**P1-5. LLM-layer duplication.** Dual cost ledgers (`llm_router._log_usage` hook vs `LLMClient._track_cost`; `invoke_structured` bypasses the router entirely); three hand-rolled retry loops with string-match transience detection (`llm_invoke.py:31-39` + 3 call sites); `invoke_llm_with_meta` duplicates `_invoke_text`'s provider dispatch (`llm_router.py:410-469` vs `:395-407`); `extract_claims` still hand-rolls JSON repair that `invoke_structured`/`json_extract` were built to replace (`self_review.py:72-107`); global mutable router config re-`configure()`d per call (`llm_client.py:148`) — thread-unsafe with multiple configs.

**P1-6. Generator-layer duplication.** DMN/EARS siblings: byte-identical `_sorted_transitions` (`dmn_generator.py:101-104` ≡ `ears_generator.py:156-159`), parallel guard parsing and condition grouping; confidence-cap-at-0.5 policy encoded twice (`doc_generator.py:21,404-407`; `screen_doc_writer.py:107-109`); `embed_docs` is a degraded fork of `embed_chunks` (no hashing/retry/batching, `force_recreate=True` every run — `doc_embedder.py:145` vs `embedder.py:121-341`); no shared `Generator`/frontmatter abstraction across the two generator camps.

**P1-7. Documentation drift on load-bearing guides.** `extension-checklist.md` documents non-existent registration dicts and parser APIs (`parse()`, `get_calls()`, `CallReference`); `resolution-heuristics.md` says 7 levels / normalized scores (code: 6 stages / raw); `decisions.md` says 4 stages and contradicts itself on normalization; `domain_classifier` implements neither documented LCA nor the ≥3-node threshold. For a project whose CLAUDE.md declares "heuristics are load-bearing… update both code and documentation," this is the principle most violated.

## P2 — Performance & scalability

| # | Finding | Evidence |
|---|---|---|
| P2-1 | Phase 13 runs LLM scenario inference **serially** while phases 11/12/17 are threaded | `pipeline.py:1290-1301` |
| P2-2 | O(n²) BFS hotspots (`pop(0)`, visited-on-pop) in scenario slicing and alternate-path detection | `call_graph.py:623-656,862` |
| P2-3 | `_suffix_matches` scans all nodes per pending dotted call | `call_graph.py:510` |
| P2-4 | FSM-identity cross pass is undocumented O(n²) over all fingerprints | `fsm_identity.py:198-214` |
| P2-5 | Whole corpus held in memory end-to-end (nodes, chunks, rag_chunks); source freed but structure resident; resume reloads everything | `pipeline.py:208-226,1096-1097,1799-1830` |
| P2-6 | Per-operation SQLite connections (PRAGMAs re-applied each `get_conn`); per-row INSERT loop for nodes (edges use `executemany`); `retry_on_locked` exists but is never applied | `db.py:346,368`; `pipeline.py:728-753` |
| P2-7 | No prompt caching / no Batches API → paying full price for a batch workload; thread-pool concurrency fights rate limits instead | doc 03 §2.1–2.2 |
| P2-8 | Federation aggregate-field recomputation O(n²·fields) per bucket | `federation.py:191-205` |
| P2-9 | Bedrock embedding is sequential per text (no batch API used, no threading at that call site) | `llm_router.py:563-619` |

Practical ceiling today: comfortable to roughly ~50k nodes / mid-size monoliths on a beefy machine; the O(n²) traversals, single-process memory model, and serial phase 13 are what break first on large corpora.

## P3 — Hygiene

- Swallowed-exception census: ~10 `except Exception` log-and-continue sites in `pipeline.py` alone (list under P0-9); `screen_mapper.py` hides real IO/parse failures behind identical `logger.debug` messages (`:190,330,415,473,936,976,994,1070`).
- `print()` instead of logging: `screen_mapper.py:1228`, `menu_detector.py:150,233`.
- Hardcoded `/tmp/ingest-{doc_id}.md` in gitea push (`ingest/runner.py:269,291`) — collision-prone, ignores scratch conventions.
- `import re` inside hot function bodies (`java.py:343,353,738`, `python_parser.py:705`).
- `config.py` mutates `sys.path` in an import fallback (`config.py:18-22`).
- Opposite grammar-version philosophies: C# queries fail silent to `None` (`csharp.py:36-110`) vs JS fails loud at import (`javascript.py:48-51`).
- `strip_thinking` regex surgery applied to *all* providers' output, incl. stripping `<function_calls>` XML that could legitimately appear in docs about LLM codebases (`llm_router.py:47-80`).
- Dashboard artifact paths (lowercase `bpmn/`, slug-root files) don't match generator DocHub folders (uppercase `BPMN/`) — `dashboard.py:24-38` vs `doc_generator.py:47-52`.
- Offline push copies to `{doc_id}.md` while primary tree writes `{domain}.md` — mirror diverges, self-copy guard dead (`push.py:72-74`).

## Test-coverage gaps

Strong: corpus harness (F1 floors + regression baseline), code embedder, generators, dashboard pure functions. **Zero coverage**: `rag/chat.py` (pure `_build_context`/`_build_prompt` included), `rag/doc_embedder.py` (would have caught P0-2), `retriever.dual_search`, the `viewer/server.py` HTTP layer **including its path-traversal defenses** (the security-critical surface), CLI smoke tests (would have caught P0-1). No root `conftest.py` — fixture boilerplate repeated per file.

## Security notes

- Viewer path handling is defensively written (`_is_within`, `_SAFE_SEGMENT`) but untested; the server binds locally and has no auth — fine for local use, should be stated explicitly in docs if ever exposed.
- `dashboard.search_nodes` correctly escapes `LIKE` input (`dashboard.py:317-328`) — good.
- Scanned-repo source flows into LLM prompts; prompt-injection from hostile repos is inherent to the product category. The deterministic prose validator and claim verification partially mitigate fabricated-output risk, but nothing tags LLM-visible content as untrusted. Worth a short threat-model doc if scanning third-party code becomes a use case.
- API keys resolved from env/config and never logged — checked, clean.
