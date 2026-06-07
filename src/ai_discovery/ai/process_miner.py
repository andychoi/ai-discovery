"""Structural flow statistics over pseudo event logs (pure Python).

Computes edge (transition) frequencies across scenario event sequences.
This replaced the former pm4py-based process miner (removed 2026-06-06):
the inputs here are statically inferred, single-trace event sequences —
not real execution logs — so model discovery added no information and the
conformance/bottleneck metrics it reported were fabricated (hardcoded
fitness, synthetic 1-second timestamps). See
docs/architecture/decisions.md "Pseudo Event Log Generation" for the
reversal record and the conditions for reintroducing real process mining
(runtime event-log ingestion).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EdgeFrequency:
    """Information about edge (transition) frequency."""
    from_activity: str
    to_activity: str
    count: int
    percentage: float


@dataclass
class MiningResult:
    """Flow statistics for one scenario's pseudo event log."""
    scenario_id: str
    scenario_name: str
    domain: str | None

    edge_frequencies: list[EdgeFrequency] = field(default_factory=list)
    event_count: int = 0

    # Diagnostics
    warnings: list[str] = field(default_factory=list)

    # Timestamps
    mined_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class ProcessMiner:
    """Computes structural flow statistics from pseudo event logs."""

    def __init__(self, scenario_id: str, scenario_name: str, domain: str | None = None):
        self.scenario_id = scenario_id
        self.scenario_name = scenario_name
        self.domain = domain
        self.result = MiningResult(
            scenario_id=scenario_id,
            scenario_name=scenario_name,
            domain=domain,
        )

    def mine_from_pseudo_log(self, pseudo_log: dict) -> MiningResult:
        """Compute flow statistics for a single pseudo event log.

        Args:
            pseudo_log: { "case_id", "events": [{"order", "event_name", "event_type"}] }
                (from ScenarioFlow.generate_pseudo_event_log())

        Returns:
            MiningResult with edge frequencies
        """
        logger.info(f"Computing flow statistics for {self.scenario_id}")

        try:
            events = pseudo_log.get("events", [])
            self.result.event_count = len(events)
            self.result.edge_frequencies = _calculate_edge_frequencies(events)

            logger.debug(
                f"Flow statistics for {self.scenario_id}: "
                f"{self.result.event_count} events, "
                f"{len(self.result.edge_frequencies)} edges"
            )

        except Exception as e:
            logger.error(f"Flow statistics failed for {self.scenario_id}: {e}", exc_info=True)
            self.result.warnings.append(f"Flow statistics error: {str(e)}")

        return self.result

    def get_summary(self) -> dict[str, Any]:
        """Get human-readable summary of flow statistics.

        Returns:
            Dict suitable for markdown rendering
        """
        top_edges = self.result.edge_frequencies[:5]
        edge_md = "\n".join(
            f"- {e.from_activity} → {e.to_activity}: {e.percentage:.1f}% ({e.count})"
            for e in top_edges
        )

        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "status": "SUCCESS" if not self.result.warnings else "WARNINGS",
            "event_count": self.result.event_count,
            "edge_frequencies": edge_md or "(none)",
            "warnings": self.result.warnings,
            "mined_at": self.result.mined_at,
        }


def _calculate_edge_frequencies(events: list[dict]) -> list[EdgeFrequency]:
    """Calculate frequency of each edge (transition) in an event sequence.

    Args:
        events: Ordered event dicts with an "event_name" key

    Returns:
        List of EdgeFrequency, sorted by frequency descending
    """
    edge_counts: dict[tuple[str, str], int] = {}
    total_edges = 0

    for i in range(len(events) - 1):
        from_activity = events[i].get("event_name", f"Step_{i}")
        to_activity = events[i + 1].get("event_name", f"Step_{i + 1}")

        key = (from_activity, to_activity)
        edge_counts[key] = edge_counts.get(key, 0) + 1
        total_edges += 1

    frequencies = []
    for (from_act, to_act), count in edge_counts.items():
        percentage = (count / total_edges * 100) if total_edges > 0 else 0.0
        frequencies.append(EdgeFrequency(
            from_activity=from_act,
            to_activity=to_act,
            count=count,
            percentage=percentage,
        ))

    # Sort by frequency descending
    frequencies.sort(key=lambda e: e.count, reverse=True)

    return frequencies


def mine_scenarios(pseudo_logs: list[dict]) -> dict[str, MiningResult]:
    """Compute flow statistics for all scenarios.

    Args:
        pseudo_logs: List of pseudo event log dicts (from ScenarioFlowInference)

    Returns:
        Dict[scenario_id] → MiningResult
    """
    results = {}

    for pseudo_log in pseudo_logs:
        scenario_id = pseudo_log.get("case_id", "unknown")
        scenario_name = pseudo_log.get("process_name", scenario_id)
        domain = pseudo_log.get("domain")

        miner = ProcessMiner(scenario_id, scenario_name, domain)
        result = miner.mine_from_pseudo_log(pseudo_log)
        results[scenario_id] = result

    return results
