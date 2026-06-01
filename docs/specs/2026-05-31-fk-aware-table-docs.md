# Proposal: Foreign-Key-Aware Table Documentation

**Date:** 2026-05-31
**Status:** Proposal (design only — no code yet)
**Relates to:** Assessment `04-reverse-spec-solution-review.md` → HIGH-6 (no FK/relationship extraction)
**Motivation (요청):** 각 테이블 설명서를 만들 때, foreign key가 가리키는 테이블의 context(이름, 설명, 필드 등)를 함께 살펴보며 **테이블의 용도(purpose)** 를 파악해야 한다 — i.e. a table's purpose is often only legible through its relationships, so table docs should reason over FK-referenced tables, not each table in isolation.

---

## 1. The problem

Today every table/entity is documented **in isolation**. `_build_verified_schema_table` (`src/ai_discovery/ai/rollup.py:117-152`) emits, per entity, only: name, field-name list, and base classes:

```
### `OrderItem`  ✓ 1.00
Source: OrderItem.java:12 — extends `BaseEntity`
**Fields:**
- id
- order_id
- product_id
- quantity
```

A reader (or the LLM writing the surrounding prose) sees `order_id` and `product_id` as bare strings. Nothing tells them — or the model — that `order_id → orders.id` and `product_id → products.id`, that `orders` is "a customer purchase" and `products` is "the catalog item". So the model cannot reliably infer that **`OrderItem` is the line-item join between an order and a product** — the single most useful sentence in the table's documentation. It either guesses from the name or omits the purpose.

This is structurally guaranteed because the relationship data is **thrown away at extraction time**:

- **SQL:** `_parse_create_table_columns` explicitly filters out `FOREIGN KEY` / `CONSTRAINT` / `REFERENCES` clauses (`src/ai_discovery/extractors/sql_extractor.py:223-234`). `.sql` migration files — the most authoritative FK source — are also never read (assessment HIGH-6).
- **JPA/EF:** the Java parser stamps `db_model` nodes from `@Entity`/`@Table` (`src/ai_discovery/parsers/java.py:156-157`) but never captures `@ManyToOne` / `@OneToMany` / `@JoinColumn` (no such extraction exists — grep is empty). C#/EF navigation properties likewise.

So the FK edge — the thing that reveals purpose — is invisible to the whole pipeline.

---

## 2. The idea

Treat a table's **foreign-key neighborhood as first-class documentation context**. When documenting table `T`:

1. Resolve every FK on `T` to its referenced table `R` (`T.order_id → orders`).
2. Pull `R`'s context: name, its own one-line purpose (if already derived), and its key fields.
3. Feed that neighborhood — both **outbound** FKs (`T` references `R`) and **inbound** FKs (other tables reference `T`) — into the purpose-inference step.
4. Render a deterministic, AST-verified **Relationships** block in the table doc (like the existing verified-facts tables), and let the LLM write the *purpose prose* grounded in that real neighbor context rather than the table name alone.

Inbound edges matter as much as outbound: a table that *nothing* references but that references many others is usually a transaction/event/junction table; a table referenced by many others is usually a core master/reference entity (e.g. `users`, `products`). The fan-in/fan-out shape is itself a purpose signal.

---

## 3. Design

Three layers, each reusing existing patterns. **Keep the FK facts deterministic (AST-extracted, cited); let the LLM only write the purpose sentence, grounded in the supplied neighbor context.** This preserves the trustworthiness model from assessment §3 (LLM as author-of-prose-around-facts, never author-of-facts).

### Layer 1 — Extract FK edges (fixes the root cause)

Add relationship capture to the extractors and store edges in the graph:

- **SQL** (`sql_extractor.py`): stop discarding `FOREIGN KEY (...) REFERENCES tbl(col)` and inline `col ... REFERENCES tbl(col)`; emit `(from_table, from_col, to_table, to_col)`. Also walk `.sql` / `migrations/` files (currently skipped).
- **JPA** (`parsers/java.py`): on `db_model` fields, capture `@ManyToOne`/`@OneToMany`/`@ManyToMany` + `@JoinColumn(name=…, referencedColumnName=…)`; the referenced entity is the field's declared type.
- **EF** (`parsers/csharp.py`): navigation properties + `[ForeignKey]`.

New persistence: a `db_relationship` table — `(scan_id, from_entity, from_field, to_entity, to_field, cardinality, source_file, source_line, confidence)`. FK from explicit DDL/annotation = confidence 1.0; name-convention-inferred (e.g. `order_id` with no declared FK → `orders`) = lower confidence and clearly flagged as *inferred*.

### Layer 2 — Build the relationship graph + purpose ordering

- Add `EntityRelationshipGraph` (adjacency over `db_relationship`), analogous to the existing entity-FSM rollup in `graph/`.
- Compute per-entity **fan-in / fan-out** degree and a topological-ish ordering so referenced tables are documented (or at least purpose-summarized) **before** the tables that reference them. This means `OrderItem`'s purpose inference can consume an already-derived purpose for `orders` and `products`, not just their names.
- Classify shape heuristically as a *signal, not a verdict*: high fan-in + few fields → **reference/master**; ≥2 outbound FKs + few non-FK fields → **junction/line-item**; FK + status field + timestamps → **transaction/event**. These map cleanly onto the existing 8-kind entity taxonomy.

