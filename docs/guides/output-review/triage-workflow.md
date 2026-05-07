# Output Triage: Step-by-Step Workflow

Verify low-confidence rows in the output of a `discover scan` against actual source code, propose targeted corrections, and emit an audit report.

This skill is **reactive and bounded** — it runs over flagged rows only, not the full corpus. A 10-minute run typically costs less than a single Tier-3 LLM call.

---

## When to Use This Skill

- Immediately after a `discover scan` run, before treating its output as authoritative
- Before pushing scan results to DocHub or stakeholders
- After regenerating a section the pipeline flagged as low-confidence
- When a reader spots a doubtful claim and you want to confirm/refute it systematically

**Don't** use this skill for: rewriting whole docs, adding missing scenarios, fixing parser bugs (those are pipeline-level concerns — see `docs/assessments/03-discovery-output-quality-assessment.md`).

---

## Phase 1: Setup (2 min)

The skill must know which scan to triage. Ask the user for both the source repo path and the discovery output path — the source path is needed to verify claims against actual code; the output path is needed to read flagged rows.

```
Required from user:
  - source_repo_path:  e.g. /Users/x/ai/ai-discovery/data/java-springboot-ecommerce-application
  - discovery_output:  e.g. /Users/x/ai/ai-discovery/data/output-java-springboot-ecommerce-application
                       (or project_slug, resolved via `discover view -p <slug>`)
```

Confirm both paths exist and that the discovery output contains a `discovery-<slug>.db` SQLite file with at least one row in `generated_docs`. If not, abort with a clear error — the skill cannot triage what hasn't been scanned.

---

## Phase 2: Pull Flagged Rows (3 min)

Query the DB for low-confidence docs and contradicted claims. Two scopes — start with the doc-level scan, then drill into specific claims.

### Step 2.1: Doc-level confidence scan

```sql
-- Docs whose blended confidence dropped below 0.65 after self-review.
-- These are the targets for triage; high-confidence docs need no review.
SELECT domain, doc_type, confidence, unverified_claims, verified_row_count
FROM generated_docs
WHERE scan_id = (SELECT MAX(id) FROM scan_runs)
  AND confidence < 0.65
ORDER BY confidence ASC;
```

A confidence of 0.5 with `verified_row_count = 20` is structurally different from 0.5 with `verified_row_count = 0`: the first has many AST-verified rows but a contradicted prose section; the second is mostly-prose and mostly-broken.

### Step 2.2: Contradicted claims (highest priority)

```sql
-- Specific claims self-review flagged as contradicted by source.
-- These are bugs in the LLM's prose synthesis — not ambiguities.
SELECT g.domain, g.doc_type, r.claim_text, r.source_file
FROM review_claims r
JOIN generated_docs g ON g.id = r.doc_id
WHERE g.scan_id = (SELECT MAX(id) FROM scan_runs)
  AND r.status = 'contradicted'
ORDER BY g.domain, g.doc_type;
```

### Step 2.3: Unverified claims (medium priority)

```sql
-- Claims self-review couldn't find evidence for. May be true (RAG missed
-- the supporting code) or false (LLM invented). Triage decides which.
SELECT g.domain, g.doc_type, r.claim_text
FROM review_claims r
JOIN generated_docs g ON g.id = r.doc_id
WHERE g.scan_id = (SELECT MAX(id) FROM scan_runs)
  AND r.status = 'unverified'
ORDER BY g.domain, g.doc_type;
```

---

## Phase 3: Verify Each Flagged Claim (5–15 min)

For each flagged claim, run a targeted source-code check. The pattern is the same regardless of claim shape:

1. Identify the smallest source surface that should contain (or refute) the claim
2. Read that source directly with `Read` or `Bash grep`
3. Decide one of: **accept** (claim is correct, RAG missed it), **correct** (claim is wrong, propose replacement text), **flag** (genuinely ambiguous, escalate to human)

### Pattern A: Endpoint claims

> "The `/users/register` endpoint accepts POST requests"

```bash
# Look for the actual @-Mapping annotations
grep -rn "@.*Mapping" <source_repo>/src/main/java/.../controller/UserController.java
```

If the actual route is `/users/signup` POST: **correct** — claim text should be `/users/signup`.
If the route does not exist at all: **correct** — propose deletion of the claim.
If the route matches: **accept** — RAG just missed the supporting chunk.

### Pattern B: Entity-field claims

> "The Order entity has a status field"

```bash
grep -n "private.*status\|@Column.*status" <source_repo>/src/main/java/.../entity/Order.java
```

