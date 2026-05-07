# Entity Identity Consolidation — Algorithm

**Phase**: 2d (intra-repo) with extension points for 2e (non-class field sources) and Phase 4 (cross-repo).

**Problem**: the same logical business entity appears under multiple class names (`Order`, `OrderEntity`, `Orders`), across layers (domain `Order`, JPA `OrderJpaEntity`, DTO `OrderResponse`), or without any class at all (Redux reducers, SQL-first code, utility functions). Meanwhile, the *inverse* problem exists too — the short name `Order` can appear in two unrelated modules (`billing.Order` vs `ecommerce.Order`) and these must **not** be merged.

**Goal**: merge the ones that genuinely represent the same entity; link the ones that represent projections or views; keep apart the ones that happen to share a base class or name stem (or short name, across modules).

---

## Identity contract: `entity` vs `entity_id`

Every `StateTransition` and `EntityStateMachine` carries two names:

- **`entity`** — short display name (e.g. `Order`). What reviewers see in BPMN/DMN/EARS outputs.
- **`entity_id`** — unique qualified key. For class-backed FSMs, it is the class's `qualified_name` (e.g. `src.billing.order.Order`). For duck-typed receivers with no backing class, it is `{enclosing_function.qualified_name}::{var}` (e.g. `src.util.process::thing`).

Parsers emit `entity_id` at the source; it flows through `call_graph.py` → `fsm_rollup` → SQLite/JSON export unchanged. The rollup **groups by `entity_id`**, not `entity`, so `billing.Order` and `ecommerce.Order` never silently collapse.

Consolidation reasons over both: `entity_id` determines inheritance/mixin lookups and canonical identity of merge groups; `entity` governs stem normalization and display.

---

## Inputs

`consolidate_entities(fsms, nodes)` takes:

- `fsms: list[EntityStateMachine]` — the rollup output from `fsm_rollup.build_entity_state_machines` (already keyed by `entity_id`)
- `nodes: list[CodeNode]` — the full parsed code graph. Used to look up each FSM's backing class by `entity_id == qualified_name`, and to read `bases` for the inheritance graph

Output: a new `(consolidated_fsms, projection_links)` pair.

---

## Pre-filters (before any pair scoring)

These hygiene passes run unconditionally and cheaply:

1. **Drop generic-named FSMs.** If the rollup produced an FSM with `entity` in `{"thing", "obj", "item", "arg", "value", "self_", "result", "data"}` and there is no class with that name, it's parser noise from duck-typed code (`def ship(thing): thing.status = 'shipped'`). Drop the FSM; we'd need call-site type inference to do better (Phase 3).
2. **Filter ORM-meta fields** from every class node's `fields` before fingerprinting: `__tablename__`, `__table_args__`, `__abstract__`, `Meta`, `objects`, `DoesNotExist`, `MultipleObjectsReturned`. These are infrastructure, not entity state. (List is extendable per-framework.)
3. **Flag mixin classes.** A class used as a `base` by ≥2 other classes is very likely a mixin or abstract base (`TimestampedMixin`, `AuditableMixin`, `BaseEntity`). We do two things with mixins:
   - **Do not emit FSMs for them** even if transitions touch their fields — they're not entities themselves
   - **Subtract their fields from descendants** before scoring, so `Order {id, created_at, status}` and `Customer {id, created_at, email}` don't merge just because both inherit `id, created_at` from the same mixin
4. **Merge by `qualified_name`.** Same qualified name across files = C# partial classes or generated-file pairs. Union fields, union transitions, keep as one FSM unconditionally.

---

## Pass 1 — Class-backed consolidation

For each FSM that has a backing class `CodeNode` (looked up by `fsms[i].entity` matching `CodeNode.name`), compute:

```python
adjusted_fields[A] = set(A.fields) - filtered_meta - inherited_mixin_fields(A)
name_stem[A]       = normalize(A.entity)  # see below
```

Name stem normalization strips:

- common layer suffixes: `Entity`, `Record`, `Schema`, `Model`, `Dto`, `Dao`, `Data`, `Info`, `Item`
- framework suffixes: `Reducer`, `Slice`, `Saga` (Redux / redux-saga) — so classless FSMs like `orderReducer` + `orderSlice` normalize to `order`
- the plural `s` (heuristic — `Orders` → `Order`, but not `Address` → `Addres`, so we require the stem to still be a known class name to apply)
- common prefixes: `Abstract`, `Base` (only when another class with the shorter name also exists)

For each candidate pair `(A, B)` where `name_stem(A) == name_stem(B)` OR `stem_similarity(A, B) >= 0.6` (Jaro-Winkler):

1. **Hard exclude** if `A` is an ancestor or descendant of `B` in the inheritance graph. Parent/child are semantically different roles even when they share fields.
2. Compute:
   - `ja = jaccard(adjusted_fields[A], adjusted_fields[B])`
   - `ratio = min(|A.adjusted|, |B.adjusted|) / max(|A.adjusted|, |B.adjusted|)` (size-compatibility)
