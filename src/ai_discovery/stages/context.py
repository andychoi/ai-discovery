"""``RunContext``: what the pipeline phases used to pass between each other as locals."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class RunContext:
    # ── inputs ─────────────────────────────────────────────────────────────
    repo: str
    branch: str
    config: Any                      # DiscoveryConfig
    layout: Any                      # core.paths.ProjectLayout
    resume: bool = False
    resume_from: Optional[str] = None
    rescan: bool = False
    skip_phases: list[str] = field(default_factory=list)

    # ── set by `resolve` ───────────────────────────────────────────────────
    resolved: Any = None             # repo.resolver.ResolvedRepo
    scan_id: Optional[int] = None
    start_phase: Optional[float] = None
    llm_client: Any = None

    # ── stage outputs ──────────────────────────────────────────────────────
    lang_stats: dict = field(default_factory=dict)                 # walk
    walker: Any = None                                             # walk (M6.1 RepoWalker, shared with parse)
    all_nodes: list = field(default_factory=list)                  # parse
    domains_dict: dict = field(default_factory=dict)               # domains
    domains: list = field(default_factory=list)                    # domains
    edges: list = field(default_factory=list)                      # graph
    unresolved: list = field(default_factory=list)                 # graph (ADR-0004 unresolved calls)
    scenarios: list = field(default_factory=list)                  # slices
    all_transitions: list = field(default_factory=list)            # slices (8a)
    sql_nodes: list = field(default_factory=list)                  # sql
    sql_fsms: list = field(default_factory=list)                   # sql
    relationships: list = field(default_factory=list)              # sql
    fsms: list = field(default_factory=list)                       # entities
    chunks: list = field(default_factory=list)                     # embed
    rag_chunks: list = field(default_factory=list)                 # embed
    summaries_dict: dict = field(default_factory=dict)             # summaries
    flows_by_domain: dict = field(default_factory=dict)            # flows
    scenario_flows: list = field(default_factory=list)             # flows
    rollups: list = field(default_factory=list)                    # rollup
    scenario_artifacts: dict = field(default_factory=dict)         # visuals
    mining_results: dict = field(default_factory=dict)             # visuals
    final_status: Optional[str] = None                             # finalise

    pipeline_start: float = field(default_factory=time.perf_counter)

    # ── StageRunner controls (M1.8) ────────────────────────────────────────
    only: Optional[set[str]] = None                                # --only stage names
    forced_phases: set[float] = field(default_factory=set)        # fingerprint changed / --only
    suppressed_phases: set[float] = field(default_factory=set)    # reuse stored output

    # ── early exit (a stage decides the run is over) ───────────────────────
    halted: bool = False
    result: Optional[str] = None

    def halt(self, result: Optional[str] = None) -> None:
        self.halted, self.result = True, result

    # layout shortcuts, named as the old pipeline locals
    @property
    def project_slug(self) -> str:
        return self.layout.slug

    @property
    def output_dir(self) -> Path:
        return self.layout.state_dir

    @property
    def docs_dir(self) -> Path:
        return self.layout.docs_dir

    @property
    def db_path(self) -> Path:
        return self.layout.db_path

    def phase_runs(self, phase_num: float) -> bool:
        """Run (True) or reload (False) a gated phase.

        The StageRunner's decision wins (``suppressed_phases``: stored output
        is reused; ``forced_phases``: inputs changed or ``--only`` named it);
        otherwise the old ``_phase_should_run(n, start_phase, skip_phases)`` gate.
        """
        if phase_num in self.suppressed_phases:
            return False
        if phase_num in self.forced_phases:
            return True
        from ..pipeline import _phase_should_run

        return _phase_should_run(phase_num, self.start_phase, self.skip_phases)

    def degrade(self, stage: str, kind: str, count: int, sample: Optional[list] = None) -> None:
        """Record a degradation for this scan (``degradations`` table; end-panel summary)."""
        if self.scan_id is None or count <= 0:
            return
        from ..pipeline import _record_degradation

        _record_degradation(self.db_path, self.scan_id, stage, kind, count, sample)
