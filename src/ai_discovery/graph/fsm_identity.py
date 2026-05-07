"""Phase 2d: consolidate name-variant FSMs into one entity.

Parsers emit FSMs keyed by `entity_id` — unique per class qualified_name or
per `enclosing_fn::var` for duck-typed receivers. So collisions like
`billing.Order` vs `ecommerce.Order` never silently merge at rollup time.

But that leaves a different problem: the *same* logical entity often appears
under multiple names in one repo — `Order` + `OrderEntity`, domain `Order` +
persistence `OrderJpaEntity`, or `Order` + the `order_reducer` Redux slice.
Those produce distinct FSMs that downstream BPMN / DMN / EARS consumers
should see as one. This module does that merging.

Algorithm (see docs/guides/entity-identity/consolidation-algorithm.md for
the full writeup). Inputs: `list[EntityStateMachine]` + `list[CodeNode]`.
Output: a consolidated FSM list (smaller than the input) and a list of
projection links (subset relationships not strong enough to merge).

Pipeline is:

  1. Pre-filters (generic-name drop, ORM-meta filter, mixin detection).
  2. Fingerprint each FSM — adjusted field set + short-name stem.
  3. Pair-scan within stem buckets (O(n·k)) — classful Pass 1 merges by
     field Jaccard with inheritance guard; classless Pass 2; mixed Pass 3.
  4. Union-find collapses merge groups.
  5. Display-name disambiguation when two kept-apart FSMs share a short
     name (e.g. "Order (billing)" vs "Order (ecommerce)").

Provenance is recorded on every merged FSM's `metadata`: original names,
node types, merge rule, related projections — so reviewers can audit what
got merged and why.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from .models import CodeNode, EntityStateMachine, StateTransition


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Generic parser-noise entity names from duck-typed code. If there is no class
# with the same short name, the FSM is dropped — we'd need call-site type
# inference (Phase 3) to do better than "thing".
_GENERIC_ENTITY_NAMES = frozenset({
    "thing", "obj", "item", "arg", "value", "self_", "result", "data",
})

# Node types whose `fields` contribute to an FSM's shape fingerprint. Regular
# OO classes anchor Pass 1; `sql_table` / `sql_view` synthetics from Phase
# 2e-1 join the same pass so SQL-discovered entities merge with matching
# classful FSMs (`orders` table + `Order` class) instead of orphaning.
_CLASS_LIKE_NODE_TYPES: frozenset[str] = frozenset(
    {"class", "db_model", "sql_table", "sql_view"}
)

# Infrastructure fields that every ORM-mapped class carries. They do not
# represent entity state and must not drive fingerprint similarity.
_ORM_META_FIELDS = frozenset({
    "__tablename__", "__table_args__", "__abstract__",
    "Meta",                              # Django-style inner class
    "objects",                           # Django manager
    "DoesNotExist", "MultipleObjectsReturned",
    "id", "pk",                          # trivially shared across every model
})

# Layer suffixes stripped during stem normalization. Ordered longest-first so
# `OrderDataItem` reduces cleanly to `Order`. Includes Redux-style framework
# suffixes (Reducer/Slice/Saga) so classless FSMs from store modules normalize
# to the underlying entity name.
_LAYER_SUFFIXES = (
    "Reducer",
    "Entity", "Record", "Schema",
    "Slice", "Model",
    "Data", "Info", "Item", "Saga",
    "Dto", "Dao",
)

# Prefixes stripped only when the shorter form is also a real class.
_LAYER_PREFIXES = ("Abstract", "Base")

# Pair-scoring thresholds.
_MERGE_JACCARD_STRICT = 0.9
_MERGE_JACCARD_RELAXED = 0.7
_MERGE_STEM_SIM_RELAXED = 0.8
_MERGE_SIZE_RATIO = 0.7
_PROJECTION_COVERAGE = 0.8
_PROJECTION_RATIO_MAX = 0.5
_PAIR_STEM_SIM = 0.6
_CLASSLESS_JACCARD = 0.7
_CLASSLESS_STEM_SIM = 0.8
_CROSS_STEM_SIM = 0.8


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def consolidate_entities(
    fsms: list[EntityStateMachine],
    nodes: list[CodeNode],
) -> tuple[list[EntityStateMachine], list[dict]]:
    """Merge FSMs that represent the same entity; return links for projections.

    Returns `(consolidated_fsms, projection_links)`:
    - `consolidated_fsms`: new list, may be smaller than `fsms`. Each merged
      entry carries `metadata.consolidated_from`, `source_node_types`,
      `merge_rule`, and (when applicable) `projection_links`.
    - `projection_links`: list of `{"entity", "projected_by", "coverage"}`
      for subset relationships not strong enough to merge (e.g. `OrderDto`
      is a projection of `Order`).

    Non-destructive: inputs are not mutated.
    """
    if not fsms:
        return [], []

    # ------------------------------------------------------------------
    # Index the code graph.
    # ------------------------------------------------------------------
    # entity_id → the class CodeNode that backs it (if any). Unique by
    # construction: parsers use `qualified_name` as entity_id for classes.
    class_by_id = _build_class_by_id(nodes)

    # Short name → list of class nodes with that name. Used for stem
    # normalization ("is 'Base' a real class?") and mixin detection.
    classes_by_name = _build_classes_by_name(nodes)

    # Transitive ancestors per class name, for pair exclusion.
    ancestors_by_id = _build_ancestors_by_id(nodes, classes_by_name)

    # Fields inherited from mixin/abstract ancestors, subtracted before Jaccard.
    mixin_fields_by_id = _build_mixin_fields_by_id(nodes, classes_by_name)

    # Classes used as base by ≥2 descendants — treat as mixins (don't emit).
    mixin_class_names = _find_mixin_class_names(nodes)
    all_class_names = set(classes_by_name)

    # ------------------------------------------------------------------
    # Pre-filter.
    # ------------------------------------------------------------------
    filtered = _prefilter(
        fsms,
        class_by_id=class_by_id,
        mixin_class_names=mixin_class_names,
    )

    # ------------------------------------------------------------------
    # Fingerprint.
    # ------------------------------------------------------------------
    fingerprints = [
        _fingerprint(
            fsm,
            class_by_id=class_by_id,
            mixin_fields_by_id=mixin_fields_by_id,
            all_class_names=all_class_names,
        )
        for fsm in filtered
    ]

    # ------------------------------------------------------------------
    # Pair scan in stem buckets.
    # ------------------------------------------------------------------
    uf = _UnionFind(len(filtered))
    merge_rules: dict[int, str] = {}
    projection_links: list[dict] = []

    buckets: dict[str, list[int]] = defaultdict(list)
    for i, fp in enumerate(fingerprints):
        key = (fp["stem"][:3] if fp["stem"] else "").lower()
        buckets[key].append(i)

    for bucket in buckets.values():
        for a_idx in range(len(bucket)):
            for b_idx in range(a_idx + 1, len(bucket)):
                i, j = bucket[a_idx], bucket[b_idx]
                decision = _score_pair(
                    fingerprints[i],
                    fingerprints[j],
                    ancestors_by_id=ancestors_by_id,
                )
                if decision["action"] == "merge":
                    uf.union(i, j)
                    merge_rules[uf.find(i)] = decision["rule"]
                elif decision["action"] == "project":
                    projection_links.append({
                        "entity": decision["entity"],
                        "projected_by": decision["projected_by"],
                        "coverage": decision["coverage"],
                    })

    # ------------------------------------------------------------------
    # Cross pass (classful + classless merge when subset fields).
    # ------------------------------------------------------------------
    for i, fp_a in enumerate(fingerprints):
        if not fp_a["has_class"]:
            continue
        for j, fp_b in enumerate(fingerprints):
            if i == j or fp_b["has_class"]:
                continue
            if uf.find(i) == uf.find(j):
                continue
            sim = _stem_similarity(fp_a["stem"], fp_b["stem"])
            if sim < _CROSS_STEM_SIM:
                continue
            if not fp_b["fields"]:
                continue
            if fp_b["fields"] <= fp_a["fields"]:
                uf.union(i, j)
                merge_rules[uf.find(i)] = f"pass3:classless_subset_sim_{sim:.2f}"

    # ------------------------------------------------------------------
    # Materialize merged groups.
    # ------------------------------------------------------------------
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(filtered)):
        groups[uf.find(i)].append(i)

    projections_by_target_id: dict[str, list[str]] = defaultdict(list)
    for pl in projection_links:
        projections_by_target_id[pl["entity"]].append(pl["projected_by"])

    consolidated: list[EntityStateMachine] = []
    for root, members in groups.items():
        member_fsms = [filtered[i] for i in members]
        if len(member_fsms) == 1:
            fsm = member_fsms[0]
            projected = projections_by_target_id.get(fsm.entity_id, [])
            if projected:
                fsm = _with_projection_links(fsm, projected)
            consolidated.append(fsm)
        else:
            consolidated.append(_merge_fsms(
                member_fsms,
                merge_rule=merge_rules.get(root, "pass1:unspecified"),
                class_by_id=class_by_id,
                all_class_names=all_class_names,
                projections_by_target_id=projections_by_target_id,
            ))

    # ------------------------------------------------------------------
    # Display-name disambiguation for short-name collisions.
    # ------------------------------------------------------------------
    consolidated = _disambiguate_display_names(consolidated)

    consolidated.sort(key=lambda f: (f.entity, f.entity_id))
    return consolidated, projection_links


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def _build_class_by_id(nodes: list[CodeNode]) -> dict[str, CodeNode]:
    """entity_id → backing class node. entity_id is the class's qualified_name."""
    result: dict[str, CodeNode] = {}
    for n in nodes:
        if n.node_type in _CLASS_LIKE_NODE_TYPES:
            result[n.qualified_name] = n
    return result


