"""Phase 4 deliverable: workspace federation over per-repo artifacts.

Reads the canonical backbone artifacts from multiple scan output dirs
(`entity_state_machines.json`, `cross_entity_transitions.json`,
`entity_conditions.json`) and merges them into a single federated view.

Unlike the intra-repo consolidator (`fsm_identity.consolidate_entities`),
this operates on JSON-loaded FSMs without access to the source code graph
— so the merge signal is narrower: normalized-stem name equality plus
field-set Jaccard overlap above a threshold. Two services in different
repos cannot share a class (by definition), so the superclass / mixin
reasoning that fsm_identity needs intra-repo is unnecessary here.

The federated FSMs carry `metadata.source_repos: {repo_slug: {...}}`
so downstream generators can answer "which repo drives `Order → submitted`?"
— the Phase 4 exit deliverable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fsm_export import (
    entity_state_machines_to_json,
    cross_entity_links_to_json,
    entity_conditions_to_json,
    load_cross_entity_links_json,
    load_entity_conditions_json,
    load_entity_state_machines_json,
)
from .models import (
    CrossEntityTransitionLink,
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


# Cross-repo merge threshold. Higher than intra-repo's 0.93 because we have
# less evidence (no superclass info, no CodeNode hints) — demand a stronger
# field overlap before calling two FSMs the same entity.
_FIELD_JACCARD_THRESHOLD = 0.7


def federate_workspace(
    artifact_dirs: list[Path],
    *,
    repo_slugs: list[str] | None = None,
    jaccard_threshold: float = _FIELD_JACCARD_THRESHOLD,
) -> dict[str, Any]:
    """Merge canonical artifacts from multiple per-repo scans.

    Parameters
    ----------
    artifact_dirs: Each directory must contain `entity_state_machines.json`.
        `cross_entity_transitions.json` and `entity_conditions.json` are
        optional; missing files contribute no links/conditions.
    repo_slugs: Display names per directory. Defaults to the directory's
        basename. Used in the `source_repos` provenance on merged FSMs.

    Returns
    -------
    dict with keys `fsms`, `cross_links`, `conditions`, `source_repos` —
    ready for the existing `write_*_json` functions.
    """
    if repo_slugs is None:
        repo_slugs = [d.name for d in artifact_dirs]
    if len(repo_slugs) != len(artifact_dirs):
        raise ValueError("repo_slugs length must match artifact_dirs length")

    per_repo: list[tuple[str, list[EntityStateMachine], list[CrossEntityTransitionLink], list[EntityConditionCorrelation]]] = []
    for slug, d in zip(repo_slugs, artifact_dirs):
        fsm_path = d / "entity_state_machines.json"
        if not fsm_path.exists():
            raise FileNotFoundError(f"{slug}: no entity_state_machines.json in {d}")
        fsms = load_entity_state_machines_json(fsm_path)
        cross_path = d / "cross_entity_transitions.json"
        links = load_cross_entity_links_json(cross_path) if cross_path.exists() else []
        cond_path = d / "entity_conditions.json"
        conds = load_entity_conditions_json(cond_path) if cond_path.exists() else []
        per_repo.append((slug, fsms, links, conds))

    merged_fsms = _merge_fsms_across_repos(per_repo, jaccard_threshold)
    merged_links = _merge_cross_links_across_repos(per_repo)
    merged_conditions = _merge_conditions_across_repos(per_repo)

    return {
        "fsms": merged_fsms,
        "cross_links": merged_links,
        "conditions": merged_conditions,
        "source_repos": repo_slugs,
    }


def write_federation(
    federation: dict[str, Any], output_dir: Path,
) -> dict[str, Path]:
    """Write federated artifacts using the same schema as per-repo scans."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    fsm_path = output_dir / "entity_state_machines.json"
    fsm_path.write_text(entity_state_machines_to_json(federation["fsms"]))
    paths["fsms"] = fsm_path

    if federation["cross_links"]:
        cross_path = output_dir / "cross_entity_transitions.json"
        cross_path.write_text(cross_entity_links_to_json(federation["cross_links"]))
        paths["cross_links"] = cross_path

    if federation["conditions"]:
        cond_path = output_dir / "entity_conditions.json"
        cond_path.write_text(entity_conditions_to_json(federation["conditions"]))
        paths["conditions"] = cond_path

    # A federation manifest helps anyone inspecting the artifact dir know
    # which repos contributed, without parsing the FSM metadata.
    manifest = output_dir / "federation.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "source_repos": sorted(federation["source_repos"]),
    }, indent=2, sort_keys=True) + "\n")
    paths["manifest"] = manifest

    return paths


