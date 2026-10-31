# M2 — CLI v1 (hard break)

**Goal:** a small, predictable CLI: `init`, `scan`, `status`, `doctor`, `serve`, `mcp`, `ask`, `impact`, `export`, `drift`, `push`, plus `evidence` (delivered by M8). No aliases for old commands.
**Exit criteria:** old `cli.py` deleted; every command has CliRunner tests; README CLI reference generated from the app.
**Refs:** `docs/roadmap/design/cli-v1.md`

### M2.1 `cli/` skeleton ★
- **Status:** Done (f88f67a)
- **Size:** M
- **Lane:** A
- **Depends on:** M1.2, M1.3
- **Owns:** `src/ai_discovery/cli/` (new), `src/ai_discovery/core/errors.py` (new), `src/ai_discovery/core/logging.py` (new), `pyproject.toml` (`[project.scripts]`)
- **Acceptance:**
  - One module per command under `cli/commands/`, lazily imported (`discover --help` imports no tree-sitter/boto3; test asserts via `sys.modules`).
  - Global flags (per `design/cli-v1.md` §2): `-C/--repo PATH`, `--state-dir DIR`, `--json`, `-v/-q`, `--version`, `--no-color`, `--progress rich|json|none` (NDJSON stage/error/done events).
  - Exit codes 0 ok / 1 failure / 2 usage-or-config / 3 budget / 4 lock held / 5 not found-or-not-indexed / 6 drift detected (table in `design/cli-v1.md`); `DiscoveryError(message, reason, hint=None, exit_code=1)` with a reason-slug registry.
  - Logging configured once (stderr, levels from -v/-q); no raw warnings.
- **Verify:** `pytest -q tests/cli`
- **Out of scope:** individual command behaviour beyond stubs.

### M2.2 `init` + first-run menu ★
- **Status:** Done (e9228ea)
- **Size:** S
- **Lane:** A
- **Depends on:** M2.1
- **Owns:** `src/ai_discovery/cli/commands/init.py` (new)
- **Acceptance:**
  - Detects stack (languages, frameworks, menu formats) and shows a 3-option menu: (1) structure only — no key, no spend; (2) with prose — choose provider, shows that an estimate precedes spend; (3) advanced.
  - Non-TTY or stdin EOF: requires `--yes` + mode flags, else exit 2 naming them.
  - Writes `.discovery/config.yaml` + `.gitignore`; optional `--mcp` writes `.mcp.json` entry (M5.3 provides the server).
- **Verify:** `pytest -q tests/cli -k init`
- **Out of scope:** scanning.

### M2.3 `scan` v1 ★
- **Status:** Done (131d914)
- **Size:** M
- **Lane:** A
- **Depends on:** M2.1, M0.10
- **Owns:** `src/ai_discovery/cli/commands/scan.py` (new), `src/ai_discovery/core/hooks.py` (new, post-scan hook registry)
- **Acceptance:**
  - Incremental by default (stage fingerprints when M1.8 is Done; otherwise full), `--full`, `--only`, `--prose/--no-prose` (default `--no-prose`), `--resume`, `--yes`.
  - Named-stage progress; end panel: metrics, degradations (e.g. "12 files decoded as CP949", "0 screens: no menu format matched"), and ≤ 2 "What's next" commands.
  - Post-scan hooks discovered via registry (pages build plugs in at M3.5).
- **Verify:** `pytest -q tests/cli -k scan`
- **Out of scope:** cost gate (M2.4).

### M2.4 Cost gate + `--dry-run`
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M2.3, M1.11
- **Owns:** `src/ai_discovery/cli/commands/scan.py`, `src/ai_discovery/cli/cost_gate.py` (new)
- **Acceptance:**
  - `--dry-run` prints estimate range per stage and exits 0 without writing state or taking the lock.
  - Above $2 confirm (default Yes); above $25 default No; thresholds configurable; non-TTY without `--yes` → exit 2 with reason `cost_gate_no_tty` naming `--yes`/`--no-prose`.
  - Estimate vs actual recorded per run for calibration.