def _build_classes_by_name(nodes: list[CodeNode]) -> dict[str, list[CodeNode]]:
    """Short class name → list of matching class nodes (usually one)."""
    result: dict[str, list[CodeNode]] = defaultdict(list)
    for n in nodes:
        if n.node_type in _CLASS_LIKE_NODE_TYPES:
            result[n.name].append(n)
    return dict(result)


def _find_mixin_class_names(nodes: list[CodeNode]) -> set[str]:
    """A class used as a base by ≥2 classes is a likely mixin/abstract base."""
    usage: dict[str, int] = defaultdict(int)
    for n in nodes:
        if n.node_type not in _CLASS_LIKE_NODE_TYPES:
            continue
        for base in n.bases or []:
            usage[base] += 1
    return {name for name, count in usage.items() if count >= 2}


def _ancestors_for_class(
    node: CodeNode,
    classes_by_name: dict[str, list[CodeNode]],
) -> set[str]:
    """Transitive bases up the inheritance tree; returns unqualified names."""
    seen: set[str] = set()
    stack = list(node.bases or [])
    while stack:
        b = stack.pop()
        if b in seen:
            continue
        seen.add(b)
        for parent in classes_by_name.get(b, []):
            if parent.bases:
                stack.extend(parent.bases)
    return seen


