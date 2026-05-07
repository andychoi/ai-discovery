# Output Consistency: Step-by-Step Workflow

Verify that the artifacts produced by `discover scan` are internally consistent — every endpoint claimed in ASIS appears in a process flow, every entity in ASSC appears in EARS requirements, every PF scenario references a use case, and so on. Also flag domain-pattern absences (e-commerce missing `logout` / `refund` flows, etc.).

This skill is **read-only** — it never modifies the scan output. Its product is `consistency-report.md`, a severity-ranked list of inconsistencies and missing-flow callouts that humans or downstream skills act on.

---

## When to Use This Skill

- After triage (`/discover-triage`) has cleaned up flagged rows
- As a periodic audit on already-shipped output
- Before publishing scan output to stakeholders
- When you suspect a doc was regenerated and lost its cross-references

**Don't** use this skill for: triaging individual claims (use `/discover-triage`), verifying source code accuracy (also `/discover-triage`), or fixing pipeline bugs.

---

## Phase 1: Setup (1 min)

Ask the user for the discovery output path. Source repo is **not** required — consistency checking is a self-contained audit of the output tree.

```
Required from user:
  - discovery_output:  e.g. /Users/x/ai/ai-discovery/data/output-java-springboot-ecommerce-application
                       (or project_slug, resolved via `discover view -p <slug>`)
Optional:
  - domain_tag:        e.g. "ecommerce", "fintech", "saas" — enables domain-pattern absence checks
                       (read from frontmatter `tags` field if present)
```

