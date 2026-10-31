# CLI v1 specification (hard break)

- **Status:** Accepted
- **Last updated:** 2026-10-08
- **Related items:** M2.1–M2.7, M0.10, M1.3, M1.11, M3.10, M4.5, M5.3, M8.2–M8.4 (`evidence`)
- **Replaces:** `src/ai_discovery/cli.py` (13 commands, 69 option declarations). No aliases for old commands.

## 1. Principles
1. **Zero required flags in a repo.** The project is found from cwd (ADR-0001), so `-p` no longer exists.
2. **One spend knob:** `--prose/--no-prose`. The default is keyless (`--no-prose`) until `init` records otherwise.
3. **Same flag, same meaning everywhere.** Every global flag works on every command.
4. **Machine mode on every command:** `--json` for results, `--progress json` for NDJSON events.
5. **Fail loudly and specifically.** Every error has a reason slug and a documented exit code. "Nothing found" is never exit 0 when a gate was requested.
6. **`--help` stays fast.** Commands are lazily imported, so `--help` must not import tree-sitter, boto3 or sqlite-vec. A test checks this (M2.1).

## 2. Global flags (all commands)

| Flag | Meaning |
|---|---|
| `-C, --repo PATH` | Run as if started in PATH (project root discovery from there) |
| `--state-dir DIR` | Use DIR instead of `<repo>/.discovery/` (read-only sources) |
| `--json` | Emit the result as a single JSON document on stdout; human text goes to stderr |
| `--progress rich\|json\|none` | Progress rendering; `json` = NDJSON events `{event: stage\|warn\|error\|done, ...}` |
| `-v, --verbose` / `-q, --quiet` | Log level (repeatable `-vv`); logs also go to `.discovery/logs/<run>.log` |
| `--version` | Print the version and exit 0 |
| `--no-color` | Also honoured through the `NO_COLOR` env var |

## 3. Commands (12: init, scan, status, doctor, serve, mcp, ask, impact, export, drift, evidence, push)

### `discover init [PATH]`
- **Detection:** finds the stack (languages, frameworks, menu format, abapGit or UI5 markers) and prints what it found.
- **First-run menu** (TTY only):
  1. **Structure only.** No key, no spend: graph, screens, entities, tables, wiki and MCP. *(recommended first)*
  2. **Structure + prose.** Pick a provider and model roles. The estimate is shown before any spend.
  3. **Advanced.** Walks through excludes, encodings, budget, concurrency and serve port.
- **Writes** `.discovery/config.yaml` and `.discovery/.gitignore`.
- **Optionally** writes `.mcp.json` (`--mcp/--no-mcp`, default asks on a TTY and is off otherwise). Everything `init` writes is listed so it can be undone.
- **Flags:**

  | Flag | Effect |
  |---|---|
  | `--yes` | Accept the defaults |
  | `--prose/--no-prose` | Set the spend mode |
  | `--provider NAME` | Choose the LLM provider |
  | `--mcp/--no-mcp` | Write `.mcp.json` or not |
  | `--git-hook/--no-git-hook` | Install or remove the post-commit freshness hook (M2.8; off by default, listed in the init inventory) |
  | `--force` | Overwrite an existing config |
- **Non-TTY without `--yes` or a mode flag:** exit 2 `init_needs_choice`.
- **Stdin EOF:** treated as non-TTY.

### `discover scan`
Incremental by default (ADR-0002).

| Flag | Default | Meaning |
|---|---|---|
| `--full` | off | Ignore parse and LLM caches |
| `--prose/--no-prose` | from config | Enable LLM stages |
| `--only STAGE[,STAGE]` | all | Run only the named stages; their prerequisites are rehydrated |
| `--dry-run` | off | Walk, parse and estimate cost; write nothing; take no lock |
| `--yes` | off | Pre-approve the cost gate |
| `--budget USD` | config | Override `llm.budget_usd` |
| `--quality draft\|final` | config | Model role mapping (replaces `--prod`) |
| `--source URL[@ref]` | — | Clone or fetch a remote repo into the state dir (token via env `DISCOVERY_GIT_TOKEN`, never in the URL) |
| `--watch` | off | Re-scan incrementally on file changes (debounced, same lock; never runs prose unless config enables it) — M2.8 |

**Cost gate** (only with prose; M2.4):

| Estimated spend | Behaviour |
|---|---|
| < $2 | Runs without asking |
| $2–$25 | Asks, default **Yes** |
| > $25 | Asks, default **No** |
| Non-TTY without `--yes` | Exit 2 `cost_gate_no_tty`, with a message naming `--yes` and `--no-prose` |