If no match, look for `state`, `orderStatus`, `lifecycle` — common naming variants. If still no match: **correct** — claim is invented.

### Pattern C: Behavioral claims

> "placeOrder runs atomically in a single transaction"

```bash
grep -B2 -A5 "placeOrder" <source_repo>/src/main/java/.../OrderController.java
grep -B2 -A5 "@Transactional" <source_repo>/src/main/java/.../OrderService.java
```

If `@Transactional` is absent on the relevant method: **correct** — propose: "placeOrder is *not* wrapped in @Transactional; partial writes are possible on failure between Order persistence and OrderItem persistence."

### Pattern D: Architectural claims

> "All services depend on AuthenticationService"

```bash
grep -l "AuthenticationService" <source_repo>/src/main/java/.../service/
# Compare result count to total service count
ls <source_repo>/src/main/java/.../service/*.java | wc -l
```

If only 4 of 7 services use it: **correct** — propose precise count rather than "all".

---

## Phase 4: Emit `triage-report.md` (3 min)

Write a single Markdown file at `<discovery_output>/triage-report.md` summarizing findings. Do NOT edit the original `.md` artifacts in place — the report is the audit trail; corrections are applied as a separate step on user confirmation.

```markdown
# Triage Report

**Source:** <source_repo_path>
**Output:** <discovery_output>
**Scan ID:** <id> (<scan_date>)
**Total flagged:** N rows across M docs

## Corrected (proposed)

### docs/output-x/ASIS/sena.md

- **Original:** "The `/user/register` endpoint accepts POST requests"
  **Corrected:** "The `/users/signup` endpoint accepts POST requests"
  **Source:** `UserController.java:18` — `@PostMapping("/signup")` on class with `@RequestMapping("/users")`
  **Confidence delta:** 0.50 → 1.00

- **Original:** "placeOrder runs atomically in a single transaction"
  **Corrected:** "placeOrder is not wrapped in @Transactional; partial writes are possible if OrderItem persistence fails after Order persistence."
  **Source:** `OrderController.java:31` — no @Transactional annotation
  **Severity:** High — affects reliability planning

## Accepted (RAG miss)

- **Claim:** "The Cart entity has a quantity field"
  **Status:** Verified directly in source — `Cart.java:24` has `private int quantity`
  **Note:** Self-review's RAG retrieval missed the supporting chunk; no doc edit needed.

## Flagged for human review

- **Claim:** "The system uses a sliding-window rate limiter"
  **Investigation:** No rate-limit annotations or filter bean found. Could be in middleware not yet scanned, or genuinely missing.
  **Suggested action:** Confirm with engineering whether rate limiting exists at infrastructure layer.

## Statistics

- Contradicted: X resolved (Y corrected, Z accepted)
- Unverified: X resolved (Y corrected, Z accepted)
- Flagged for human: N
```

---

## Phase 5: Apply Corrections (only on confirmation)

Show the user the report and **wait** for confirmation. Do not auto-apply.

On confirmation, two paths:

**Path A — surgical edits (small report).** Edit the affected `.md` files in `<discovery_output>/` directly. Update the corresponding `confidence` and `unverified_claims` in the DB:

```sql
UPDATE generated_docs SET confidence = ?, unverified_claims = ?
WHERE scan_id = ? AND domain = ? AND doc_type = ?;
```

**Path B — targeted re-scan (large report).** If 30%+ of a doc's claims need correction, recommend a phase-targeted re-scan over edit-in-place:

```bash
discover scan <repo> -p <slug> --resume --resume-from=14  # tier3_doc_rollup
```

Re-running tier3 with the corrected source context is often more reliable than line-edits, and the assessment plan's Track 1 already grounds it deterministically.

---

## Failure Modes & Escapes

- **The DB doesn't have `verified_row_count`.** The output predates Track 4 (schema v6). Migrate the DB by running `discover scan ... --resume` once — the migration is idempotent and runs on every connection.
- **Self-review never ran.** Phase 17 (`self_review`) was skipped or budget-blocked. There are no `review_claims` rows. Re-run with `--resume-from=17` before triaging.
- **Source repo path doesn't match the scan.** The scan recorded a different `repo_url` or commit. The triage will produce false negatives because line numbers shift. Confirm with `git log -1` against the scan's recorded commit before proceeding.

---

## Reference Files

- DB schema for flagged rows: `src/ai_discovery/db.py` — `generated_docs`, `review_claims` tables
- Confidence blending logic: `src/ai_discovery/ai/rollup.py` — `blend_confidence()`
- Self-review claim status semantics: `src/ai_discovery/ai/self_review.py` — `verify_claim()`