3. **Merge** if `ja >= 0.9 AND ratio >= 0.7`, OR `ja >= 0.7 AND stem_similarity >= 0.8 AND ratio >= 0.7`
4. **Projection link** (no merge) if `ratio < 0.5 AND coverage >= 0.8` where `coverage = |A ∩ B| / min(|A|, |B|)` — the smaller set is mostly contained in the larger. Record `projection_links.append({"entity": X, "projected_by": Y, "coverage": ...})`. Used by `OrderResponse` / `CreateOrderRequest` / `OrderDto`.
   - *Why coverage instead of Jaccard here*: a 2-field DTO against a 7-field entity has Jaccard ≈ 0.29 by construction (union = 7), even when the DTO is a 100% pure subset. Jaccard is symmetric and penalizes asymmetric sizes; coverage is the right measure for "is A mostly inside B?"
5. Otherwise: keep apart.

When merging:

- **Canonical `entity`**: prefer an unsuffixed real class (`Order` over `OrderEntity`); then any real class; then the shortest original. The canonical FSM's `entity_id` is carried through unchanged.
- **All merged transitions are rewritten** to use the canonical `entity` + `entity_id`, so downstream joins on `state_transitions.entity_id` see one identity per merged group.
- Union all `transitions`, `states`, `fields`, `source_files`
- Confidence = average of inputs
- Preserve source in `metadata.consolidated_from = ["Order", "OrderEntity"]`, `metadata.consolidated_ids = ["src.billing.Order", "src.billing.OrderEntity"]`, and `metadata.source_node_types = ["class", "db_model"]` so reviewers can audit

---

## Pass 2 — Classless consolidation

For FSMs with no backing class (Redux reducers, utilities, duck-typed transitions):

```python
classless_fields[A] = {t.field for t in A.transitions}
```

This isn't a real field *set* — it's just the fields observed being written. It's narrower than a class's true shape, but good enough for fingerprinting when class is absent.

Same pair scoring as Pass 1, but without inheritance checks (no class → no `bases`). Thresholds: `ja >= 0.7 AND stem_similarity >= 0.8`.

---

## Pass 3 — Cross consolidation

For each classful FSM `A` and classless FSM `B`:

- if `stem_similarity(A, B) >= 0.8` AND `classless_fields[B] ⊆ adjusted_fields[A]`:
  - merge `B` into `A`

This handles the "raw SQL code touches the orders table, and a separate `Order` class exists" case (relevant once Phase 2e lands).

---

## Post-consolidation: display disambiguation

After all merges settle, distinct FSMs may still share the same short `entity` label — e.g. `billing.Order` and `ecommerce.Order` each survive as their own FSM because their field sets are disjoint. Downstream BPMN/DMN/EARS views would show two unlabeled "Order" rows. To prevent that, consolidation rewrites only the `entity` display field (never `entity_id`) to include a module hint:

- `billing.Order` → display `Order (billing)`; `entity_id` stays `src.billing.Order`
- `ecommerce.Order` → display `Order (ecommerce)`; `entity_id` stays `src.ecommerce.Order`

The module hint comes from the second-to-last segment of `entity_id` (the package above the class), falling back to the first non-`src` segment of a source-file path. Idempotent: re-running consolidation on already-disambiguated FSMs does nothing (the labels no longer collide).

---

## Provenance

Every merged FSM records:

```json
{
  "entity": "Order",
  "entity_id": "src.billing.order.Order",
  "...": "...",
  "metadata": {
    "consolidated_from": ["Order", "OrderEntity", "order_reducer"],
    "consolidated_ids": ["src.billing.order.Order", "src.billing.persistence.OrderEntity", "src.store.order_reducer"],
    "source_node_types": ["class", "db_model", null],
    "merge_rule": "pass1:jaccard_0.93",
    "projection_links": ["src.api.OrderResponse", "src.api.CreateOrderRequest"]
  }
}
```

This makes every merge auditable. A reviewer can regenerate both the pre- and post-consolidation FSMs from the same scan and diff them. `consolidated_ids` is the precise audit trail — `consolidated_from` alone could be ambiguous under the short-name-collision scenario.

---

## Complexity

`O(n²)` pair scoring, but pairs are first filtered by name stem prefix (bucket by first 3 chars), which brings it down to `O(n · k)` where `k` is the average bucket size — typically ≤ 5 for real codebases.

---

## What this algorithm does NOT handle

See `edge-cases.md` for the full matrix. Highlights:

- Immutable-replace transitions (Phase 2f or 3, in transition detection)
- GoF State pattern classes (Phase 3)
- Dynamic column access (`setattr`) — **acceptable limit**
- Stored procedures (Phase 5+)

---

## Extension points for Phase 2e

The algorithm is fingerprint-agnostic. Phase 2e adds new **sources** of `CodeNode.fields` (and synthetic classless nodes) but doesn't change the passes. Specifically:

- Raw SQL → synthetic class node with `name=table_name`, `fields=column_list`, `node_type="sql_table"`
- GraphQL schema → synthetic class node with `name=type_name`, `fields=graphql_fields`, `node_type="graphql_type"`
- Migrations → fields get merged onto the existing model's node at parse-aggregation time

Pass 1 picks them up automatically. Pass 3 handles the cross case.

---

## Extension points for Phase 4 (cross-repo)

Same algorithm, different inputs: `nodes` is unioned across scanned repos; entities merged across language boundaries via `stem_similarity` + `adjusted_fields` overlap. Additional signal: endpoint naming (`POST /orders` in repo A ↔ `consumes orders.created` in repo B). Out of scope for 2d.
