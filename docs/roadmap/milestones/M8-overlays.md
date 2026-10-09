# M8 — Evidence overlays (git history, coverage, runtime)

**Goal:** add evidence the static graph cannot produce, as **separate provenance** — never merged into static confidence, and "not observed" never counts as negative evidence.
**Exit criteria:** impact results show git/coverage/runtime evidence in their own sections; static-only output remains byte-reproducible.
**Refs:** `docs/roadmap/design/evidence-overlays.md`, `docs/roadmap/research/research-doc-review.md`

### M8.1 Git history layer
- **Status:** Planned
- **Size:** M
- **Lane:** G
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/overlays/git.py` (new)
- **Acceptance:**
  - Churn (decayed), ownership (mailmap-aware), co-change pairs (ignore commits touching > 200 files, merges, bots) stored as `evidence_kind=co_change`; used for ranking/risk, never served as a dependency.
  - Handles shallow clones (warning) and history rewrites (fall back to full walk).
- **Verify:** `pytest -q tests -k overlay_git`
- **Out of scope:** —

### M8.2 Coverage ingestion
- **Status:** Planned
- **Size:** M
- **Lane:** G
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/overlays/coverage/` (new), `src/ai_discovery/cli/commands/` (`discover evidence import` command per `design/cli-v1.md`, coordinated with lane A)
- **Acceptance:**
  - Ingest coverage.py JSON (with dynamic contexts = per-test), JaCoCo XML, Cobertura (coverlet), LCOV → function "executed-by-test" evidence; aggregate (non-per-test) reports labelled as such.
  - Enables "impacted tests" in M5.2 output.
- **Verify:** `pytest -q tests -k coverage_ingest`
- **Out of scope:** running tests for the user.

### M8.3 OTLP / Jaeger span ingestion
- **Status:** Planned
- **Size:** M
- **Lane:** G
- **Depends on:** M1.12
- **Owns:** `src/ai_discovery/overlays/otlp.py` (new)
- **Acceptance:**
  - Ingest OTLP JSON / Jaeger export files; map SERVER spans (route) → child DB spans (`db.statement`/`db.query.text`, parsed by M6.8 when available) and outbound HTTP/messaging → endpoint↔table and endpoint↔external evidence (`evidence_kind=runtime`), labelled with environment + time window.
  - Docs state plainly: zero-code agents yield request-level spans, not method chains.
- **Verify:** `pytest -q tests -k otlp`
- **Out of scope:** running collectors.

### M8.4 Profiler stacks
- **Status:** Exploring
- **Size:** M
- **Lane:** G
- **Depends on:** M8.2
- **Owns:** `src/ai_discovery/overlays/profiles.py` (new)
- **Acceptance:**
  - speedscope / pprof / collapsed-stack ingestion → sampled method-chain evidence (dotnet-trace, JFR-derived, py-spy/sys.monitoring, node `--cpu-prof`); sampled ⇒ absence meaningless.
- **Verify:** `pytest -q tests -k profiles`
- **Out of scope:** —