def _build_ancestors_by_id(
    nodes: list[CodeNode],
    classes_by_name: dict[str, list[CodeNode]],
) -> dict[str, set[str]]:
    """entity_id (qualified_name) → set of ancestor short names."""
    return {
        n.qualified_name: _ancestors_for_class(n, classes_by_name)
        for n in nodes
        if n.node_type in _CLASS_LIKE_NODE_TYPES
    }


def _build_mixin_fields_by_id(
    nodes: list[CodeNode],
    classes_by_name: dict[str, list[CodeNode]],
) -> dict[str, set[str]]:
    """entity_id → set of fields contributed by mixin/abstract ancestors.

    Subtracting these before computing Jaccard prevents spurious merges
    between sibling classes that share a base mixin's `created_at`, `id`,
    etc. (That's the whole point of mixins — common boilerplate — so their
    fields carry no discriminating power.)
    """
    mixin_names = _find_mixin_class_names(nodes)
    result: dict[str, set[str]] = {}
    for n in nodes:
        if n.node_type not in _CLASS_LIKE_NODE_TYPES:
            continue
        inherited: set[str] = set()
        for ancestor in _ancestors_for_class(n, classes_by_name):
            if ancestor in mixin_names:
                for parent in classes_by_name.get(ancestor, []):
                    if parent.fields:
                        inherited.update(parent.fields)
        result[n.qualified_name] = inherited
    return result


# ---------------------------------------------------------------------------
# Pre-filter
# ---------------------------------------------------------------------------

