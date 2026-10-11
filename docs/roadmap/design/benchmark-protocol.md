# Design: Benchmark Protocol

- **Status:** Accepted. Reference repos are pinned in `bench/repos.toml` (M0.11).
- **Last updated:** 2026-10-11
- **Related items:** M0.11 (harness), M0.12 (baseline), M1.12 (edge-contract gate), M5.7 (agent-loop eval), M6.13 (CI accuracy gates), M7.1 (SAP repos)
- **Existing assets:**
  - `tests/corpus/` (fixture ground truth, `baseline.json`, `metrics.py`)
  - `tests/corpus/external/` (`manifest.json`, `fetch.py`, `ground_truth/{spring-petclinic,node-express-realworld-example-app,microblog}.json`, `external_baseline.json`)
  - `docs/specs/2026-05-31-corpus-accuracy-harness.md` (stale: it says Phase 3 is pending, but `external/` exists)

The adopted practices come from repowise's published benchmark method (`../research/repowise-benchmark.md`). They are restated here as our own rules.

## 1. Rules
1. **Pre-register before spending.** The metric, the repos and the pass bands are committed in a results stub before the run.
2. **Precision and recall are always reported as a pair.** Either one alone can be gamed.
3. **No pooled recall across repos.** Recall depends on how many entry points or oracle edges each repo has. Report it per repo.
4. **An unknown bucket.** Edges the ground truth or oracle cannot judge are reported separately and charged to no one.
5. **A sealed split.** Hard cases are split into dev and sealed halves by a fixed seed. The sealed half is evaluated only at milestone gates (M1.12, M6.13 releases), never while tuning.
6. **Prove an arm is alive before recording a zero.** A tool that indexed 0 files is a void cell, not a 0 score.
7. **Baselines change only with a justification line** in `docs/roadmap/PROGRESS.md`.
8. **Repowise is a black box.** Install it, run it, and compare its outputs and its MCP answers only. Never read its source to tune ours (ADR-0006).

## 2. Reference repositories

Owner-provided local checkouts live in `../sample-apps/` (ASP.NET Core `eShop`, Java `spring-petclinic`). Use them first. Still uncovered by a real repo: WebForms, VB.NET, JSP, Korean-encoded source, ABAP/UI5. Other rows are candidates, Unverified until pinned.