- **Verify:** `pytest -q tests/cli -k cost_gate`
- **Out of scope:** pricing table (M1.11).

### M2.5 `status` + `doctor` ★
- **Status:** Done (1188374)
- **Size:** M
- **Lane:** A
- **Depends on:** M2.1, M1.4
- **Owns:** `src/ai_discovery/cli/commands/{status,doctor}.py` (new)
- **Acceptance:**
  - `status`: indexed commit vs HEAD (behind by N, dirty tree), last run outcome, degradations, costs, stale pages; `--json`.
  - `doctor`: python/sqlite version, FTS5, sqlite-vec / pysqlite3, grammar query compilation, config validity, provider reachability + model invocable (absorbs `test-llm`), lock state, old-layout detection with migration advice; `--repair` clears stale lock, applies migrations, rebuilds FTS; `--json` read-only and incompatible with `--repair`.
- **Verify:** `pytest -q tests/cli -k "status or doctor"`
- **Out of scope:** —

### M2.6 `drift`, `push`, `export`
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M2.1, M3.10
- **Owns:** `src/ai_discovery/cli/commands/{drift,push,export}.py` (new)
- **Acceptance:**
  - `drift`: page/spec source-hash check; exit 0 clean, 6 drifted, 5 not indexed or nothing to check (no pages/specs with source hashes) — never 0 on "not found".
  - `push --target dochub|gitea` absorbs `ingest` + `ingest-docs` with one auth flag vocabulary.
  - `export --format markdown|html|json` delegates to M3.10 (markdown = one file per page); `push` sends that markdown export.
- **Verify:** `pytest -q tests/cli -k "drift or push or export"`
- **Out of scope:** export internals.

### M2.7 `ask` + delete old CLI
- **Status:** Planned
- **Size:** S
- **Lane:** A
- **Depends on:** M5.1, M2.6
- **Owns:** `src/ai_discovery/cli/commands/{ask,impact}.py` (new), `src/ai_discovery/cli.py` (delete), `README.md` (CLI reference section)
- **Acceptance:**
  - `ask` uses the query service (hybrid FTS + vector, RRF) with page-id/file:line citations; `impact` prints M5.2 results.
  - Old commands removed: chat, query, federate, view, test-llm, detect-screens, export-graph, verify-drift, ingest, ingest-docs.
  - README CLI reference generated from the Typer app by a script; test fails if stale.
- **Verify:** `pytest -q tests/cli`
- **Out of scope:** —

### M2.8 Freshness triggers (git hook + watch)
- **Status:** Planned
- **Size:** M
- **Lane:** A
- **Depends on:** M3.6, M1.4
- **Owns:** `src/ai_discovery/core/githook.py` (new), `src/ai_discovery/cli/commands/init.py` (`--git-hook/--no-git-hook`), `src/ai_discovery/cli/commands/scan.py` (`--watch`), `tests/cli/`
- **Acceptance:**
  - `discover init --git-hook` installs a post-commit hook (chained with any existing hook, marker-delimited) that starts a detached incremental keyless `scan`; it respects the run lock (skips if held), never blocks or fails the commit, and logs to `<state>/logs/hook.log`. `--no-git-hook` removes exactly what was added; `init`'s inventory lists it; `doctor` reports hook installed / stale / missing.
  - `discover scan --watch` re-scans on file changes with a debounce (default 2 s) and the same lock; `--progress json` emits one event per rescan.
  - Prose (LLM) never runs from the hook or watch unless `--prose` is set explicitly in config — no surprise spend.
  - Worktrees and detached HEAD handled (hook resolves the repo root; skips during rebase/merge).
- **Verify:** `pytest -q tests/cli -k "githook or watch"`
- **Out of scope:** webhooks, schedulers, a daemon (`research/agent-platform-assessment.md` §3 — skipped for v1).