def _prefilter(
    fsms: list[EntityStateMachine],
    class_by_id: dict[str, CodeNode],
    mixin_class_names: set[str],
) -> list[EntityStateMachine]:
    out: list[EntityStateMachine] = []
    for fsm in fsms:
        # Drop FSMs whose backing class is itself a mixin — mixins are not entities.
        cls = class_by_id.get(fsm.entity_id or "")
        if cls and cls.name in mixin_class_names:
            continue
        # Drop FSMs with generic duck-typed names and no backing class.
        is_generic = fsm.entity.lower() in _GENERIC_ENTITY_NAMES
        if is_generic and cls is None:
            continue
        out.append(fsm)
    return out


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def _fingerprint(
    fsm: EntityStateMachine,
    class_by_id: dict[str, CodeNode],
    mixin_fields_by_id: dict[str, set[str]],
    all_class_names: set[str],
) -> dict:
    cls = class_by_id.get(fsm.entity_id or "")
    if cls is not None:
        raw_fields = set(cls.fields or [])
        inherited = mixin_fields_by_id.get(cls.qualified_name, set())
        fields = raw_fields - _ORM_META_FIELDS - inherited
        has_class = True
    else:
        # Classless fingerprint: whatever fields transitions observed writing.
        # Narrower than a real class shape, but enough for duck-typed merges.
        fields = {t.field for t in fsm.transitions if t.field}
        has_class = False
    stem = _normalize_stem(fsm.entity, all_class_names)
    return {
        "fsm": fsm,
        "fields": fields,
        "stem": stem,
        "has_class": has_class,
    }


def _normalize_stem(name: str, all_class_names: set[str]) -> str:
    """Strip Abstract/Base prefix, layer suffix, and plural-s heuristically."""
    stem = name
    # Prefix: only when stripping leaves a real class name (so `Abstract` →
    # `Base` doesn't accidentally eat legitimate identifiers).
    for prefix in _LAYER_PREFIXES:
        if stem.startswith(prefix) and len(stem) > len(prefix):
            shorter = stem[len(prefix):]
            if shorter in all_class_names:
                stem = shorter
                break
    stem = _strip_layer_suffix(stem)
    # Plural 's' → singular, but only when the singular form is a known class
    # (guards against false positives like `Address` → `Addres`).
    if stem.endswith("s") and len(stem) > 1 and not stem.endswith("ss"):
        singular = stem[:-1]
        if singular in all_class_names:
            stem = singular
    return stem


def _strip_layer_suffix(name: str) -> str:
    for suffix in _LAYER_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# Pair scoring
# ---------------------------------------------------------------------------

def _score_pair(
    fp_a: dict,
    fp_b: dict,
    ancestors_by_id: dict[str, set[str]],
) -> dict:
    """Decide merge / project / skip for a pair. Returns a decision dict."""
    # Stem prefilter.
    stem_a, stem_b = fp_a["stem"], fp_b["stem"]
    sim = _stem_similarity(stem_a, stem_b)
    same_stem = bool(stem_a) and stem_a == stem_b
    if not same_stem and sim < _PAIR_STEM_SIM:
        return {"action": "skip"}

    fsm_a, fsm_b = fp_a["fsm"], fp_b["fsm"]

    # Hard exclude when one is an inheritance ancestor of the other.
    ancestors_a = ancestors_by_id.get(fsm_a.entity_id, set())
    ancestors_b = ancestors_by_id.get(fsm_b.entity_id, set())
    # Compare using short names (ancestors are short-name-qualified since
    # `bases` is unqualified).
    short_a = _short_name(fsm_a)
    short_b = _short_name(fsm_b)
    if short_b in ancestors_a or short_a in ancestors_b:
        return {"action": "skip"}

    fields_a, fields_b = fp_a["fields"], fp_b["fields"]
    if not fields_a and not fields_b:
        return {"action": "skip"}

    ja = _jaccard(fields_a, fields_b)
    if fields_a and fields_b:
        ratio = min(len(fields_a), len(fields_b)) / max(len(fields_a), len(fields_b))
    else:
        ratio = 0.0

    if fp_a["has_class"] and fp_b["has_class"]:
        # Pass 1: classful.
        if ja >= _MERGE_JACCARD_STRICT and ratio >= _MERGE_SIZE_RATIO:
            return {"action": "merge", "rule": f"pass1:jaccard_{ja:.2f}"}
        if (
            ja >= _MERGE_JACCARD_RELAXED
            and sim >= _MERGE_STEM_SIM_RELAXED
            and ratio >= _MERGE_SIZE_RATIO
        ):
            return {
                "action": "merge",
                "rule": f"pass1:relaxed_jaccard_{ja:.2f}_sim_{sim:.2f}",
            }
        # Projection: asymmetric sizes AND the smaller set is mostly contained
        # in the larger. Coverage (not Jaccard) is the subset signal — a pure
        # subset has low Jaccard by construction (|smaller|/|union|), so using
        # Jaccard as the gate would reject exactly the cases we want to flag.
        if ratio < _PROJECTION_RATIO_MAX and fields_a and fields_b:
            if len(fields_a) >= len(fields_b):
                larger, smaller = fp_a, fp_b
            else:
                larger, smaller = fp_b, fp_a
            coverage = (
                len(smaller["fields"] & larger["fields"]) / len(smaller["fields"])
                if smaller["fields"] else 0.0
            )
            if coverage >= _PROJECTION_COVERAGE:
                return {
                    "action": "project",
                    "entity": larger["fsm"].entity_id,
                    "projected_by": smaller["fsm"].entity_id,
                    "coverage": round(coverage, 3),
                }
        return {"action": "skip"}

    if not fp_a["has_class"] and not fp_b["has_class"]:
        # Pass 2: classless. Transitions-only fingerprints are narrow, so
        # require both strong Jaccard and strong stem similarity — no
        # projection handling (the asymmetry signal isn't trustworthy here).
        if ja >= _CLASSLESS_JACCARD and sim >= _CLASSLESS_STEM_SIM:
            return {
                "action": "merge",
                "rule": f"pass2:jaccard_{ja:.2f}_sim_{sim:.2f}",
            }
        return {"action": "skip"}

    # Mixed (classful + classless): handled by the cross pass in the caller.
    return {"action": "skip"}


