---
name: roadmap-next
description: Pick up and implement the next ai-discovery v1 roadmap work item (or a named one, e.g. "/roadmap-next M1.6") following docs/roadmap/README.md — claim it, implement within its owned files, verify, and record status + PROGRESS.md. Use when asked to "continue the roadmap", "do the next item", or to work on an M<n>.<m> item.
---

# roadmap-next

Drive one roadmap work item end-to-end. The protocol source of truth is `docs/roadmap/README.md`; if it conflicts with this skill, the README wins.

## Steps

1. **Orient.** Read `docs/roadmap/README.md` and `docs/roadmap/ROADMAP.md`, plus the last ~5 entries of `docs/roadmap/PROGRESS.md`.
2. **Baseline.** Run `pytest -q`. If it fails on main, stop and report — don't start an item on a red baseline.
3. **Pick.** If the user named an item (args), use it. Otherwise run `python scripts/roadmap_lint.py --next` (add `--lane X` if told which lane) and take the first result (★ critical-path items are listed first). Never pick `Exploring` items unless the user explicitly asks. If several lanes have ready items and the user wants parallelism, spawn one sub-agent per item with `isolation: "worktree"`, each told to follow this skill for its single item, and never two lane-A items at once.
4. **Load context.** Open the item in `docs/roadmap/milestones/M*.md`; read every design doc / ADR it references and the matching rows in `docs/roadmap/research/edge-case-catalog.md` and `research/ai-discovery-audit.md`.
5. **Claim.** Set the item's `Status:` to `In progress (rm/<ID>-<slug>, <today>)`, commit only that change to main (if the user allows commits), then create branch `rm/<ID>-<slug>`.
6. **Size check.** If it won't fit one session, split into `<ID>.a/.b` in the milestone file first and do `.a`.
7. **Implement** within the item's **Owns** paths only. Touching another lane's file → stop and note it in PROGRESS.md as a follow-up instead. Do not read or copy repowise source (ADR-0006).
8. **Verify.** Run the item's **Verify** commands and `pytest -q`; also `python scripts/roadmap_lint.py`. Don't regenerate golden/bench baselines just to pass — justify any change in PROGRESS.md.
9. **Close.** Set `Status: Done (<short-sha>)`, append a PROGRESS.md entry using its template, update any design doc/ADR whose contract changed. Commit/PR only if the user has asked for commits; PR description includes "No repowise code was copied or translated."
10. **Report** to the user: item, what changed, verify output, follow-ups, and the next ready items (`roadmap_lint.py --next`).