**End panel**, printed after every scan:
- Counts: files, symbols, edges by origin, unresolved calls, screens, entities, tables and pages.
- Spend: `$x.xx` of `$budget`.
- **Degradations**, e.g. "37 files decoded with cp949 fallback", "2 files over 2048 KB skipped", "WebForms: 0 screens detected".
- **What's next**, at most 2 commands, by priority:
  1. `discover serve` (always, unless an error occurred).
  2. `discover mcp install`, if MCP is not configured.
  3. Otherwise `scan --prose` if keyless, or the biggest degradation fix.

### `discover status`
- **Shows:** index commit vs HEAD (behind by N, dirty), last scan result, stage table (fresh, skipped, failed), stale page count, degradations, spend to date, and whether MCP is configured.
- **Exit codes:** 0 when an index exists; 5 `not_indexed` when none does.

### `discover doctor [--repair]`
- **Checks:**
  - Python version;
  - sqlite FTS5;
  - sqlite-vec loadable;
  - every tree-sitter query compiles;
  - config valid;
  - provider reachable, with model invocable via `--llm` (absorbs `test-llm`);
  - DB migrations at head;
  - lock state;
  - old `data/discovery-output` layout present;
  - `.mcp.json` entry launchable;
  - wheel has a bundled SPA.
- **`--repair`:** clears a stale lock, runs pending migrations, rebuilds FTS, and re-registers MCP. It is not allowed together with `--json`.

### `discover serve`
- **Flags:** `--host 127.0.0.1`, `--port 7341` (`0` = any free port), `--open/--no-open` (default no-open), `--allow-remote`, `--token` (env `DISCOVER_SERVE_TOKEN`; random when omitted).
- **Before an index exists** it still serves (the UI shows "No index yet") and prints a hint; a legacy-only index gets a "run `discover scan`" hint. A taken port fails fast with `port_in_use` (exit 1). `--json` prints `{url, host, port, remote, indexed, notes[, token]}` once, then serves.
- **What it serves:** FastAPI API v1 plus the bundled SPA (`design/wiki-web.md`). It reads `current_scan` read-only.
- **Exposure:** a non-loopback host prints a warning, and also requires `--allow-remote`.