# ---------------------------------------------------------------------------
# FSM merge — name-stem equivalence + field-Jaccard overlap.
# ---------------------------------------------------------------------------

def _merge_fsms_across_repos(
    per_repo: list[tuple[str, list[EntityStateMachine], list[CrossEntityTransitionLink], list[EntityConditionCorrelation]]],
    jaccard_threshold: float,
) -> list[EntityStateMachine]:
    """Union-find style merge across repos, keyed on normalized stem."""
    # Group candidates by normalized stem first — two FSMs must share a
    # recognizable name before we bother computing Jaccard.
    candidates: dict[str, list[tuple[str, EntityStateMachine]]] = {}
    for slug, fsms, _, _ in per_repo:
        for f in fsms:
            stem = _normalize_stem(f.entity)
            candidates.setdefault(stem, []).append((slug, f))

    merged: list[EntityStateMachine] = []
    for stem, pairs in sorted(candidates.items()):
        if len(pairs) == 1:
            slug, f = pairs[0]
            merged.append(_tag_source_repo(f, slug, is_solo=True))
            continue
        # Build merge groups: iterate pairs, join any whose fields overlap
        # above threshold with an existing group's aggregate field set.
        groups: list[list[tuple[str, EntityStateMachine]]] = []
        for slug, f in pairs:
            placed = False
            for g in groups:
                agg_fields: set[str] = set()
                for _, gf in g:
                    agg_fields |= gf.fields
                if _jaccard(f.fields, agg_fields) >= jaccard_threshold:
                    g.append((slug, f))
                    placed = True
                    break
            if not placed:
                groups.append([(slug, f)])
        for g in groups:
            if len(g) == 1:
                slug, f = g[0]
                merged.append(_tag_source_repo(f, slug, is_solo=True))
            else:
                merged.append(_merge_group(g))

    merged.sort(key=lambda f: f.entity_id or f.entity)
    return merged


def _merge_group(group: list[tuple[str, EntityStateMachine]]) -> EntityStateMachine:
    """Merge 2+ FSMs from different repos into one federated FSM."""
    # Pick canonical: the FSM with the most transitions, breaking ties by
    # shorter entity_id (usually the more precise / authoritative one).
    canonical_slug, canonical = max(
        group, key=lambda sf: (len(sf[1].transitions), -len(sf[1].entity_id or "")),
    )
    out = EntityStateMachine(
        entity=canonical.entity,
        entity_id=canonical.entity_id,
        transitions=list(canonical.transitions),
        states=set(canonical.states),
        fields=set(canonical.fields),
        source_files={_prefixed(canonical_slug, p) for p in canonical.source_files},
        confidence=canonical.confidence,
        metadata=dict(canonical.metadata),
    )

    # Per-repo provenance: what each repo contributed in its own terms.
    source_repos: dict[str, dict[str, Any]] = {
        canonical_slug: {
            "entity_id": canonical.entity_id,
            "entity": canonical.entity,
            "states": sorted(canonical.states),
            "fields": sorted(canonical.fields),
            "transition_count": len(canonical.transitions),
            "entity_kind": canonical.metadata.get("entity_kind"),
        }
    }

    for slug, f in group:
        if slug == canonical_slug and f is canonical:
            continue
        out.states |= f.states
        out.fields |= f.fields
        out.source_files |= {_prefixed(slug, p) for p in f.source_files}
        for t in f.transitions:
            out.transitions.append(t)
        # Track per-repo. If the same slug appears twice (shouldn't normally
        # happen — one slug per artifact_dir), preserve the first entry.
        if slug not in source_repos:
            source_repos[slug] = {
                "entity_id": f.entity_id,
                "entity": f.entity,
                "states": sorted(f.states),
                "fields": sorted(f.fields),
                "transition_count": len(f.transitions),
                "entity_kind": f.metadata.get("entity_kind"),
            }

    out.metadata["source_repos"] = source_repos
    out.metadata["federation_rule"] = f"name-stem+jaccard>={_FIELD_JACCARD_THRESHOLD}"
    # Confidence of a federated FSM is the min across contributors — the
    # weakest link determines how confidently we can say "same entity."
    out.confidence = round(min(sf[1].confidence for sf in group), 4)
    return out