def _short_name(fsm: EntityStateMachine) -> str:
    # `entity_id` is the qualified_name for class-backed FSMs; strip the
    # module prefix to get the short name. For classless (`fn::var`), the
    # short name is the original `entity` (which is typically `var`).
    eid = fsm.entity_id or fsm.entity
    if "::" in eid:
        return fsm.entity
    if "." in eid:
        return eid.rsplit(".", 1)[-1]
    return eid


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def _merge_fsms(
    fsms: list[EntityStateMachine],
    merge_rule: str,
    class_by_id: dict[str, CodeNode],
    all_class_names: set[str],
    projections_by_target_id: dict[str, list[str]],
) -> EntityStateMachine:
    """Produce one FSM from N FSMs in the same consolidation group."""
    # Canonical identity: prefer a real, un-suffixed class name. If none,
    # prefer a real class. Else shortest original.
    canonical_fsm = _pick_canonical(fsms, class_by_id, all_class_names)
    canonical_entity = canonical_fsm.entity
    canonical_id = canonical_fsm.entity_id or canonical_fsm.entity

    merged_transitions: list[StateTransition] = []
    states: set[str] = set()
    fields: set[str] = set()
    files: set[str] = set()
    for f in fsms:
        for t in f.transitions:
            merged_transitions.append(_rename_transition(t, canonical_entity, canonical_id))
        states |= f.states
        fields |= f.fields
        files |= f.source_files
    confidence = sum(f.confidence for f in fsms) / len(fsms) if fsms else 1.0

    # Provenance. Keep original (pre-merge) names and ids for audit.
    consolidated_from = sorted({f.entity for f in fsms})
    consolidated_ids = sorted({f.entity_id or f.entity for f in fsms})
    source_node_types: list[str | None] = []
    for f in fsms:
        cls = class_by_id.get(f.entity_id or "")
        source_node_types.append(cls.node_type if cls else None)

    related_projections = sorted({
        p for f in fsms
        for p in projections_by_target_id.get(f.entity_id or f.entity, [])
    })
    metadata = {
        "consolidated_from": consolidated_from,
        "consolidated_ids": consolidated_ids,
        "source_node_types": source_node_types,
        "merge_rule": merge_rule,
    }
    if related_projections:
        metadata["projection_links"] = related_projections

    return EntityStateMachine(
        entity=canonical_entity,
        entity_id=canonical_id,
        transitions=merged_transitions,
        states=states,
        fields=fields,
        source_files=files,
        confidence=confidence,
        metadata=metadata,
    )


