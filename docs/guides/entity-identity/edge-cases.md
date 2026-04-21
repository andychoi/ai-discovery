# Entity Identity — Edge-Case Survey

Companion to `consolidation-algorithm.md`. Catalogs the programming-style, architectural-layer, and dynamic-database cases that stress the fingerprint, with frequency / severity ratings and a disposition column (Phase 2.4 / 2.5 / Phase 3+ / acceptable limit).

**Ratings**

- **Frequency** — how often the pattern appears in real enterprise codebases (high / medium / low)
- **Severity** — how badly the class-based fingerprint breaks on it (high / medium / low)
- **Disposition** — which phase handles it, or whether it's an acknowledged limit

---

## A. Programming-style variations

### A1. Builder pattern

| Field | Value |
|---|---|
| Example | `Order.builder().status("P").build()` (Java Lombok / Guava) |
| Frequency | High |
| Severity | Low |
| Disposition | **No change** — class `Order` still has fields. Builder is call-chain sugar; transitions resolve through receiver type. |

### A2. Immutable records / frozen dataclasses / case classes

| Field | Value |
|---|---|
| Example | `@dataclass(frozen=True) class Order: ...` then `dataclasses.replace(order, status="APPROVED")` |
| Frequency | High (modern Python, Scala, Kotlin) |
| Severity | Medium — fields captured; **transitions missed** |
| Disposition | **Phase 2.6** — add `replace(...)` / `.copy(...)` / `.withX(...)` recognizer to transition detection. Not a fingerprint concern. |

### A3. State pattern (GoF)

| Field | Value |
|---|---|
| Example | `class CreatedState: def approve(self, order): order.state = ApprovedState()` |
| Frequency | Low (modern code prefers enums) |
| Severity | High when present |
| Disposition | **Phase 3** — when a class is referenced only via assignment to another class's field, treat it as a state value and synthesize transitions on the owning entity. Cross-class inference. |

### A4. Redux / flux reducers

| Field | Value |
|---|---|
| Example | `function orderReducer(state, action) { case 'APPROVE': return {...state, status: 'APPROVED'} }` |
| Frequency | High (frontend) |
| Severity | High — no class exists |
| Disposition | **Phase 2.4** partial (classless pass handles fingerprinting by transition fields). **Phase 3** to trace `...state` spreads for non-written fields. |

### A5. Mixins / traits

