"""Phase 3.1: cross-entity analysis passes over the consolidated FSM set.

This module hosts analyses that only make sense once *all* entities are
known — things a single-entity classifier can't see. Phase 3.1a (now)
detects denormalized fields; Phase 3.1b (planned) will add the cross-
entity transition correlator. They share the same iteration plumbing
over the FSM list, so they live together.

Why denormalization detection matters:
Real schemas rarely stay 3NF — `order(customer_name, customer_email, …)`
copies fields from `customer` for historical accuracy or read-path speed.
The structural classifier heuristics break on these (a denormalized
`order_item(order_id, product_id, product_name, product_price, quantity)`
no longer looks like a junction because of the extra columns). After we
flag those fields as copies, the classifier can subtract them from its
"extra columns" test and recover the right classification.

Heuristic (deliberately narrow, optimized for precision over recall):
A field `foo_bar` on entity A is treated as a denormalized copy from
entity B when:
  1. B's normalized stem equals `foo` (camel → snake, simple depluralize,
     strip `_entity`/`_model`/`_dto` suffixes).
  2. B has a field whose name equals `bar`.
  3. The prefix doesn't match A's own stem (so `order.order_total` is
     treated as a local field, not a self-reference).

Known limitations (accept for v1, queue for a later pass):
  - Single-prefix splitting: `default_shipping_line1` won't match an
    `Address` entity because `default_shipping` isn't an entity name.
  - No transitive chains: if A copies from B and B copies from C, only
    the direct A→B link is recorded.
  - Same-name collisions across modules aren't disambiguated — the first
    matching stem wins. Consolidation in Phase 2.4 should have merged
    true duplicates before we get here.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .models import CrossEntityTransitionLink, EntityStateMachine, Scenario


# Fields that are almost never denormalized — common bookkeeping that
# appears on many entities independently.
_BOOKKEEPING_FIELDS: frozenset[str] = frozenset({
    "id", "uuid", "pk",
    "created_at", "updated_at", "deleted_at",
    "created_on", "updated_on",
    "version", "revision",
})

# Suffixes stripped from entity names before stem matching.
_STEM_STRIP_SUFFIXES: tuple[str, ...] = ("_entity", "_model", "_dto", "_record")

# Camel-to-snake: insert `_` before any uppercase letter that isn't the
# first character. `OrderItem` → `order_item`.
_CAMEL_BOUNDARY_RE: re.Pattern[str] = re.compile(r"(?<!^)(?=[A-Z])")


def detect_denormalization_links(fsms: list[EntityStateMachine]) -> list[EntityStateMachine]:
    """Annotate each FSM with metadata.denormalized_fields.

    Mutates in place and returns the same list. Each link is a dict:
      {
        "field": str,            # the column on this entity
        "from_entity": str,      # display name of the owning entity
        "from_entity_id": str,   # unique id of the owning entity
        "from_field": str,       # the corresponding column on the owner
        "confidence": float,
      }
    Entities with no detected copies get no `denormalized_fields` key, so
    downstream consumers can use `metadata.get("denormalized_fields", [])`.
    """
    stem_index: dict[str, EntityStateMachine] = {}
    for fsm in fsms:
        stem = _normalize_stem(fsm.entity)
        if stem:
            # First writer wins. Consolidation should have already merged true
            # duplicates; any remaining collision is probably noise we can't
            # resolve without caller context.
            stem_index.setdefault(stem, fsm)

    for fsm in fsms:
        own_stem = _normalize_stem(fsm.entity)
        links: list[dict] = []
        for raw_field in fsm.fields:
            field = raw_field.lower()
            if field in _BOOKKEEPING_FIELDS:
                continue
            if field.endswith("_id") and field != "id":
                # FKs are the normalized form of a cross-entity reference —
                # opposite of denormalization, so explicitly excluded.
                continue
            link = _try_match_field(raw_field, stem_index, own_stem, fsm.entity_id)
            if link:
                links.append(link)
        if links:
            fsm.metadata["denormalized_fields"] = links
    return fsms


def _try_match_field(
    field: str,
    stem_index: dict[str, EntityStateMachine],
    own_stem: str,
    own_entity_id: str,
) -> dict | None:
    """Return a denorm link for `field` if one can be inferred, else None.

    Tries progressively longer prefixes — `customer_shipping_city` first
    checks for an entity `customer_shipping`, then `customer`. Longer
    matches are preferred because they're more specific and less prone
    to coincidence.
    """
    lc = field.lower()
    parts = lc.split("_")
    if len(parts) < 2:
        return None
    for i in range(len(parts) - 1, 0, -1):
        prefix = "_".join(parts[:i])
        tail = "_".join(parts[i:])
        if not tail:
            continue
        if prefix == own_stem:
            # `order.order_total` — local naming, not a cross-entity reference.
            continue
        target = stem_index.get(prefix)
        if target is None or target.entity_id == own_entity_id:
            continue
        target_fields_lc = {f.lower() for f in target.fields}
        if tail in target_fields_lc:
            return {
                "field": field,
                "from_entity": target.entity,
                "from_entity_id": target.entity_id,
                "from_field": tail,
                "confidence": 0.75,
            }
    return None


def mine_cross_entity_transitions(
    fsms: list[EntityStateMachine],
    scenarios: list[Scenario],
    min_support: int = 1,
    directional_threshold: float = 0.75,
) -> list[CrossEntityTransitionLink]:
    """Mine ordered cross-entity transition pairs from scenario walks.

    For each scenario, walks `primary_path` in order and collects transitions.
    For every ordered pair of transitions on *different* entities within the
    same scenario, increments a co-occurrence counter. After iterating all
    scenarios, emits one `CrossEntityTransitionLink` per pair that:
      - Appears in at least `min_support` scenarios going A→B, AND
      - Shows a directional ratio (A→B / (A→B + B→A)) ≥ `directional_threshold`.

    The directional gate prevents emitting both (A→B) and (B→A) for a pair
    that bounces back and forth: only the dominant direction survives. Pairs
    that are roughly 50/50 are suppressed entirely — they're probably
    independent transitions that happen to co-occur, not a causal sequence.

    Scope — deliberately simple for v1:
      - Uses only `primary_path` (top-confidence nodes per scenario), not
        alternate paths. Alternates would inflate support with repeats of
        the same logical sequence.
      - Transitions without `entity_id` are skipped (can't group).
      - Self-loops on the same entity are skipped (not "cross-entity").
      - No transitive inference (A→B→C does not imply A→C).
    """
    if not scenarios:
        return []
    # Index entity_id → display name so we can label each side of the link.
    entity_name: dict[str, str] = {f.entity_id: f.entity for f in fsms}

    # {(a_key, b_key): count}  — a_key, b_key are (entity_id, field, to_state) triples.
    pair_counts: dict[tuple, int] = defaultdict(int)
    for scenario in scenarios:
        seq = _ordered_transitions(scenario)
        # Dedup within-scenario: the same transition appearing multiple times
        # in primary_path (rare, but possible with re-entries) should count
        # once per scenario toward the pair, to keep support honest.
        seen_triples: list[tuple] = []
        seen_set: set[tuple] = set()
        for t in seq:
            if not t.entity_id:
                continue
            triple = (t.entity_id, t.field, t.to_state)
            if triple in seen_set:
                continue
            seen_set.add(triple)
            seen_triples.append(triple)
        for i in range(len(seen_triples)):
            a = seen_triples[i]
            for j in range(i + 1, len(seen_triples)):
                b = seen_triples[j]
                if a[0] == b[0]:
                    continue
                pair_counts[(a, b)] += 1

    emitted: dict[tuple, CrossEntityTransitionLink] = {}
    for (a, b), count in pair_counts.items():
        if count < min_support:
            continue
        reverse = pair_counts.get((b, a), 0)
        total = count + reverse
        directional_confidence = count / total if total else 1.0
        if directional_confidence < directional_threshold:
            continue
        # Canonicalize — if both (a,b) and (b,a) pass, emit only the dominant.
        canonical = (a, b) if count >= reverse else (b, a)
        if canonical in emitted:
            continue
        from_triple, to_triple = (a, b) if count >= reverse else (b, a)
        emitted[canonical] = CrossEntityTransitionLink(
            from_entity_id=from_triple[0],
            from_entity=entity_name.get(from_triple[0], from_triple[0]),
            from_field=from_triple[1],
            from_state=from_triple[2],
            to_entity_id=to_triple[0],
            to_entity=entity_name.get(to_triple[0], to_triple[0]),
            to_field=to_triple[1],
            to_state=to_triple[2],
            support=max(count, reverse),
            directional_confidence=max(count, reverse) / total if total else 1.0,
        )
    # Deterministic ordering for stable diffs.
    result = list(emitted.values())
    result.sort(key=lambda l: (
        l.from_entity_id, l.from_field, l.from_state or "",
        l.to_entity_id, l.to_field, l.to_state or "",
    ))
    return result


def _ordered_transitions(scenario: Scenario) -> list:
    """Return transitions in execution order for a scenario's primary_path.

    Only the primary path is walked — alternate paths are skipped here so a
    branching scenario doesn't double-count the same sequence. Nodes without
    a `state_transition` attached are invisible (they're not lifecycle events).
    """
    by_id = {n.id: n for n in scenario.nodes}
    out = []
    for nid in scenario.primary_path:
        node = by_id.get(nid)
        if node is not None and node.state_transition is not None:
            out.append(node.state_transition)
    return out


def _normalize_stem(name: str) -> str:
    if not name:
        return ""
    s = _CAMEL_BOUNDARY_RE.sub("_", name).lower()
    for suffix in _STEM_STRIP_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    # Simple depluralization — catches `customers` → `customer`,
    # `companies` → `company`, but leaves `address` alone.
    if s.endswith("ies") and len(s) > 3:
        s = s[:-3] + "y"
    elif s.endswith("ses") and len(s) > 3:
        s = s[:-2]  # `addresses` → `address`
    elif s.endswith("s") and not s.endswith("ss") and len(s) > 1:
        s = s[:-1]
    return s
