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
    all_nodes: list = field(default_factory=list)                  # parse
    domains_dict: dict = field(default_factory=dict)               # domains
    domains: list = field(default_factory=list)                    # domains
    edges: list = field(default_factory=list)                      # graph
    scenarios: list = field(default_factory=list)                  # slices
    all_transitions: list = field(default_factory=list)            # slices (8a)
    sql_nodes: list = field(default_factory=list)                  # sql
    sql_fsms: list = field(default_factory=list)                   # sql
    relationships: list = field(default_factory=list)              # sql
    fsms: list = field(default_factory=list)                       # entities

    pipeline_start: float = field(default_factory=time.perf_counter)

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
        """The old ``_phase_should_run(n, start_phase, skip_phases)`` gate."""
        from ..pipeline import _phase_should_run

        return _phase_should_run(phase_num, self.start_phase, self.skip_phases)
