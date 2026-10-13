# ADR-0003: Stage contract and strangler migration of `pipeline.py`

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related items:** M0.2, M0.6, M1.6, M1.7, M1.8, M1.9, M2.3

## Context
`run_pipeline` (`src/ai_discovery/pipeline.py:293-~1760`) threads about 15 locals between phases: `resolved`, `lang_stats`, `all_nodes`, `scenarios`, `summaries_dict`, `scenario_flows`, `rollups`, `llm_client`, `tier3_model`, `mining_results`, and so on. It has further problems:
- **Resume** is implemented as ad-hoc `else:` branches. Phase 6 reloads from the DB, phase 13 re-calls the LLM and swallows exceptions (`pipeline.py:1308-1316`).
- **Phases 14, 15, 18 and 19 have no skip check**, and the 2e–4 sub-phases (about lines 941-1088) have no checkpoint.
- **Two numbering schemes collide:** pipeline phases versus backbone "Phase 1–4".

A big-bang rewrite would change behaviour and structure at once in the hardest-to-test code. The LLM phases are nondeterministic.

## Decision
1. **Characterize first (M0.2).** Add a golden test that scans 2 fixtures with a recorded-response stub LLM. It snapshots per-table counts, sorted edge tuples and doc paths, and requires two runs to be identical.
2. **Stage contract** (`stages/` new):
   ```python
   class Stage(Protocol):
       name: str            # one of the canonical stage names below
       version: str         # bump when output semantics change
       requires: tuple[str, ...]
       def run(self, ctx: RunContext) -> None: ...
       def rehydrate(self, ctx: RunContext) -> None: ...   # load outputs from DB, no LLM
       def fingerprint(self, ctx: RunContext) -> str: ...  # version + config_hash + input hashes
   ```
   **Canonical stage list (single source; other docs link here).** Order is the default `StageRunner` order; new stages are added here first.

   | Stage | Replaces (current `pipeline.py`) | LLM |
   |---|---|---|
   | `resolve` | repo resolve, DB init, resume checks (pre-phase) | no |
   | `walk` | phase 5 `lang_detect` + file walking | no |
   | `parse` | phase 6 `parse` (+ OpenAPI/GraphQL/proto extractors) | no |
   | `graph` | phase 7 `domain_classify` resolution part (call graph, symbol index, integration correlator) | no |
   | `sql` | SQL/DDL extraction (today inside phase 6/7; grows in M6.8–M6.10) | no |
   | `domains` | phase 7 domain classification | no |
   | `entities` | unnumbered 8a/8b backbone (FSM, entity correlation, DMN/EARS) | no |
   | `slices` | phase 8 `execution_slices` (static flows) | no |
   | `screens` | phase 2 `screen_llm_specs` (mapping is structural; LLM spec only with `--prose`) | opt |
   | `embed` | phases 9 `chunk` + 10 `rag_embed` | embeddings |
   | `summaries` | phase 11 `tier1_summarize` | yes |
   | `flows` | phases 12–13 flow analysis + scenario inference | yes |
   | `rollup` | phase 14 `tier3_doc_rollup` | yes |
   | `visuals` | phase 15 visual artifacts + ONBOARD, phase 16 flow statistics | opt |
   | `review` | phase 17 `self_review` | yes |
   | `render` | phase 18 `render_markdown` (legacy SDLC tree; removed in M3.7) | no |
   | `pages` | new: page build (M3.5, runs as post-scan hook until folded in) | no |
   | `finalise` | phase 19 + `current_scan` flip, GC, degradation summary | no |

   `RunContext` holds layout, config, db handle, scan_id, llm (or None for keyless), the typed outputs of earlier stages, and a `degradations` recorder.
3. **Strangler steps, never mixed in one PR:**
   - M1.6 / M1.7: move each phase's code **verbatim** into a stage function and turn the `else:` branches into `rehydrate()`. `run_pipeline` becomes a list of calls. The golden test must stay unchanged.
   - M1.8: replace the list with `StageRunner`. It skips a stage when the stored fingerprint matches (calling `rehydrate`) and supports `--only`.
   - Behaviour changes (bug fixes, the edge contract) land in separate PRs, after the stage that owns them exists.
4. **Stage names replace phase numbers** everywhere: CLI, DB `phase_checkpoints.phase` → `stage_runs.stage`, docs.
5. **No bare `except` in `stages/`** (ruff BLE001). A degraded stage records `degradations(scan_id, stage, kind, count, sample_json)` (kinds e.g. `encoding_fallback`, `file_too_large`, `parser_error`, `llm_truncated`), and the CLI end panel shows it.

## Consequences
- The golden test is the safety net. Updating it requires a justification line in `docs/roadmap/PROGRESS.md`.
- Lane A owns `pipeline.py` and `stages/` serially (see `docs/roadmap/README.md`). No parallel edits.
- Registries are auto-discovered: stages, parsers, page builders and MCP tools are found via `pkgutil`, so adding one never edits a central list.

## Implementation notes (M1.6–M1.8, 2026-10-12)
- Stages live in `src/ai_discovery/stages/`; `stages/runner.py` holds the ordered `STAGES` list (explicit, not `pkgutil`-discovered: order matters and every stage needs a spec). `resolve` runs before the runner (it creates the scan row); `domains` runs inside `graph` (one phase-7 checkpoint).
- Each `StageSpec` names its gated phases and consumed config keys. Fingerprint = sha256(version, consumed config, commit, upstream fingerprint) — a change propagates downstream.
- Decisions: `reuse` (complete `stage_runs` row for this scan with the same fingerprint, or `--only` excludes it and output is stored) → phases suppressed, the stage's resume branch reloads; `forced` (fingerprint changed, or named by `--only`) → phases forced to run; otherwise the old phase gate decides. Reuse within the same scan only — a new scan has nothing stored.
- `phase_checkpoints` rows keep phase numbers as aliases; `stage_runs` carries stage names.
- Blind excepts in `stages/` keep their behaviour but log a debug traceback (`exc_info=True`, which satisfies ruff `BLE001`) and record a `degradations` row; stages log through `logging.getLogger("ai_discovery.pipeline")` so log names did not change.

