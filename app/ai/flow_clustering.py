"""Scenario clustering by user intent and business capability."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..graph.models import Scenario


@dataclass
class ScenarioCluster:
    """Groups scenarios by shared intent/use-case."""
    intent: str  # create, read, update, delete, cancel, approve, etc.
    domain: str  # Order, Payment, etc.
    scenarios: list[str] = field(default_factory=list)  # scenario IDs
    confidence: float = 1.0


# Common intent patterns (by order of specificity)
_INTENT_PATTERNS: dict[str, list[str]] = {
    "create": [r"create", r"new", r"post", r"add", r"insert"],
    "read": [r"get", r"fetch", r"list", r"search", r"query", r"view"],
    "update": [r"update", r"edit", r"patch", r"modify", r"change"],
    "delete": [r"delete", r"remove", r"drop", r"cancel", r"archive"],
    "approve": [r"approve", r"accept", r"validate", r"confirm", r"authorize"],
    "reject": [r"reject", r"deny", r"decline", r"refuse"],
    "process": [r"process", r"execute", r"run", r"handle", r"trigger"],
    "publish": [r"publish", r"emit", r"send", r"dispatch", r"broadcast"],
    "subscribe": [r"subscribe", r"consume", r"listen", r"handle", r"receive"],
}


def infer_intent(scenario: Scenario) -> str:
    """Infer user intent from scenario name and entry point.

    Returns one of: create, read, update, delete, approve, reject, process, publish, subscribe, other
    """
    combined_text = f"{scenario.name} {scenario.entry_point}".lower()

    # Check against patterns in specificity order
    for intent, patterns in _INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, combined_text):
                return intent

    return "other"


def cluster_scenarios_by_intent(scenarios: list[Scenario]) -> list[ScenarioCluster]:
    """Group scenarios by (intent, domain) pairs.

    Returns:
        List of ScenarioCluster, one per unique (intent, domain) pair
    """
    clusters_map: dict[tuple[str, str], list[str]] = defaultdict(list)

    for scenario in scenarios:
        intent = infer_intent(scenario)
        domain = scenario.domain or "Unknown"
        key = (intent, domain)
        clusters_map[key].append(scenario.scenario_id)

    result: list[ScenarioCluster] = []
    for (intent, domain), scenario_ids in sorted(clusters_map.items()):
        result.append(ScenarioCluster(
            intent=intent,
            domain=domain,
            scenarios=scenario_ids,
            confidence=1.0,  # All clusters are equally confident for now
        ))

    return result


def get_cluster_summary(clusters: list[ScenarioCluster]) -> dict[str, int]:
    """Summarize clustering by intent counts.

    Returns:
        { "create": 3, "read": 2, "update": 1, ... }
    """
    counts: dict[str, int] = defaultdict(int)
    for cluster in clusters:
        counts[cluster.intent] += len(cluster.scenarios)
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))