Confirm the output path contains the expected DocHub-style folders: `ASIS/`, `ASD/`, `ASSC/`, `PF/`. Optional: `BPMN/`, `DMN/`, `EARS/`, `IMPACT/`. Missing folders are themselves a finding (Phase 3 generators didn't run — see assessment Track 2).

---

## Phase 2: Build the Cross-Artifact Link Graph (2 min)

Walk the output tree, parse frontmatter from every `.md` file, build the link graph from `links_to` arrays.

```python
# Pseudocode for the skill — adapt to actual tools
graph = {}  # doc_id -> {links_to: [...], doc_type, domain, frontmatter}
for md_file in walk(output_path, "*.md"):
    fm = parse_yaml_frontmatter(md_file)
    graph[fm["doc_id"]] = {
        "links_to": fm.get("links_to", []),
        "doc_type": fm.get("source_doc_type") or _infer_from_folder(md_file),
        "domain":   fm.get("domain") or _infer_from_filename(md_file),
        "tags":     fm.get("tags", []),
        "path":     md_file,
    }
```

A doc with empty `links_to` is itself a finding for non-leaf doc types. PF / IMPACT docs in particular should always have links.

---

## Phase 3: Run Consistency Checks (5 min)

Six checks, each producing a severity (`critical | high | medium | low`) and a list of offending docs. Run all six, then sort by severity.

### Check 3.1 — Endpoint coverage (critical)

Every endpoint in any `ASIS/` API table should appear in at least one `PF/` doc. An endpoint with no scenario means nobody documented how it gets called.

```python
asis_endpoints = extract_endpoints_from_tables(asis_docs)  # set of (method, path)
pf_endpoint_refs = extract_endpoint_refs_from_pf(pf_docs)   # set of (method, path)
orphan_endpoints = asis_endpoints - pf_endpoint_refs
```

For each orphan: `[CRITICAL] Endpoint POST /users/signup has no PF scenario.`

### Check 3.2 — Entity coverage (high)

Every entity in `ASSC/` should appear in at least one `EARS/` requirement (if EARS docs exist) and at least one `IMPACT/` doc. An entity with no behavioral requirements means nobody documented its lifecycle.

```python
assc_entities = extract_entities(assc_docs)         # set of entity names
ears_entities = extract_subjects_from_ears(ears_docs)
impact_entities = extract_subjects_from_impact(impact_docs)

orphan_ears = assc_entities - ears_entities
orphan_impact = assc_entities - impact_entities
```

For each orphan: `[HIGH] Entity Order has no EARS requirements.`

### Check 3.3 — Use-case ↔ scenario linkage (high)

Every use case in `ASD/` should map to at least one `PF/` scenario via `links_to`, and every PF scenario should link back to at least one ASD use case.

```python
asd_use_cases = extract_use_cases(asd_docs)  # list of UC-N entries
unlinked_ucs = [uc for uc in asd_use_cases if not any_pf_links_to(uc, graph)]
unlinked_pfs = [pf for pf in pf_docs if not pf["links_to"]]
```

For each: `[HIGH] Use case UC-12 (placeOrder) has no PF scenario linked.`

### Check 3.4 — Schema-actor coherence (medium)

Every actor mentioned in `BPMN/` lanes should correspond to a real entity or external system. Lanes named after entities are a smell — lanes are *actors*, not entities (per `BPMN/` convention).

```python
bpmn_lanes = extract_lanes(bpmn_docs)
assc_entities = extract_entities(assc_docs)
suspicious_lanes = bpmn_lanes & assc_entities  # actors that share names with entities
```

For each: `[MEDIUM] BPMN lane 'Order' looks like an entity name; should be an actor (Customer, AdminUser, PaymentGateway, …).`

### Check 3.5 — DMN gateway coverage (medium)

Every `PF/` decision gateway (described in prose) should have a corresponding `DMN/` decision table for the entity it gates on.

```python
pf_gateways = extract_decision_points_from_pf(pf_docs)  # tuples of (entity, condition)
dmn_decisions = extract_decisions_from_dmn(dmn_docs)
ungated = pf_gateways - dmn_decisions
```

For each: `[MEDIUM] PF scenario placeorder gates on Order.status but no DMN table covers Order.`

### Check 3.6 — Domain-pattern absence (low to high, depends)

For domain-tagged repos, expected scenarios that are missing get flagged — but with **a critical distinction**: missing in source code (code gap) vs missing in docs (discovery failure).

Pattern library — extend per domain:

| Domain     | Expected scenarios |
|------------|--------------------|
| ecommerce  | logout, password-reset, email-confirm, order-cancel, refund, payment-webhook |
| fintech    | logout, kyc-verify, fraud-flag, chargeback, account-freeze, transaction-reversal |
| saas       | logout, password-reset, plan-upgrade, plan-downgrade, billing-failure, account-suspend, account-restore |

For each missing scenario:
1. Grep the source repo for keywords (`logout`, `signOut`, `revokeToken`, etc.) — see Phase 4.
2. If keywords found: `[HIGH] Domain pattern 'logout' appears in source but no PF scenario documents it.` (discovery failure)
3. If keywords absent: `[LOW — CODE GAP] Domain pattern 'logout' missing from both source and docs. Recommend adding to product backlog.`

The distinction matters: a discovery failure is a pipeline bug worth fixing; a code gap is a stakeholder conversation, not a discovery issue.

---

## Phase 4: Source Cross-Check for Domain Patterns (3 min, optional)

Only runs if the user provided `source_repo_path`. For each "missing domain pattern" finding from Check 3.6, grep the source for keywords.

```bash
# logout / signout
grep -rn "logout\|signout\|revokeToken\|invalidateSession" <source_repo>/src

# password reset
grep -rn "passwordReset\|forgotPassword\|resetToken" <source_repo>/src

# refund
grep -rn "refund\|reversePayment\|chargeback" <source_repo>/src

# payment webhook
grep -rn "webhook\|stripeWebhook\|paymentNotification" <source_repo>/src
```

A keyword hit means "code gap" can be downgraded to "discovery failure" or "needs deeper inspection". A clean miss confirms "code gap".

---

## Phase 5: Emit `consistency-report.md` (2 min)

Write the report to `<discovery_output>/consistency-report.md`. Sections by severity. Each finding includes:
- the offending doc(s)
- the rule that was violated
- whether it's a fixable inconsistency or a code gap
- a suggested action

```markdown
# Consistency Report

**Output:** <discovery_output>
**Scan date:** <scan_date>
**Domain tag:** <domain_tag or 'none'>

## Critical

- **Endpoint POST /users/signup has no PF scenario.**
  Rule: every ASIS endpoint must appear in at least one PF doc.
  Action: re-run scenario inference with `discover scan ... --resume-from=13`.

## High

- **Entity Order has no EARS requirements.**
  Rule: every ASSC entity must have an EARS file under `EARS/`.
  Action: confirm Phase 15 (`visual_artifacts`) ran successfully — likely cause is missing FSM transitions for Order.

- **Use case UC-12 (placeOrder) has no PF scenario linked.**
  Rule: every ASD use case must link to at least one PF doc.
  Action: re-run rollup link computation; if persistent, investigate `doc_generator.py` link graph builder.

## Medium

- **BPMN lane 'Order' looks like an entity name; should be an actor.**
  Rule: BPMN lanes are actors (Customer, Admin, …) — entities don't get their own lanes.
  Action: re-run Phase 15 BPMN generator; if persistent, investigate `bpmn_generator.py` lane assignment.

## Low (code gaps, not discovery failures)

- **Domain pattern 'logout' missing from both source and docs.**
  Source grep: 0 matches for `logout|signout|revokeToken`.
  This is a real product gap — users cannot revoke their tokens. Not a discovery failure.
  Action: surface to product / engineering for backlog.

## Statistics

- Total inconsistencies: 12 (3 critical, 4 high, 3 medium, 2 low)
- Code gaps (not discovery failures): 2
- Cross-artifact links checked: 47
- Pattern-library checks run: 6
```

---

## Reading the Report

The intended audience is whoever owns the discovery output's quality:

- **Critical / High** → discovery pipeline issues. Either re-run a phase or escalate to the AI-Discovery maintainer.
- **Medium** → quality issues in artifact generators (BPMN actor logic, DMN gateway coverage). Worth filing as parser/generator improvements.
- **Low (code gap)** → product/engineering conversations. The discovery system is doing its job; the *codebase* lacks the feature.

---

## Failure Modes

- **No `links_to` populated anywhere.** The output predates the cross-artifact link graph (assessment Track 5). Most consistency checks return false positives. Skip them and emit a `[CRITICAL] Output predates Phase 5 link graph; re-scan to enable consistency checking.` finding.
- **Pattern library has no entries for the user's domain.** Skip Check 3.6 and note in the report. The library should be extended in `docs/guides/output-review/consistency-workflow.md` (this file).
- **Output spans multiple repos (federation).** The link graph is cross-repo. Use `discover federate` artifacts as the source of truth for cross-service edges; per-repo checks still run independently.

---

## Reference Files

- Frontmatter shape: `src/ai_discovery/generators/templates/*.md.j2`
- Link graph builder: `src/ai_discovery/generators/doc_generator.py` — `_WITHIN_DOMAIN_LINKS` (assessment Track 5 expands this)
- BPMN actor logic: `src/ai_discovery/generators/bpmn_generator.py`
- DMN decision logic: `src/ai_discovery/generators/dmn_generator.py`
