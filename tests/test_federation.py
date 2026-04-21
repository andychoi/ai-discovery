"""Tests for the Phase 4 workspace federation over per-repo artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.graph.federation import federate_workspace, write_federation
from ai_discovery.graph.fsm_export import (
    load_cross_entity_links_json,
    load_entity_state_machines_json,
    write_cross_entity_links_json,
    write_entity_conditions_json,
    write_entity_state_machines_json,
)
from ai_discovery.graph.models import (
    CrossEntityTransitionLink,
    EntityConditionCorrelation,
    EntityStateMachine,
    StateTransition,
)


# --- fixtures ---------------------------------------------------------------


def _fsm(
    entity: str, *,
    entity_id: str | None = None,
    states: set[str] | None = None,
    fields: set[str] | None = None,
    transitions: list[StateTransition] | None = None,
    kind: str = "transactional",
    confidence: float = 1.0,
    source_files: set[str] | None = None,
) -> EntityStateMachine:
    return EntityStateMachine(
        entity=entity,
        entity_id=entity_id or f"mod.{entity}",
        states=states or set(),
        fields=fields or {"status"},
        source_files=source_files or {f"src/{entity.lower()}.py"},
        transitions=transitions or [],
        confidence=confidence,
        metadata={"entity_kind": kind},
    )


def _t(entity: str, from_state: str | None, to_state: str, *,
       trigger: str | None = None) -> StateTransition:
    return StateTransition(
        entity=entity, entity_id=f"mod.{entity}", field="status",
        from_state=from_state, to_state=to_state,
        trigger_function=trigger or f"mod.{entity.lower()}_trans",
    )


def _write_repo(
    path: Path, *,
    fsms: list[EntityStateMachine],
    links: list[CrossEntityTransitionLink] | None = None,
    conditions: list[EntityConditionCorrelation] | None = None,
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    write_entity_state_machines_json(fsms, path / "entity_state_machines.json")
    if links:
        write_cross_entity_links_json(links, path / "cross_entity_transitions.json")
    if conditions:
        write_entity_conditions_json(conditions, path / "entity_conditions.json")
    return path


# --- merge behavior ---------------------------------------------------------


def test_single_repo_federates_identically(tmp_path: Path):
    """Federating one repo just copies its FSMs — with a source_repos tag."""
    billing = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status", "total"}),
    ])
    out = federate_workspace([billing])
    assert len(out["fsms"]) == 1
    fsm = out["fsms"][0]
    assert fsm.entity == "Order"
    assert list(fsm.metadata["source_repos"].keys()) == ["billing"]


def test_same_entity_in_two_repos_merges(tmp_path: Path):
    """Matching stem + overlapping fields → one federated FSM."""
    billing = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status", "total"}, states={"draft", "submitted"}),
    ])
    fulfillment = _write_repo(tmp_path / "fulfillment", fsms=[
        _fsm("Order", fields={"id", "status", "total"}, states={"submitted", "shipped"}),
    ])
    out = federate_workspace([billing, fulfillment])
    assert len(out["fsms"]) == 1
    fsm = out["fsms"][0]
    # States from both repos union in
    assert fsm.states == {"draft", "submitted", "shipped"}
    # Both repos are in provenance
    assert set(fsm.metadata["source_repos"].keys()) == {"billing", "fulfillment"}
    assert "federation_rule" in fsm.metadata


def test_same_stem_but_low_field_overlap_stays_separate(tmp_path: Path):
    """Two 'Order' classes that don't share fields aren't the same entity."""
    r1 = _write_repo(tmp_path / "r1", fsms=[
        _fsm("Order", fields={"id", "customer_id", "total"}),
    ])
    r2 = _write_repo(tmp_path / "r2", fsms=[
        _fsm("Order", fields={"order_ref", "vendor_code"}),  # disjoint fields
    ])
    out = federate_workspace([r1, r2])
    assert len(out["fsms"]) == 2


def test_suffix_normalized_matches_merge(tmp_path: Path):
    """`OrderEntity` in one repo merges with `Order` in another (same stem
    after stripping `Entity` suffix)."""
    r1 = _write_repo(tmp_path / "r1", fsms=[
        _fsm("Order", fields={"id", "status"}),
    ])
    r2 = _write_repo(tmp_path / "r2", fsms=[
        _fsm("OrderEntity", entity_id="fulfill.OrderEntity", fields={"id", "status"}),
    ])
    out = federate_workspace([r1, r2])
    assert len(out["fsms"]) == 1


def test_plural_variant_merges(tmp_path: Path):
    """`Orders` collection class merges with `Order` via plural folding."""
    r1 = _write_repo(tmp_path / "r1", fsms=[
        _fsm("Order", fields={"id", "status"}),
    ])
    r2 = _write_repo(tmp_path / "r2", fsms=[
        _fsm("Orders", entity_id="listing.Orders", fields={"id", "status"}),
    ])
    out = federate_workspace([r1, r2])
    assert len(out["fsms"]) == 1


def test_canonical_picked_by_transition_count(tmp_path: Path):
    """Tie-break on merge: richer FSM (more transitions) sets the canonical name."""
    billing = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", entity_id="billing.Order", fields={"id", "status"},
             transitions=[_t("Order", "draft", "submitted")]),
    ])
    # Fulfillment has more transitions — it'll be canonical
    fulfillment = _write_repo(tmp_path / "fulfillment", fsms=[
        _fsm("Order", entity_id="fulfillment.Order", fields={"id", "status"},
             transitions=[
                 _t("Order", "submitted", "shipped"),
                 _t("Order", "shipped", "delivered"),
             ]),
    ])
    out = federate_workspace([billing, fulfillment])
    assert out["fsms"][0].entity_id == "fulfillment.Order"
    # But transitions from both repos are included
    assert len(out["fsms"][0].transitions) == 3


def test_source_repos_records_per_repo_details(tmp_path: Path):
    r1 = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status"}, states={"draft"}),
    ])
    r2 = _write_repo(tmp_path / "fulfillment", fsms=[
        _fsm("Order", fields={"id", "status"}, states={"shipped"}),
    ])
    out = federate_workspace([r1, r2])
    prov = out["fsms"][0].metadata["source_repos"]
    assert prov["billing"]["states"] == ["draft"]
    assert prov["fulfillment"]["states"] == ["shipped"]


def test_source_files_prefixed_with_slug(tmp_path: Path):
    """Federated source_files disambiguate two repos that both have src/order.py."""
    r1 = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status"}, source_files={"src/order.py"}),
    ])
    r2 = _write_repo(tmp_path / "fulfillment", fsms=[
        _fsm("Order", fields={"id", "status"}, source_files={"src/order.py"}),
    ])
    out = federate_workspace([r1, r2])
    files = out["fsms"][0].source_files
    assert "billing::src/order.py" in files
    assert "fulfillment::src/order.py" in files


def test_custom_slugs(tmp_path: Path):
    """Custom repo_slugs override the dir basename."""
    r1 = _write_repo(tmp_path / "a", fsms=[_fsm("Order", fields={"id", "status"})])
    r2 = _write_repo(tmp_path / "b", fsms=[_fsm("Order", fields={"id", "status"})])
    out = federate_workspace([r1, r2], repo_slugs=["billing-svc", "fulfillment-svc"])
    assert set(out["fsms"][0].metadata["source_repos"].keys()) == {"billing-svc", "fulfillment-svc"}


def test_slug_length_mismatch_rejected(tmp_path: Path):
    r1 = _write_repo(tmp_path / "a", fsms=[_fsm("Order")])
    with pytest.raises(ValueError, match="length"):
        federate_workspace([r1], repo_slugs=["billing", "extra"])


# --- cross-entity links + conditions ----------------------------------------


def test_cross_links_union_across_repos(tmp_path: Path):
    link_a = CrossEntityTransitionLink(
        from_entity="Order", from_entity_id="billing.Order", from_field="status",
        from_state="submitted",
        to_entity="Invoice", to_entity_id="billing.Invoice", to_field="status",
        to_state="pending", support=3, directional_confidence=0.9,
    )
    link_b = CrossEntityTransitionLink(
        from_entity="Shipment", from_entity_id="fulfill.Shipment", from_field="status",
        from_state="pending",
        to_entity="Order", to_entity_id="fulfill.Order", to_field="status",
        to_state="shipped", support=2, directional_confidence=0.85,
    )
    r1 = _write_repo(tmp_path / "billing", fsms=[_fsm("Order"), _fsm("Invoice")], links=[link_a])
    r2 = _write_repo(tmp_path / "fulfill", fsms=[_fsm("Order"), _fsm("Shipment")], links=[link_b])
    out = federate_workspace([r1, r2])
    assert len(out["cross_links"]) == 2


def test_duplicate_cross_links_dedupe_to_max_support(tmp_path: Path):
    make_link = lambda support: CrossEntityTransitionLink(
        from_entity="Order", from_entity_id="Order", from_field="status",
        from_state="submitted",
        to_entity="Invoice", to_entity_id="Invoice", to_field="status",
        to_state="pending", support=support, directional_confidence=0.9,
    )
    r1 = _write_repo(tmp_path / "a", fsms=[_fsm("Order", entity_id="Order")],
                     links=[make_link(3)])
    r2 = _write_repo(tmp_path / "b", fsms=[_fsm("Order", entity_id="Order")],
                     links=[make_link(5)])
    out = federate_workspace([r1, r2])
    assert len(out["cross_links"]) == 1
    assert out["cross_links"][0].support == 5


def test_conditions_preserved_across_repos(tmp_path: Path):
    c = EntityConditionCorrelation(
        target_entity_id="mod.Invoice", target_entity="Invoice",
        target_field="status", target_to_state="pending",
        context_entity_id="mod.Order", context_entity="Order",
        context_field="status", context_state="submitted",
        support=4, consistency=1.0,
    )
    r1 = _write_repo(tmp_path / "a", fsms=[_fsm("Order"), _fsm("Invoice")],
                     conditions=[c])
    r2 = _write_repo(tmp_path / "b", fsms=[_fsm("Order"), _fsm("Invoice")])
    out = federate_workspace([r1, r2])
    assert len(out["conditions"]) == 1


# --- I/O --------------------------------------------------------------------


def test_write_federation_round_trip(tmp_path: Path):
    """Write federated artifacts → load them with the standard loaders."""
    r1 = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status"}),
    ])
    r2 = _write_repo(tmp_path / "fulfill", fsms=[
        _fsm("Order", fields={"id", "status"}),
    ])
    fed = federate_workspace([r1, r2])
    paths = write_federation(fed, tmp_path / "federated")
    assert paths["fsms"].exists()
    assert paths["manifest"].exists()
    # The federated FSM file is loadable by the standard loader — that's
    # the whole point of reusing the per-repo schema.
    loaded = load_entity_state_machines_json(paths["fsms"])
    assert len(loaded) == 1
    assert "source_repos" in loaded[0].metadata


def test_missing_fsm_artifact_raises(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        federate_workspace([empty])


def test_output_is_deterministic(tmp_path: Path):
    """Same per-repo inputs → identical federated output regardless of dir order."""
    r1 = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status"}, states={"draft"}),
        _fsm("Invoice", fields={"id", "status"}, states={"pending"}),
    ])
    r2 = _write_repo(tmp_path / "fulfill", fsms=[
        _fsm("Order", fields={"id", "status"}, states={"shipped"}),
        _fsm("Shipment", fields={"id", "status"}, states={"dispatched"}),
    ])
    fed_a = federate_workspace([r1, r2])
    fed_b = federate_workspace([r2, r1])
    # Order shouldn't affect the *set* of merged entities
    entities_a = sorted(f.entity_id or f.entity for f in fed_a["fsms"])
    entities_b = sorted(f.entity_id or f.entity for f in fed_b["fsms"])
    assert entities_a == entities_b


def test_federation_reuses_impact_query(tmp_path: Path):
    """Sanity: federated FSMs feed the existing impact query unchanged —
    federation is schema-compatible with per-repo scans."""
    from ai_discovery.graph.impact import query_entity_impact
    r1 = _write_repo(tmp_path / "billing", fsms=[
        _fsm("Order", fields={"id", "status"},
             transitions=[_t("Order", "draft", "submitted")]),
    ])
    r2 = _write_repo(tmp_path / "fulfill", fsms=[
        _fsm("Order", fields={"id", "status"},
             transitions=[_t("Order", "submitted", "shipped")]),
    ])
    fed = federate_workspace([r1, r2])
    report = query_entity_impact("Order", fed["fsms"],
                                 fed["cross_links"], fed["conditions"])
    assert "Impact report: Order" in report
    # Both transitions appear in the federated view
    assert "draft" in report and "shipped" in report