def _tag_source_repo(f: EntityStateMachine, slug: str, *, is_solo: bool) -> EntityStateMachine:
    """Tag a single-repo FSM with its source slug so federated reports can
    still attribute transitions."""
    tagged = EntityStateMachine(
        entity=f.entity,
        entity_id=f.entity_id,
        transitions=list(f.transitions),
        states=set(f.states),
        fields=set(f.fields),
        source_files={_prefixed(slug, p) for p in f.source_files},
        confidence=f.confidence,
        metadata=dict(f.metadata),
    )
    tagged.metadata["source_repos"] = {
        slug: {
            "entity_id": f.entity_id,
            "entity": f.entity,
            "states": sorted(f.states),
            "fields": sorted(f.fields),
            "transition_count": len(f.transitions),
            "entity_kind": f.metadata.get("entity_kind"),
        }
    }
    return tagged


# ---------------------------------------------------------------------------
# Cross-entity links + conditions — merge by concatenation, then dedup on
# the tuple key. Inter-repo conflicts don't happen today (scans are
# per-repo), but if they did, we'd want to keep the highest-support
# observation rather than double-count.
# ---------------------------------------------------------------------------

def _merge_cross_links_across_repos(
    per_repo: list[tuple[str, list[EntityStateMachine], list[CrossEntityTransitionLink], list[EntityConditionCorrelation]]],
) -> list[CrossEntityTransitionLink]:
    by_key: dict[tuple, CrossEntityTransitionLink] = {}
    for _, _, links, _ in per_repo:
        for l in links:
            key = (
                l.from_entity_id or l.from_entity, l.from_field, l.from_state,
                l.to_entity_id or l.to_entity, l.to_field, l.to_state,
            )
            existing = by_key.get(key)
            if existing is None or l.support > existing.support:
                by_key[key] = l
    return sorted(by_key.values(), key=lambda l: (
        l.from_entity, l.from_field, l.from_state or "",
        l.to_entity, l.to_field, l.to_state or "",
    ))


def _merge_conditions_across_repos(
    per_repo: list[tuple[str, list[EntityStateMachine], list[CrossEntityTransitionLink], list[EntityConditionCorrelation]]],
) -> list[EntityConditionCorrelation]:
    by_key: dict[tuple, EntityConditionCorrelation] = {}
    for _, _, _, conds in per_repo:
        for c in conds:
            key = (
                c.target_entity_id or c.target_entity, c.target_field, c.target_to_state,
                c.context_entity_id or c.context_entity, c.context_field, c.context_state,
            )
            existing = by_key.get(key)
            if existing is None or c.support > existing.support:
                by_key[key] = c
    return sorted(by_key.values(), key=lambda c: (
        c.target_entity, c.target_field, c.target_to_state or "",
        c.context_entity, c.context_field, c.context_state,
    ))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ENTITY_SUFFIX_STRIPS = ("Entity", "Model", "Record", "Dto", "DTO")


def _normalize_stem(name: str) -> str:
    """Lowercase + strip common suffixes + strip trailing 's' plural."""
    s = name
    for suffix in _ENTITY_SUFFIX_STRIPS:
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    s = s.lower()
    if s.endswith("s") and not s.endswith("ss") and len(s) > 1:
        s = s[:-1]
    return s


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _prefixed(slug: str, path: str) -> str:
    """Namespace source files by repo slug so federated reports aren't
    ambiguous between e.g. billing-svc/src/order.py and fulfillment-svc/src/order.py."""
    return f"{slug}::{path}"