| Stack | Candidate | Already in corpus? | Notes |
|---|---|---|---|
| Java / Spring | spring-projects/spring-petclinic — **local at `../sample-apps/spring-petclinic` @ `500158f`** (owner-provided) | **Yes** (`tests/corpus/external`; check its pinned SHA vs the local one) | Ground truth exists; records a known call-edge gap; Thymeleaf views, not JSP |
| Java / JSP + MyBatis | mybatis/jpetstore-6 | No | JSP views + MyBatis mappers, so it exercises M6.9 |
| TS/JS backend | node-express-realworld-example-app | **Yes** | |
| Python / Flask | microblog | **Yes** | |
| ASP.NET Core | **dotnet/eShop — local at `../sample-apps/eShop` @ `dc7ea49`** (owner-provided) | No | DI via `AddScoped`, EF Core, Blazor; not WebForms |
| ASP.NET WebForms (C#/VB) | **Owner to supply.** Public WebForms samples are scarce. | No | Prefer a private repo as the sealed split |
| Vue / React SPA | A realworld frontend (Vue 3 and React variants) | No | Routes → screens |
| ABAP (abapGit) | abapGit/abapGit, abap2xlsx/abap2xlsx | No | Confirm in M7.1 |
| UI5 / Fiori | SAP-samples/openui5-sample-app | No | Confirm in M7.1 |
| Korean legacy encoding | Synthetic CP949/EUC-KR fixtures (M0.4) + an owner repo | No | |

**Pinned (M0.11, 2026-10-11):** every row above is SHA-pinned with its license in `bench/repos.toml` (verified with `python -m bench verify-repos`). `active` = spring-petclinic @ `753d35c` (the ground-truth SHA; the owner checkout at `500158f` is newer, so the harness fetches the pinned commit), eShop @ `dc7ea49` (local), node-express-realworld @ `30b68e1`, microblog @ `a975ef6`. `candidate` = jpetstore-6, vue-realworld (now `realworld-apps/…`), react-redux-realworld (archived upstream), UI5 sample-app (now `UI5/sample-app`), abapGit, abap2xlsx. node-express-realworld declares no license: commit derived facts only. WebForms/VB.NET/Korean-encoding rows still need owner repos. `tests/corpus/external/manifest.json` keeps the ground-truth pins; a test asserts both files agree.

## 3. Metrics

| Metric | Definition | Where |
|---|---|---|
| Edge precision | Share of **emitted** call edges that are correct. Measured on a **seeded, stratified random sample of 30 edges per language × origin** (seed recorded), each hand-graded from source with a verdict and reason. Report a 95% CI. | `bench/precision/` (new) |
| Oracle precision | Where a compiler oracle exists (TS: `tsc` symbol resolution; Java/C#: SCIP indexers or Roslyn, **(Unverified availability)**), the share of emitted edges confirmed. Oracle-silent edges go to the unknown bucket. | `bench/oracle/` (new) |
| Edge recall | Share of hand-labelled ground-truth edges found, **per repo** | existing `tests/corpus/metrics.py::score_call_edges` |
| Unresolved share | `unresolved_calls` / call sites, by language and reason (after M1.12) | DB |
| File coverage | Files indexed / eligible files; skipped files listed by reason | run report |
| Screen recall / precision | Detected screens vs hand list | existing corpus dims |
| Table blast radius | For a seeded set of tables: readers and writers found vs ground truth | (new) |
| Change-impact accuracy | **Historical replay.** At commit C, compute the impact of the diff C→C+1. Score against files and tests actually touched by follow-up fix commits within N days. Report precision and recall, with the "co-change only" bucket kept separate. Compare with a **trivial baseline** (rank by fan-in) to show the method beats it. | `bench/impact/` (new) |
| Cost | Wall time, peak RSS, LLM USD and tokens per scan (keyless and prose) | run report, `llm_costs` |
| Doc grounding | Share of cited `file:line` / symbol references that resolve; flagged claims per page | M3.8 |

Doc *quality* (prose usefulness) is **not** scored with an LLM judge until the judge's rerun noise floor has been measured first. Repowise found the noise exceeded every effect.

## 4. Harness layout (M0.11)

```
bench/                       (lane Q; M0.11 built the parts marked ✓)
  __main__.py            ✓  python -m bench list | verify-repos | fetch | run | sample | score
  repos.toml / repos.py  ✓  SHA-pinned repos; local checkout if HEAD == sha, else shallow fetch into
                            tests/corpus/external/.cache/ (gitignored)
  run_ai_discovery.py    ✓  `discover scan --full --no-prose` with a temp --state-dir → metrics JSON
  metrics.py             ✓  files indexed/skipped by reason, nodes by kind, edges by resolved_by,
                            unresolved, screens, wall, $; key-call-edge recall per repo (only at the GT SHA)
  precision/             ✓  sampler (seeded, stratified language × origin) → sheets/*.csv; score.py
                            re-scores graded sheets (Wilson 95% CI per stratum, unknown kept separate)
  run_repowise.py           black box: `repowise init --no-prose -y` (M0.12)
  impact/                   historical replay (later)
  results/                  YYYY-MM-DD-<repo>.json via `run --save` — summaries go to docs/roadmap/benchmarks/
```

Skipped-file reasons are classified by the harness, not the walker: `skip_dir`, `unsupported_extension`, `no_symbols` (a parsable file that produced no nodes). `--gate` is not built yet (CI gating is M6.13).

`--gate` compares the run with the committed baseline. It fails when precision drops beyond the CI band, or when recall drops without a matching rise in `unresolved_calls` (the M1.12 trade-off).

## 5. Results template (`docs/roadmap/benchmarks/YYYY-MM-DD-<label>.md`)

```markdown
# Benchmark: <label> — <date>
- Pre-registration commit: <sha>   Tool versions: ai-discovery <sha>, repowise <version>
- Repos (pinned): <name@sha>, ...
- Seed: <n>   Sealed split used: yes/no

| Repo | Tool | Files idx/eligible | Edges | Precision (95% CI, n) | Recall (per repo) | Unknown | Unresolved share | Wall | $ |
|---|---|---|---|---|---|---|---|---|---|

## Per-origin precision (language × origin)
## Misses decomposed (dynamic dispatch / DI / closure / encoding / parser gap)
## Deviations from pre-registration
## Verdict vs bands
```

## 6. Agent-loop evaluation (M5.7)

This answers the research doc's question: does ai-discovery's MCP reduce Claude Code's "early stop" misses?

- **Tasks:** at least 15 change requests on the pinned repos. Each has a gold diff (a historical commit or a hand-made patch) and a gold blast-radius file list.
- **Arms:** (a) bare Claude Code; (b) Claude Code + ai-discovery MCP + `discover-impact` skill; optionally (c) Claude Code + repowise MCP (black box).
  - Prompts are byte-identical, the index is fresh at the same commit, and the model is fixed.
  - Run order is randomized, because prompt caching biases the first arm.
- **Metrics:**
  - files-touched recall and precision vs the gold list;
  - build/test pass rate after the agent's change (the compiler/test truth loop);
  - **output tokens** as the headline cost metric (not dollars, which caching distorts);
  - whether the agent actually called the tools, reported but not trusted.
- **Bands are pre-registered.** For example: "≥ +15 points file recall at ≤ +20% output tokens = adopt skill as default" **(the numbers are placeholders, to be set at pre-registration)**.
