# Exploring Scan Results — Navigation & Quality Checks

Guide for navigating the output of `discover scan` and assessing its quality. Fastest path is the wiki, `discover serve` (below).

---

## The fast path: `discover serve`

```bash
discover serve            # → http://127.0.0.1:7341 (add --open to launch a browser)
discover status           # scan history, spend, degradations, stale pages
```

The wiki (React SPA bundled in the wheel, works offline) shows:

- **Docs tree** — screens, domains, modules, entities, tables, flows
- **Page reader** — markdown and diagrams, a **confidence badge** (structure / model-written / stub) and a **freshness badge**; a stale page says which file changed and to run `discover scan`
- **Citations** — `file:line` links that open the source next to the page
- **Drill down**: screen → flow → entity (state change) → table, in the right rail; **backlinks** and **search** (⌘K)
- **What drives each change** on entity pages: the trigger, its guard, the flows that reach it and the screens that start them. Who does a step, waits and frequencies are not visible from code, and the page says so (ADR-0007)
- **Notes** — add a note to any page; notes are stored in the index and survive rescans (writes are local-only unless `discover serve --allow-remote`, which prints a token)

The quality widgets of the retired pre-v1 viewer dashboard (confidence histogram, weakest docs) are a follow-up for the wiki's status page.

---

## Output Layout

`discover scan` writes everything under `.discovery/` in the repository:

```
.discovery/
  discovery.db        SQLite index: nodes, call edges, pages, screens, entity FSMs, costs (the source of truth)
  config.yaml         project config (`discover init`)
  docs/screens/       screen spec markdown (drift-hashed)
  exports/            `discover export` / `discover push` output (json, html, markdown)
  reports/            post-scan skill reports (triage, consistency)
  logs/  cache/
```

Every document is a wiki page (`pages` table). The legacy ASIS/ASD/ASSC/PF/BPMN/DMN/EARS tree was removed in
M3.7.b. For tooling outside the wiki, `discover export --format json` writes the portable knowledge graph.

---

## Quality-Check Workflow

Work from cheapest signal to deepest inspection. Stop when you've found enough to answer your question.

### 1. Confidence triage

Every page carries a confidence: `structure` (1.0, built from parsed code), `model-written` (narrative from
`--prose`, grounded by M3.8) or `stub` (generation failed). Lowest first:

```bash
sqlite3 .discovery/discovery.db \
  "SELECT page_id, confidence FROM pages WHERE scan_id = (SELECT scan_id FROM current_scan) \
   AND confidence < 0.65 ORDER BY confidence LIMIT 30"
```

Open the worst ones first in `discover serve`.

### 2. Read the wiki top-down

Start at the overview, then a domain, then follow **Drill down** in the right rail: screen → flow → entity →
table. A screen with no flows means the screen-to-backend mapping found nothing, which is a mapping gap
(M6.18), not proof that the screen is static. An entity transition with "no traced flow reaches it" means the
trigger is not on any traced flow: it is dead code, reached through an unresolved call, or past the depth cap.

### 3. Diagrams

The wiki renders Mermaid (entity lifecycles, ERDs) inline, and `/graph` draws the call graph. For a file
outside the wiki, paste the Mermaid into <https://mermaid.live>. No BPMN is produced (ADR-0007).

### 4. Probe with built-in commands

This is where real quality checking happens — each command pulls from the canonical artifacts, not the rendered markdown.

**Entity-level end-to-end trace**

```bash
discover impact entity:Order
discover --json impact entity:Order > /tmp/order-impact.json
```

Ranks what may break if the entity changes: its writers and readers, the screens and endpoints that reach them, and
the unknowns (unresolved calls, dynamic dispatch). Then open the entity page: **What drives each change** lists
every literal state assignment with its trigger, guard, flows and screens.

**Conversational Q&A over code + docs**

```bash
discover ask "<question>"
```

Searches the index. Every hit cites its page and source lines, so you can probe for coverage gaps. Sample probes: "where are orders paid?", "which screens write the customer table?".

**Ad-hoc SQL audits**

```bash
# Find low-confidence call edges to hand-verify
sqlite3 .discovery/discovery.db \
  "SELECT c.file_path, c.qualified_name, e.callee_name, e.resolved_by, e.confidence
   FROM call_edges e JOIN code_nodes c ON c.id = e.caller_id
   WHERE e.scan_id = (SELECT scan_id FROM current_scan) AND e.confidence < 0.65
   ORDER BY e.confidence LIMIT 30"
```

The graded confidence scheme (a 4-stage cascade plus a stage-0 symbol-index tier, confidences 0.5–1.0) is designed for triage — sort ascending and hand-verify the bottom 10-20 edges. See `guides/call-graph/resolution-heuristics.md` for what each stage means.

### 5. Cross-repo federation

`discover federate` was removed with the pre-v1 CLI (M2.7); a multi-repo workspace is an Exploring roadmap item.
Scan each service on its own and use `discover impact` per repository.

### 6. Export a portable knowledge graph (optional)

For downstream tooling that shouldn't depend on the SQLite file, export one canonical knowledge-graph JSON — nodes, call edges (with confidence + resolution stage), domains, FK relationships, entity FSMs, and a doc/screen index:

```bash
discover export --format json            # → .discovery/exports/json/
discover export --format json --out ./kg   # another directory
```

It's a portable, diffable VIEW of the discovery DB — commit it so teammates and tools can consume scan results without the `.db`.

---

## Suggested First-Pass Recipe

```bash
# (a) Open the wiki and drill down from one screen: screen → flow → entity → table
discover serve

# (b) Pick one important entity and check what may break
discover impact entity:Order

# (c) Ask coverage questions over code + docs
discover ask "<question>"

# (d) Audit low-confidence edges with ad-hoc SQL
sqlite3 .discovery/discovery.db \
  "SELECT c.qualified_name, e.callee_name, e.confidence FROM call_edges e
   JOIN code_nodes c ON c.id = e.caller_id
   WHERE e.scan_id = (SELECT scan_id FROM current_scan) AND e.confidence < 0.65
   ORDER BY e.confidence LIMIT 30"
```

About 10-15 minutes for a first-pass audit on a medium-sized scan.

---

## Quality Targets (from CLAUDE.md)

| Metric | Target |
|--------|--------|
| Call resolution accuracy | ≥ 85% |
| Low-confidence edge ratio | ≤ 15% |
| Parser accuracy per language | ≥ 80% |

If an audit reveals the low-confidence edge ratio exceeds 15%, that's a signal to tune heuristics (see `guides/call-graph/debugging-workflow.md`), not to accept the result.

---

## Known Gaps

- `discover serve` bundles its diagram libraries (mermaid, cytoscape) and works offline.
- No BPMN is generated or rendered: a call graph cannot tell sequence from choice, or see people, waits or timers (ADR-0007). Decision tables (DMN) are markdown tables built from parsed guards. A process model needs observed events (M8.6).

---

## See Also

- `guides/call-graph/resolution-heuristics.md` — what confidence scores mean
- `guides/call-graph/debugging-workflow.md` — triage low-confidence edges
- `guides/pipeline/phase-breakdown.md` — which phase produces which artifact
- `README.md` → "Output" section — canonical file layout reference
