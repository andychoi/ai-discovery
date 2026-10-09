# ADR-0001: Project layout — `.discovery/` inside the repo

- **Status:** Accepted
- **Date:** 2026-10-08
- **Related items:** M1.2, M1.3, M1.4, M2.1, M2.5, M0.9

## Context
Today a scan writes to two roots with two naming schemes. The DB goes to `<output>/output-<slug>/discovery-<slug>.db` and docs to `<docs_root>/output-<slug>/<PREFIX>/` (`src/ai_discovery/pipeline.py:322-325`). Readers disagree with the writer:
- The viewer still builds `<out>/<slug>/` for docs and diagrams (`src/ai_discovery/viewer/server.py:67-68`).
- `chat` indexes `<out>/output-<slug>/docs`, which nothing writes (`src/ai_discovery/cli.py:307`).
- `verify-drift` defaults to `./data/specs`.

Seven commands require `-p <slug>`, and there is no notion of a "current project". `discovery.yaml` is only read when `-c` is passed (`src/ai_discovery/config.py:259-388`).

## Decision
1. **One project per repository.** State lives in `<repo>/.discovery/`:
   ```
   .discovery/
     config.yaml        # committed (project config, no secrets)
     .gitignore         # "*" then "!config.yaml" "!.gitignore"
     discovery.db       # SQLite: graph, pages, runs, caches
     cache/             # parse cache blobs, embeddings scratch
     logs/              # run logs (one file per run id)
     exports/           # default target of `discover export`
     lock               # single-flight run lock (ADR-0002, M1.4)
     .env               # optional provider keys, 0600, never committed
   ```
2. **Project discovery:** walk up from cwd (or `-C PATH`) to the first directory containing `.discovery/`. Otherwise use `git rev-parse --show-toplevel`, which works in worktrees where `.git` is a file.
3. **Read-only or remote sources** (a cloned URL, a mounted share): `--state-dir DIR` puts the same tree elsewhere. The repo-path → state-dir mapping is recorded in `~/.config/ai-discovery/projects.json` (new).
4. **One resolver for every path:** `core/paths.py` (new) exposes `ProjectLayout`. No other module may build an output path. A grep test enforces this: no `"output-"` literal outside `core/paths.py`.
5. **Project identity:** the slug defaults to the repo directory name. It is stored in `config.yaml` and is only needed for `push` targets.

## Consequences
- `-p/--project-slug` disappears from all commands; the CLI is a hard break (M2).
- The old `./data/discovery-output/...` trees are not migrated. `doctor` detects them and prints the `rm -rf` hint (M2.5).
- Scanning a repo now writes into it. CI or read-only mounts must pass `--state-dir`.
- The viewer/chat/drift path bugs are fixed for free once every reader uses `ProjectLayout`. M0.9 is the interim patch.