### `discover mcp [serve|install|uninstall|print-config]`
- **`serve`** (default): stdio MCP server (`design/mcp-and-agents.md`). `--tools a,b,+c,-d` selects tools (profiles `default|lean|all`; also `$DISCOVER_MCP_TOOLS`); `--max-tokens N` caps every response (also `$DISCOVER_MCP_MAX_TOKENS`). `--http [--host 127.0.0.1] [--port 7342] [--token T] [--allow-host H]` serves streamable HTTP at `/mcp` instead of stdio; a non-loopback `--host` without a token exits 2 `serve_remote_not_allowed`.
- **`print-config`:** prints the `.mcp.json` snippet, the selected tools and their schema-token cost. `--instructions` also prints the generated AGENTS.md/CLAUDE.md block.
- **`install --instructions agents|claude|both`** writes that block (M5.11); `uninstall --instructions …` removes it. Refused with `not_indexed` before the first scan.
- **`install`:** `--target claude|codex|cursor|vscode|all` (default `claude`), `--scope project|user` (default `project`), `--tools`, `--command` (default `discover` if on PATH, else this executable's absolute path), `--force`. Project scope writes portable files in the repo (`.mcp.json`, `.codex/config.toml`, `.cursor/mcp.json`, `.vscode/mcp.json`); user scope writes `~/.codex/config.toml`, `~/.cursor/mcp.json`, `$COPILOT_HOME/mcp-config.json` with an absolute repo path and a per-repo server name. Claude Code user scope is not edited: the `claude mcp add --scope user …` command is printed. Formats and paths: `src/ai_discovery/mcp/targets/__init__.py`. A second server that launches `discover mcp` (another name, or the enabled Claude Code plugin) is refused without `--force`. `doctor` checks every installed entry launches.
- **`uninstall`:** same `--target`/`--scope`; removes exactly what `install` wrote (TOML: the marker block only; a file or directory it created is removed when left empty).

### `discover ask "QUESTION"`
- **Retrieval:** hybrid FTS5 + vector search over pages, with an answer citing page ids and file:line.
- **Keyless:** returns ranked pages and snippets only, with no synthesis.
- **Flags:** `--k 8`, `--role fast|standard`.

### `discover impact TARGET`
- **TARGET** may be a symbol qualified name, a `file:path`, `table:name`, `screen:id`, `entity:Name`, `--diff FILE`, `--range A..B`, or `--working-tree`.
- **Flags:**

  | Flag | Default | Meaning |
  |---|---|---|
  | `--depth` | 3 | Maximum depth, cap 6 |
  | `--include` | `static,framework,dynamic_hint` | `evidence_kind` values to follow (ADR-0004); `dispatches_to` edges are `evidence_kind=static` |
  | `--format` | `table` | `table`, `md` or `json` |
- **Output:** the ranked blast radius with evidence, `may_break` wording, and the unknowns section.
- **Exit codes:** 5 if the target is not found, never an empty success.

### `discover export`
- **Flags:** `--format markdown|html|json`, `--out DIR` (default `.discovery/exports/<format>`).
- **Formats:** `markdown` writes one file per page (no SDLC folder-tree profile — dropped 2026-10-08). `json` is the knowledge-graph JSON, `schema_version` 2.

### `discover drift`
- **Purpose:** compares `pages.source_hashes` with the working tree. `--type screen|all` (default `all`).
- **Exit codes:** 0 if clean, 6 `drift_detected` if any page drifted, 5 if there is no index (`not_indexed`) **or** nothing to check — no pages/specs with source hashes (`nothing_to_check`). Never 0 when nothing was checked.

### `discover evidence import FILE` (delivered by M8)
- **Purpose:** ingest overlay evidence into `evidence_rows` (never merged into static confidence): coverage (coverage.py JSON with contexts, JaCoCo XML, Cobertura, LCOV — M8.2), OTLP/Jaeger span exports (M8.3), profiler stacks (speedscope/pprof/collapsed — M8.4, Exploring).
- **Flags:** `--kind coverage|otlp|profile` (default: sniffed from content), `--env NAME` (required for `otlp`/`profile`), `--window START..END`.
- **Exit codes:** 0 ok, 2 unrecognized format (`evidence_format_unknown`), 5 `not_indexed`.

### `discover push`
- **Flags:** `--target dochub|gitea`, `--only-failed`, `--dry-run`, `--batch-size N`.
- **URL and credentials:** URLs come from config. Credentials come only from env (`DISCOVERY_DOCHUB_TOKEN`, `DISCOVERY_GITEA_TOKEN`).

## 4. Exit codes

| Code | Name | Examples |
|---|---|---|
| 0 | ok | — |
| 1 | failure | unexpected error, stage failed without degradation path |
| 2 | usage/config | bad flag combo, invalid config key, `cost_gate_no_tty`, `init_needs_choice` |
| 3 | budget | `budget_exceeded` (scan stopped cleanly, partial results kept, `current_scan` not flipped) |
| 4 | lock | `lock_held` |
| 5 | not found | `not_indexed`, `target_not_found`, `project_not_found`, `nothing_to_check` |
| 6 | drift | `drift_detected` (only `drift`) |

## 5. Reason slug registry (`core/errors.py`, new)
Format: snake_case. Slugs are stable, so they can be used in tests and telemetry. The initial set:

`project_not_found`, `not_indexed`, `target_not_found`, `init_needs_choice`, `config_invalid_key`, `config_invalid_value`, `provider_not_configured`, `provider_unreachable`, `model_not_invocable`, `budget_exceeded`, `cost_gate_no_tty`, `cost_gate_declined`, `lock_held`, `migration_failed`, `fts_unavailable`, `sqlite_vec_unavailable`, `grammar_query_failed`, `drift_detected`, `push_auth_failed`, `serve_remote_not_allowed`, `port_in_use`, `contradictory_flags`, `nothing_to_check`, `evidence_format_unknown`.

## 6. Old → new mapping (all 13 old commands)

| Old (`cli.py`) | New | Notes |
|---|---|---|
| `init` | `init` | Writes `.discovery/config.yaml`; menu |
| `scan REPO -p … --resume --resume-from --skip-phases --rescan --budget --prod` | `scan` | Resume is implicit (fingerprints); `--only` replaces skip/resume-from; `--quality final` replaces `--prod`; `--full` replaces `--rescan` |
| `chat` | `ask` | Hybrid search; doc retrieval no longer broken |
| `ingest --target dochub\|gitea` | `push --target` | |
| `ingest-docs DIR --push api\|gitea` | removed | Always crashed (`cli.py:756`); importing external docs moves to Exploring |
| `query SQL --db` | removed | `.discovery/discovery.db` is plain SQLite; docs show the `sqlite3` usage |
| `impact ENTITY` | `impact entity:NAME` | Generalized to any target |
| `federate DIRS` | removed (Exploring: workspace) | |
| `view` | `serve` | New SPA |
| `test-llm` | `doctor --llm` | |
| `export-graph` | `export --format json` | |
| `detect-screens REPO` | `scan --only screens` | |
| `verify-drift REPO --spec-dir` | `drift` | Correct exit codes |
