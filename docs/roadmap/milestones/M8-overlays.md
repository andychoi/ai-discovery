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
  - Churn/hotspot/ownership/co-change feed a separate `risk` section of M5.2 change impact (`{hotspots, owners, co_change}`), never merged into `items` (`research/agent-platform-assessment.md` §3).
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

### M8.5 Rationale overlay + `get_why`
- **Status:** Planned
- **Size:** M
- **Lane:** G
- **Depends on:** M8.1, M5.3
- **Owns:** `src/ai_discovery/overlays/rationale.py` (new), `src/ai_discovery/mcp/tools/get_why.py` (new; auto-discovered tool, no central-list edit), one migration file under `store/migrations/` (number reserved in PROGRESS.md)
- **Acceptance:**
  - Mines rationale records with provenance (`evidence_kind=rationale`, source = adr | commit | pr_message | code_comment) from ADR-style files (`docs/adr/`, `doc/adr/`, `*decisions*.md`), commit messages touching the target, and `WHY:`/`NOTE:`/`HACK:` comments; each record cites path:line or commit SHA.
  - Agent-proposed decisions from M5.10 are listed with `signer: agent, status: proposed` and never shown as accepted until a human confirms.
  - Opt-in MCP tool `get_why(target|query)` → records ranked by proximity to the target; no records → `status: "unknown"` with the sources searched, never an empty `ok`.
  - Stale flag when the cited lines changed after the record's date.
- **Verify:** `pytest -q tests -k rationale`
- **Out of scope:** mining agent session transcripts; LLM summarisation of rationale (prose layer may add it later).

### M8.6 Observed process discovery
- **Status:** Exploring
- **Size:** L
- **Lane:** G
- **Depends on:** M8.3
- **Owns:** `src/ai_discovery/overlays/process.py` (new)
- **Acceptance:**
  - ADR-0007 point 7: a process model is built only from **observed** events. Sources are status-history or
    audit tables (a configured `table`, `entity id`, `state`, `timestamp` mapping) and M8.3 spans.
    Object-centric event log (OCEL 2.0) → discovered lifecycle variants, frequencies and wait times per entity.
  - Every element of an exported model (BPMN or OCPN) is labelled `observed`, `confirmed` (by a human note) or
    `inferred`. Nothing static-only is exported as a process model.
  - Entity pages show observed variants beside the static lifecycle, as a separate evidence section (M8 rule:
    never merged into static confidence).
- **Verify:** `pytest -q tests -k process_discovery`
- **Out of scope:** live log streaming; reading production databases directly (exports only).

