# Exploring Scan Results — Navigation & Quality Checks

Guide for navigating the output of `discover scan` and assessing the quality of the generated artifacts. Fastest path is `discover view` (below); for deeper digging, four commands (`view`, `chat`, `impact`, `query`) work against the same artifacts.

---

## The fast path: `discover view`

```bash
discover view -p <slug>    # → opens http://127.0.0.1:8765 in your browser
```

A local read-only web viewer that shows, in one page:

- **Quality targets** — pass/fail vs the CLAUDE.md thresholds (≥85% call-resolution, ≤15% low-confidence)
- **Confidence histogram** — 10-bucket distribution of `call_edges.confidence`
- **Weakest docs** — `generated_docs` ranked by `(unverified_claims DESC, confidence ASC)`, with one-click open
- **Artifact presence** — which of the 11 canonical artifacts (DB, FSM JSON, cross-links, BPMN dir, …) actually got written
- **Docs tree** — every rendered markdown, with confidence / unverified-claim chips
- **Diagram viewers** — mermaid, BPMN, and DMN render inline (CDN libraries; requires internet on first load)

```bash
discover view -p myproj --port 9000 --no-open   # custom port, don't auto-open
```

For programmatic use, `GET /api/summary` returns the same dashboard data as JSON.

---

## Output Layout

`discover scan` writes to two trees with different audiences:

```
data/discovery-output/<slug>/          ← canonical (machine-readable)
  discovery-<slug>.db                  SQLite: nodes, calls, flows, confidences, costs
  entity_state_machines.json           Phase 3 FSM backbone
  cross_entity_transitions.json        Phase 3b/3c cross-entity links
  entity_conditions.json               Phase 3d guard correlations
  entity_backbone.mmd                  Mermaid L1/L2 diagram
  entity_decisions.md                  Decision log
  entity_ears.md                       EARS requirement summary
  bpmn/  dmn/  ears/                   Phase 15 generators (XML / md)

data/<slug>/                           ← human-readable markdown
  ASIS/    as-is — high-level current-state summary
  ASD/     as-is-detail — module-by-module deep dives
  ASSC/    as-is-schema — data-layer / persistence detail
  PF/      process-flow — scenario process flows
  SPEC/ DM/ IF/ …                      (other doc types if generated)
  discovery.log
  discovery.yaml
```

The JSON backbone is the source of truth — `discover impact` and `discover federate` both read it directly, and any downstream tooling should prefer it over the SQLite DB for cross-tool portability (see README.md output section).

---

## Quality-Check Workflow

Work from cheapest signal to deepest inspection. Stop when you've found enough to answer your question.

### 1. Frontmatter triage

Every generated markdown carries `discovery_confidence` and `unverified_claims` in its frontmatter — the per-doc quality signals, written by Phase 17 (self-review).

```bash
# List docs ranked by unverified-claim count
grep -rH "unverified_claims" data/<slug>/ | sort -t: -k3 -n -r

# Or filter low-confidence docs
grep -rHB1 "discovery_confidence: 0\.[0-5]" data/<slug>/
```

Open the worst offenders first.

### 2. Read the markdown

```bash
glow data/<slug>/ASIS/              # terminal preview
# or open the folder in VSCode / Obsidian for graph + preview
```

The four buckets map to SDLC layers: ASIS (as-is), ASD (spec), ASSC (interface), PF (data-model). Start with ASIS to sanity-check that the pipeline understood the repo, then move to the layer relevant to your question.

### 3. View diagrams natively

| Artifact | How to view |
|----------|-------------|
| `entity_backbone.mmd`, `mermaid/*.mmd` | VSCode "Markdown Preview Mermaid" extension, or paste into <https://mermaid.live> |
| `bpmn/*.bpmn` | <https://bpmn.io> demo viewer, or Camunda Modeler |
| `dmn/*.dmn` | bpmn.io (DMN tab) |

Mermaid rendering is the fastest way to eyeball whether entity relationships make structural sense.

### 4. Probe with built-in commands

This is where real quality checking happens — each command pulls from the canonical artifacts, not the rendered markdown.

**Entity-level end-to-end trace**

```bash
discover impact Order -p <slug>
discover impact Order -p <slug> -f /tmp/order-impact.md    # write to file
```

Walks one entity across FSM states, cross-entity links, and guard conditions — validates the *reasoning chain*, not just a doc page. If `entity_state_machines.json` is missing, the scan didn't reach Phase 3 (resume with `--resume`).

**Conversational Q&A over code + docs**

```bash
discover chat -p <slug>
```

Re-indexes both code chunks and generated docs, so you can probe the pipeline's own output for hallucinations and coverage gaps. Sample probes: "what entities were detected?", "which domains have the least coverage?", "show me the weakest-confidence claims in the Orders doc".

**Ad-hoc SQL audits**

```bash
# Find low-confidence call edges to hand-verify
discover query \
  "SELECT src_file, src_name, dst_name, confidence
   FROM call_edge WHERE confidence < 0.65
   ORDER BY confidence LIMIT 30" \
  --db data/discovery-output/<slug>/discovery-<slug>.db
```

The 7-level confidence scheme is designed for triage — sort ascending and hand-verify the bottom 10-20 edges. See `guides/call-graph/resolution-heuristics.md` for what each level means.

### 5. Cross-repo federation (optional)

If you've scanned multiple services, merge them before exploring:

```bash
discover federate ./data/discovery-output/billing \
                  ./data/discovery-output/fulfillment \
                  -o ./data/discovery-output/federated
discover impact Order -p federated -o ./data/discovery-output
```

`impact` works on the federated tree the same way it works on a single scan.

---

## Suggested First-Pass Recipe

```bash
SLUG=myproj

# (a) Open the viewer — quality targets + weakest-docs ranking in one glance
discover view -p $SLUG

# (b) Pick one important entity, validate its full trace
discover impact Order -p $SLUG -f /tmp/order-impact.md
glow /tmp/order-impact.md

# (c) Ask coverage questions over code + docs
discover chat -p $SLUG

# (d) Audit low-confidence edges with ad-hoc SQL
discover query \
  "SELECT src_file, src_name, dst_name, confidence
   FROM call_edge WHERE confidence < 0.65
   ORDER BY confidence LIMIT 30" \
  --db data/discovery-output/$SLUG/discovery-$SLUG.db
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

- `discover view` renders diagrams via CDN libraries (mermaid, bpmn-js, dmn-js). First load requires internet; subsequent loads are browser-cached.
- `discover view` is read-only — for editing/authoring BPMN/DMN you still need Camunda Modeler or bpmn.io.
- `discover chat` re-indexes on every invocation unless `--no-index` is passed; for repeated sessions on the same scan, add `--no-index`.

---

## See Also

- `guides/call-graph/resolution-heuristics.md` — what confidence scores mean
- `guides/call-graph/debugging-workflow.md` — triage low-confidence edges
- `guides/pipeline/phase-breakdown.md` — which phase produces which artifact
- `README.md` → "Output" section — canonical file layout reference