### Layer 3 — FK-aware table documentation

- **Deterministic Relationships block** (extend `_build_verified_schema_table`): under each entity, render its verified FK edges with citations:

  ```
  ### `OrderItem`  ✓ 1.00
  **Relationships:**
  - `order_id` → `orders` (N:1)        ✓ OrderItem.java:18
  - `product_id` → `products` (N:1)    ✓ OrderItem.java:22
  - referenced by: (none)
  ```

- **Purpose inference, grounded in neighbor context:** build a `_build_relationship_context(entity, graph)` helper that assembles, for the purpose-inference prompt, each neighbor's `{name, derived_purpose_or_field_summary, key_fields}`. The prompt becomes: *"Table `OrderItem` has fields […] and these relationships: references `orders` (a customer purchase: id, user_id, total, status) and `products` (a catalog item: id, name, price). State this table's purpose in one sentence."* The model now has the actual semantic neighborhood, so it produces *"Line items linking an order to the products it contains, with per-product quantity"* instead of guessing.
- Emit purpose with provenance: `purpose` is LLM prose, but tagged with the FK facts it was derived from (cited), so a reader can audit it. If a neighbor's purpose is itself only inferred, mark the dependency.

---

## 4. Worked example

| Table | Fields (verified) | FK edges (verified) | Inferred purpose (LLM, grounded) |
|---|---|---|---|
| `orders` | id, user_id, total, status, created_at | `user_id → users` | "A customer's purchase: who ordered, total, and lifecycle status." |
| `order_items` | id, order_id, product_id, quantity | `order_id → orders`, `product_id → products` | "Line items of an order — which products, in what quantity." |
| `products` | id, name, price, category_id | `category_id → categories` | "Catalog item available for purchase, grouped by category." |

Without FK context, `order_items` reads as "a table with four id-ish columns." With it, its role in the domain is obvious — and **derivable**, not guessed.

---

## 5. Implementation plan

| Phase | Work | Files | Effort |
|---|---|---|---|
| 1 | FK extraction (SQL + JPA + EF), `.sql`/migrations walking | `extractors/sql_extractor.py`, `parsers/java.py`, `parsers/csharp.py`, `repo/file_walker.py` | medium |
| 2 | `db_relationship` table + persistence + migration | `db.py` (schema bump), `pipeline.py` | small |
| 3 | `EntityRelationshipGraph` + fan-in/out + doc ordering | new `graph/entity_relationships.py` | medium |
| 4 | Relationships block + neighbor-context purpose prompt | `ai/rollup.py` (`_build_verified_schema_table`, new `_build_relationship_context`), `generators/templates/as-is-schema.md.j2` | medium |
| 5 | Tests: FK parse fixtures; doc asserts every FK row matches a real `REFERENCES`/`@JoinColumn`; purpose-grounding test | `tests/` | medium |

Reuse, don't reinvent: the verified-facts injection (`rollup.py:352-369`), confidence blending (`blend_confidence`), and `links_to` graph (`doc_generator.py`) already exist — the FK relationship block plugs into all three. The relationship graph mirrors the entity-FSM rollup already in `graph/`.

---

## 6. Faithfulness guarantees (non-negotiable)

- **FK edges are facts, not prose.** Extracted from DDL/annotations, rendered deterministically, cited to `file:line`, `✓ 1.00`. The LLM may never invent or drop an edge — same contract as the endpoint/entity tables.
- **Name-convention inference is separated and flagged.** A `customer_id` column with no declared FK may *suggest* `customers`, but it is rendered as `⚠ inferred (name convention)` at < 1.0 confidence, never as a verified edge. This avoids fabricating relationships that don't exist in the schema.
- **Purpose prose is grounded and auditable.** The one-sentence purpose is LLM-authored but constrained to the supplied verified neighbor context and tagged with the edges it used; it flows through the same self-review/`blend_confidence` pass as other rollup prose (and should — see assessment HIGH-2: that pass must extend beyond today's coverage).
- **Cycles & missing neighbors handled.** Self-referential FKs (`employees.manager_id → employees`) and FKs to tables outside the scanned scope are rendered explicitly ("references external/unknown table `X`"), never silently dropped.

---

## 7. Why this is high-leverage

It converts the schema doc from a column dump into a **relational map a reader can navigate and trust**, and it directly enables better downstream artifacts: entity-impact blast radius (FK edges *are* the blast radius), BPMN data objects, and screen→table provenance. It is the natural companion to the screen-centric work — screens already list `db_tables`, and FK context is what turns that list into "this screen edits an order and its line items, which reference the product catalog."

**Recommendation:** schedule Phase 1–2 (extraction + storage) first as a standalone, independently valuable change (it also unblocks entity-impact accuracy), then 3–4 for the documentation payoff.