| Field | Value |
|---|---|
| Example | `class Order(TimestampedMixin, AuditableMixin, Base): ...` |
| Frequency | High |
| Severity | Medium — shared mixin fields inflate similarity across unrelated descendants |
| Disposition | **Phase 2.4** — inherited-field subtraction + mixin detection (class used as base by ≥2 others → don't emit FSM, subtract fields). |

### A6. Python `__slots__`

| Field | Value |
|---|---|
| Example | `class Order: __slots__ = ("id", "status", "total")` |
| Frequency | Low |
| Severity | Low — fields declared via literal tuple, not assignments |
| Disposition | **Phase 2.5** — small extension: detect `__slots__` tuple/list literal and add its strings to `fields`. |

### A7. `typing.NamedTuple` / `TypedDict` / Pydantic

| Field | Value |
|---|---|
| Example | `class OrderDict(TypedDict): id: str; status: str` |
| Frequency | High (modern typed Python) |
| Severity | Low — fields already captured as class-body annotations |
| Disposition | **Phase 2.4** — works today. Minor refinement later: tag as `node_type="data_shape"` vs `"class"` so consumers know it's structural. |

### A8. Prototype-based JavaScript

| Field | Value |
|---|---|
| Example | `function Order() { this.status = 'CREATED' }` + `Order.prototype.approve = function() {}` |
| Frequency | Low in new code, medium in legacy |
| Severity | High |
| Disposition | **Acceptable limit** — prototype-style is legacy. If needed later, walk `this.X` in functions called via `new`. |

### A9. Polymorphism via duck-typing

| Field | Value |
|---|---|
| Example | `def ship(thing): thing.status = "SHIPPED"` |
| Frequency | High (Python, Ruby) |
| Severity | High — creates a spurious `thing` FSM |
| Disposition | **Phase 2.4** — parsers emit `entity_id = enclosing_fn::var` so collisions don't silently merge; rollup pre-filter drops FSMs whose short `entity` is a generic parameter (`thing`, `obj`, `item`, `arg`, `self_`, `result`, `data`) *and* no class with that name exists. |

### A10. Same short name in different modules

| Field | Value |
|---|---|
| Example | `billing.Order` (purchase orders) and `ecommerce.Order` (customer carts) coexist in one repo |
| Frequency | Medium — common in monorepos and bounded-context DDD |
| Severity | Critical — silently merging them corrupts BPMN/DMN/EARS outputs for two distinct business processes |
| Disposition | **Phase 2.4** — parsers emit `entity_id = class.qualified_name` so the rollup groups by `src.billing.Order` vs `src.ecommerce.Order` separately. The consolidator's inheritance/mixin checks further ensure they stay apart, and field sets are usually disjoint. Post-consolidation, display disambiguation rewrites `entity` to `Order (billing)` / `Order (ecommerce)`; `entity_id` is never mutated so downstream joins remain stable. |

---

## B. MVC / architectural-layer patterns

### B1. Domain entity vs. DTO vs. Request/Response

| Field | Value |
|---|---|
| Example | `Order` (domain) + `OrderDto` (transport) + `CreateOrderRequest` (input) + `OrderResponse` (output) |
| Frequency | Nearly universal in enterprise |
| Severity | High |
| Disposition | **Phase 2.4** — asymmetric rules: high Jaccard + balanced ratio → merge; low ratio + high coverage (≥0.8) of the smaller set inside the larger → projection link, don't merge. `OrderDto` with broadly-overlapping fields merges with `Order`; a narrow `CreateOrderRequest` or `OrderResponse` links as projection. Coverage (not Jaccard) is the projection gate because Jaccard drops toward `|smaller|/|larger|` for pure subsets and would reject the cases we want to flag. |

### B2. Active Record vs. Data Mapper

| Field | Value |
|---|---|
| Example | Rails `order.save()` vs. Clean-arch `Order` + `OrderRepository` |
| Frequency | High |
| Severity | Medium |
| Disposition | **Phase 2.4** — Active Record trivially one class. Data Mapper handled by class-backed pass + inherited-field subtraction. |

### B3. CQRS

| Field | Value |
|---|---|
| Example | `CreateOrderCommand` (narrow write) + `OrderReadModel` (wide, denormalized) |
| Frequency | Medium in modern DDD |
| Severity | High |
| Disposition | **Phase 2.5** — same asymmetric projection machinery as B1, extended to detect `*Command` / `*Query` / `*ReadModel` naming. |

### B4. DDD Aggregate Root + Value Objects + Entities

| Field | Value |
|---|---|
| Example | `Order` (root) + `OrderItem` (entity in aggregate) + `Money` (value object) |
| Frequency | High in DDD codebases |
| Severity | Medium — must NOT merge the inner entities |
| Disposition | **Phase 2.4** — low Jaccard naturally keeps them apart. Aggregate relationships are a Phase 3 concern. |

### B5. MVVM ViewModel

| Field | Value |
|---|---|
| Example | `OrderViewModel` wraps `Order` with observable properties |
| Frequency | High in desktop / mobile |
| Severity | Medium — similar fields, arguably same entity |
| Disposition | **Phase 2.4** — allow merge, record both `node_type`s in provenance metadata. Reviewers can split if needed. |

### B6. Hexagonal / Ports & Adapters

| Field | Value |
|---|---|
| Example | `domain/Order` + `infrastructure/OrderJpaEntity` + `interfaces/rest/OrderResource` |
| Frequency | High in modern enterprise |
| Severity | High |
| Disposition | **Phase 2.4** — domain + persistence merge via field overlap; REST resource links as projection. Same machinery as B1. |

### B7. GraphQL schema vs. resolver

| Field | Value |
|---|---|
| Example | `new GraphQLObjectType({ name: 'Order', fields: {...} })` or SDL `type Order { ... }` |
| Frequency | High in GraphQL APIs |
| Severity | Medium — fields in object literal, not class |
| Disposition | **Phase 2.5** — GraphQL schema extractor produces synthetic `CodeNode` with `node_type="graphql_type"`. 2.4 consolidator picks up via cross pass. |

### B8. Django `forms.ModelForm` / Django `ModelAdmin`

| Field | Value |
|---|---|
| Example | `class OrderForm: class Meta: model = Order; fields = ['status', 'total']` |
| Frequency | High in Django codebases |
| Severity | Medium — fields in nested Meta, string-list literal |
| Disposition | **Phase 2.5** — Meta resolver with cross-reference to `Meta.model`. Framework-specific but localized. |

### B9. DRF / Marshmallow serializers

| Field | Value |
|---|---|
| Example | `class OrderSerializer: class Meta: model = Order; fields = '__all__'` |
| Frequency | High |
| Severity | Medium |
| Disposition | **Phase 2.5** — same Meta resolver as B8. `'__all__'` → resolve from the referenced model's fields. |

### B10. Controllers / Resources

| Field | Value |
|---|---|
| Example | `@RestController class OrderController { @Autowired OrderService service; }` |
| Frequency | Universal |
| Severity | Low — few fields, naturally low overlap with domain entity |
| Disposition | **Phase 2.4** — works correctly by default (no merge). |

---

## C. Complex dynamic database handling

### C1. SQLAlchemy declarative / Django Model / TypeORM decorators

| Field | Value |
|---|---|
| Example | `class Order(Base): __tablename__='orders'; id = Column(...); status = Column(...)` |
| Frequency | Universal in Python ORM / TS ORM |
| Severity | Low — fields captured as class assignments |
| Disposition | **Phase 2.4** — ORM-meta filter (drops `__tablename__`, `__table_args__`, etc.) keeps the fingerprint clean. |

### C2. SQLAlchemy Core (imperative mapping)

| Field | Value |
|---|---|
| Example | `orders = Table('orders', metadata, Column('id', …)); mapper(Order, orders)` |
| Frequency | Low in new code |
| Severity | High — class `Order` may be empty |
| Disposition | **Phase 2.5** — SQL-pattern extractor matches `Table(...)` constructor calls, extracts column args. |

### C3. EAV (entity-attribute-value)

| Field | Value |
|---|---|
| Example | `CREATE TABLE attributes (entity_id, key, value)` — fields are runtime data |
| Frequency | Rare |
| Severity | Unresolvable |
| Disposition | **Acceptable limit** — document as boundary. No static analysis can recover this. |

### C4. Migrations modify schema

| Field | Value |
|---|---|
| Example | `op.add_column('orders', Column('tracking_number', String))` |
| Frequency | Medium (schema-drift scenarios) |
| Severity | Medium — fields in migrations, not in stale model |
| Disposition | **Phase 2.5** — Alembic / Django / Flyway migration parser aggregates `add_column` and `create_table` operations per table. |

### C5. NoSQL + Mongoose schemas

| Field | Value |
|---|---|
| Example | `new mongoose.Schema({ id: String, status: String, total: Number })` |
| Frequency | High in Node.js |
| Severity | Medium — fields in object literal |
| Disposition | **Phase 2.5** — JS object-literal walker anchored on `new mongoose.Schema(...)` / `new Schema(...)`. |

### C6. JSON / JSONB columns

| Field | Value |
|---|---|
| Example | `orders.metadata JSONB` holding `{audit: [...], flags: [...]}` |
| Frequency | High |
| Severity | Low at entity level (the column is captured), high at sub-field level |
| Disposition | **Acceptable limit** for sub-fields. Column-level works today. |

### C7. Polymorphic associations

| Field | Value |
|---|---|
| Example | Django `GenericForeignKey`, Rails `polymorphic_for` |
| Frequency | Medium |
| Severity | Medium — cross-entity relationship |
| Disposition | **Phase 3** — relationship edges, not entity identity. Consolidator correctly leaves `Comment` as its own entity. |

### C8. Multi-tenancy with schema-per-tenant

| Field | Value |
|---|---|
| Example | `tenant_1.orders`, `tenant_2.orders` |
| Frequency | Low |
| Severity | N/A — source code sees one class |
| Disposition | **No change needed** — correctly treated as single entity. |

### C9. Stored procedures / DB triggers

| Field | Value |
|---|---|
| Example | `CREATE PROCEDURE approve_order AS BEGIN UPDATE orders SET status = 'APPROVED' END` |
| Frequency | Medium in legacy |
| Severity | High — business logic invisible to code |
| Disposition | **Phase 5+** — optional SQL-file parser. Out of Phase 2 scope. |

### C10. Dynamic column access

| Field | Value |
|---|---|
| Example | `setattr(order, field_name, value)` / `row[config['status_field']]` |
| Frequency | Medium |
| Severity | High |
| Disposition | **Acceptable limit** — unrecoverable statically. |

### C11. Query builders (Kysely, QueryBuilder, Prisma)

| Field | Value |
|---|---|
| Example | `db.selectFrom('orders').where('status', '=', 'ACTIVE').execute()` |
| Frequency | High in TS / modern backends |
| Severity | Variable |
| Disposition | **Phase 2.5** — same string-literal extraction as raw SQL. Each builder library is a ~50-line pattern. |

### C12. ORM associations / relationships

| Field | Value |
|---|---|
| Example | `items = relationship('OrderItem', backref='order')` |
| Frequency | High |
| Severity | Low at fingerprint level (field captured) |
| Disposition | **Phase 3** — relationship semantics. |

---

## D. Cross-cutting concerns

### D1. Code generation (Protobuf, Thrift, OpenAPI)

| Field | Value |
|---|---|
| Frequency | High |
| Severity | Low if generated files are checked in; High if generated at build time |
| Disposition | **Acceptable limit** when build-time only. When in repo, works normally. |

### D2. Language interop (Python + C extension)

| Field | Value |
|---|---|
| Frequency | Low |
| Severity | High for the C side |
| Disposition | **Acceptable limit** — C parser out of current scope. |

### D3. Lombok `@Data` / `@Builder`

| Field | Value |
|---|---|
| Frequency | Universal in Spring Java |
| Severity | Low — fields still declared as `field_declaration` |
| Disposition | **Phase 2.4** — works today. |

### D4. Kotlin `data class` / `Record`

| Field | Value |
|---|---|
| Frequency | High in Android / Java 14+ |
| Severity | N/A — out of current parser scope |
| Disposition | **Future** — add via `/parser-extension` for Kotlin + Java record support. |

### D5. C# partial classes

| Field | Value |
|---|---|
| Example | `public partial class Order { public string Id; }` in one file, `public partial class Order { public string Status; }` in another |
| Frequency | High in generated C# |
| Severity | Medium — each file produces a partial CodeNode |
| Disposition | **Phase 2.4** — pre-filter merges unconditionally by `qualified_name`. |

---

## Summary table — phase assignment

| Phase | Cases |
|-------|-------|
| **Phase 2.4 (now)** | A5 (mixin subtraction), A7, A9 (generic-name drop), B1, B2, B4, B5, B6, B10, C1 (ORM-meta filter), D3, D5 |
| **Phase 2.5 (non-class field sources)** | A6 (`__slots__`), B3, B7 (GraphQL), B8 (Django forms), B9 (serializers), C2 (SQLA Core), C4 (migrations), C5 (Mongoose), C11 (query builders) |
| **Phase 2.6 / 3 (transition detection + semantic)** | A2 (immutable replace), A3 (state pattern), A4 (reducer spreads), C7 (polymorphic associations), C12 (relationships) |
| **Phase 5+** | C9 (stored procs), future SQL-file parser |
| **Acceptable limits** | A8 (prototype JS), C3 (EAV), C6 (JSONB sub-fields), C8 (tenant schemas), C10 (setattr), D1 (build-time codegen), D2 (C extensions) |

---

## How to use this document

- When adding a new parser or framework extractor, check if any Phase 2.5 / Phase 3 items apply to the target language/ecosystem and note which deferred work unlocks for it
- When a user reports a false merge or missed merge, cross-reference against this table — if the case is listed under "Phase 3" or "Acceptable limit," the behavior is by design
- Reviewers auditing a consolidated FSM can check its `metadata.merge_rule` against the algorithm in `consolidation-algorithm.md`, and its source classes against this matrix to understand the consolidation's quality
