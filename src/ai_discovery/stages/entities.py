"""Stage ``entities``: 8b backbone — per-entity FSMs, consolidation, denormalization,
entity kinds, cross-entity links, guards, conditions, backbone Mermaid, DMN and EARS.
Moved verbatim from the unnumbered block after phase 8 (M1.6). No checkpoint (as before)."""

from __future__ import annotations

from .context import RunContext


def run(ctx: RunContext) -> None:
    from ..graph.entity_classifier import classify_entities
    from ..graph.entity_correlator import (
        detect_denormalization_links,
        mine_cross_entity_transitions,
        mine_entity_conditions,
    )
    from ..graph.fsm_export import (
        write_cross_entity_links_json,
        write_entity_conditions_json,
        write_entity_state_machines_json,
    )
    from ..graph.fsm_identity import consolidate_entities
    from ..graph.fsm_persistence import persist_entity_state_machines
    from ..graph.fsm_rollup import build_entity_state_machines
    from ..graph.guard_parser import parse_cross_entity_guards
    from ..pipeline import _timed, console

    all_nodes, scenarios, scan_id = ctx.all_nodes, ctx.scenarios, ctx.scan_id
    db_path, output_dir = ctx.db_path, ctx.output_dir
    nodes_for_consolidation = all_nodes + ctx.sql_nodes

    # 8b (inline, Phase 2a–2d): roll transitions up into per-entity FSMs,
    # consolidate name-variants (Order/OrderEntity/order_reducer → one FSM),
    # persist to SQLite, export JSON. This is the canonical backbone artifact
    # — every downstream entity view (lifecycle, DMN, EARS) is supposed to consume the FSM,
    # not re-scan scenarios.
    node_file_index = {n.qualified_name: n.file_path for n in all_nodes}
    with _timed("fsm rollup"), console.status("[bold cyan]Aggregating state transitions into entity FSMs..."):
        raw_fsms = build_entity_state_machines(ctx.all_transitions, node_file_index=node_file_index)
    raw_fsms = raw_fsms + ctx.sql_fsms
    with _timed("fsm consolidate"), console.status("[bold cyan]Consolidating entity name-variants..."):
        fsms, projection_links = consolidate_entities(raw_fsms, nodes_for_consolidation)
    ctx.fsms = fsms
    # Phase 3a: detect denormalized field copies across entities so the
    # classifier's junction rule can see through them.
    with _timed("denorm detect"), console.status("[bold cyan]Detecting denormalized fields..."):
        detect_denormalization_links(fsms)
    denorm_count = sum(1 for f in fsms if f.metadata.get("denormalized_fields"))
    if denorm_count:
        console.print(f"  Denormalized fields: [green]{denorm_count}[/] entities annotated")
    # Phase 2e-2: annotate each FSM with entity_kind so downstream generators
    # can branch on role (transactional vs. master vs. key vs. summary / …).
    with _timed("fsm classify"), console.status("[bold cyan]Classifying entity kinds..."):
        classify_entities(fsms)
    if fsms:
        fsm_json_path = output_dir / "entity_state_machines.json"
        write_entity_state_machines_json(fsms, fsm_json_path)
        if scan_id is not None:
            persist_entity_state_machines(db_path, scan_id, fsms)
        total_transitions = sum(len(f.transitions) for f in fsms)
        merged_count = len(raw_fsms) - len(fsms)
        merge_suffix = f", [yellow]{merged_count}[/] merged" if merged_count else ""
        proj_suffix = f", [dim]{len(projection_links)}[/] projection links" if projection_links else ""
        console.print(
            f"  Entity FSMs: [green]{len(fsms)}[/] entities"
            f"{merge_suffix}{proj_suffix}, "
            f"[green]{total_transitions}[/] deduped transitions → {fsm_json_path.name}"
        )
        kind_counts: dict[str, int] = {}
        for f in fsms:
            k = f.metadata.get("entity_kind", "unknown")
            kind_counts[k] = kind_counts.get(k, 0) + 1
        if kind_counts:
            parts = ", ".join(f"[green]{n}[/] {k}" for k, n in sorted(kind_counts.items()))
            console.print(f"  Entity kinds: {parts}")
        # Phase 3b: cross-entity transition sequence mining. Drives cross-entity
        # cross-lane sequence flows and impact analysis.
        with _timed("cross-entity mining"), console.status("[bold cyan]Mining cross-entity transition sequences..."):
            # min_support=2 (MED): a single co-occurrence is coincidence, not a
            # causal trigger; require the A→B sequence in at least 2 scenarios.
            cross_links = mine_cross_entity_transitions(fsms, scenarios, min_support=2)
        if cross_links:
            cross_json_path = output_dir / "cross_entity_transitions.json"
            write_cross_entity_links_json(cross_links, cross_json_path)
            console.print(
                f"  Cross-entity links: [green]{len(cross_links)}[/] sequences → {cross_json_path.name}"
            )
        # Phase 3c: parse guard expressions for cross-entity state refs.
        # Annotates transition metadata in place; downstream DMN/EARS
        # generators can render these as multi-entity rule inputs.
        with _timed("guard parse"), console.status("[bold cyan]Parsing cross-entity guards..."):
            guarded = parse_cross_entity_guards(fsms)
        if guarded:
            console.print(f"  Cross-entity guards: [green]{guarded}[/] transitions annotated")
        # Phase 3d: mine entity-condition correlations. When a transition on
        # entity Y consistently fires while entity X is in a specific state,
        # that's a DMN rule input ("WHEN Order=submitted, Invoice → pending").
        # Unlike guards (syntactic, from source text), correlations are
        # statistical (from scenario walks) — complementary signals.
        with _timed("condition mining"), console.status("[bold cyan]Mining entity condition correlations..."):
            conditions = mine_entity_conditions(fsms, scenarios)
        if conditions:
            conditions_path = output_dir / "entity_conditions.json"
            write_entity_conditions_json(conditions, conditions_path)
            console.print(
                f"  Entity conditions: [green]{len(conditions)}[/] correlations → {conditions_path.name}"
            )
        # The entity backbone, DMN decision tables and EARS requirements are sections of the entity pages
        # (`pages/builders/business.py`); the old BPMN/DMN/EARS markdown files and their "legacy mirror" copies
        # at the scan root were removed with the SDLC tree (M3.7.b).


execute = run