def _pick_canonical(
    fsms: list[EntityStateMachine],
    class_by_id: dict[str, CodeNode],
    all_class_names: set[str],
) -> EntityStateMachine:
    """Choose the FSM whose name/id should be the canonical display of the merge."""

    def priority(f: EntityStateMachine) -> tuple:
        cls = class_by_id.get(f.entity_id or "")
        is_real_class = cls is not None
        is_unsuffixed = f.entity == _strip_layer_suffix(f.entity)
        # Lower tuple sorts first. Unsuffixed real class > real class > any.
        return (
            0 if (is_real_class and is_unsuffixed) else 1 if is_real_class else 2,
            len(f.entity),
            f.entity,
        )

    return min(fsms, key=priority)


def _rename_transition(
    t: StateTransition, new_entity: str, new_entity_id: str
) -> StateTransition:
    if t.entity == new_entity and (t.entity_id or t.entity) == new_entity_id:
        return t
    return StateTransition(
        entity=new_entity,
        entity_id=new_entity_id,
        field=t.field,
        from_state=t.from_state,
        to_state=t.to_state,
        trigger_function=t.trigger_function,
        confidence=t.confidence,
        guard_expr=t.guard_expr,
        entry_points=t.entry_points,
        metadata=t.metadata,
    )


def _with_projection_links(
    fsm: EntityStateMachine, projected_by: list[str]
) -> EntityStateMachine:
    """Attach projection_links metadata to an otherwise-unmerged FSM."""
    new_metadata = dict(fsm.metadata or {})
    new_metadata["projection_links"] = sorted(set(projected_by))
    return EntityStateMachine(
        entity=fsm.entity,
        entity_id=fsm.entity_id,
        transitions=fsm.transitions,
        states=fsm.states,
        fields=fsm.fields,
        source_files=fsm.source_files,
        confidence=fsm.confidence,
        metadata=new_metadata,
    )


# ---------------------------------------------------------------------------
# Display disambiguation
# ---------------------------------------------------------------------------

def _disambiguate_display_names(
    fsms: list[EntityStateMachine],
) -> list[EntityStateMachine]:
    """When two distinct FSMs share a short `entity`, append module prefixes.

    After consolidation, the canonical `entity` is the display label. If two
    truly distinct entities (e.g. `billing.Order` and `ecommerce.Order`) each
    survive as their own FSM, their display names would collide — downstream
    BPMN / DMN / EARS views would show two unlabeled "Order" rows. Append a
    module qualifier so they render as `Order (billing)` vs `Order (ecommerce)`.
    """
    counts: dict[str, int] = defaultdict(int)
    for f in fsms:
        counts[f.entity] += 1
    if all(c <= 1 for c in counts.values()):
        return fsms

    out: list[EntityStateMachine] = []
    for f in fsms:
        if counts[f.entity] <= 1:
            out.append(f)
            continue
        module_hint = _module_hint(f.entity_id or f.entity, f.source_files)
        if not module_hint:
            out.append(f)
            continue
        new_display = f"{f.entity} ({module_hint})"
        out.append(EntityStateMachine(
            entity=new_display,
            entity_id=f.entity_id,
            transitions=[_rename_transition(t, new_display, t.entity_id or f.entity_id) for t in f.transitions],
            states=f.states,
            fields=f.fields,
            source_files=f.source_files,
            confidence=f.confidence,
            metadata=f.metadata,
        ))
    return out


def _module_hint(entity_id: str, source_files: set[str]) -> str:
    """Extract a compact module label for disambiguation.

    Prefer the second-to-last segment of the qualified entity_id (the package
    above the class name). Fall back to the first source file's top-level
    directory. Returns empty string if nothing usable is found.
    """
    if "::" not in entity_id and "." in entity_id:
        parts = entity_id.split(".")
        if len(parts) >= 2:
            return parts[-2]
    if source_files:
        first = sorted(source_files)[0]
        # Take the top-level directory, e.g. "src/billing/order.py" → "billing".
        segs = [s for s in first.replace("\\", "/").split("/") if s and s != "src"]
        if segs:
            return segs[0]
    return ""


# ---------------------------------------------------------------------------
# Similarity primitives
# ---------------------------------------------------------------------------

def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _stem_similarity(a: str, b: str) -> float:
    """Gestalt ratio — close enough to Jaro-Winkler for short identifiers."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Union-find
# ---------------------------------------------------------------------------

class _UnionFind:
    __slots__ = ("_parent", "_rank")

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            self._parent[rx] = ry
        elif self._rank[rx] > self._rank[ry]:
            self._parent[ry] = rx
        else:
            self._parent[ry] = rx
            self._rank[rx] += 1
