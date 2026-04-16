"""Tier 2 flow analyzer — uses mid-tier LLM (Sonnet) to identify business flows per domain."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..ai.llm_client import LLMClient
from ..db import get_conn, now_iso
from ..graph.models import Domain, CallEdge

logger = logging.getLogger(__name__)

VALID_FLOW_TYPES = {"user_flow", "batch_flow", "integration_flow", "cross_cutting"}


@dataclass
class BusinessFlow:
    name: str
    flow_type: str  # user_flow, batch_flow, integration_flow, cross_cutting
    description: str
    involved_nodes: list[str] = field(default_factory=list)  # qualified_names


@dataclass
class ScenarioFlow:
    scenario_id: str
    steps: list[dict] = field(default_factory=list)  # list of {step: int, name: str, type: str, description: str}
    input: list[str] = field(default_factory=list)
    process: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    data_flow: list[str] = field(default_factory=list)
    external_interfaces: list[dict] = field(default_factory=list)
    confidence: float = 1.0


class ScenarioFlowInference:
    """Uses GenAI to reconstruct scenario flows from execution slices."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def infer_flow(self, scenario: Scenario, summaries: dict[str, dict]) -> ScenarioFlow:
        """Staged inference: Flow -> IPO -> Interfaces."""
        # Step 1: Flow Inference
        flow_steps = self._infer_steps(scenario, summaries)
        
        # Step 2: IPO Extraction
        ipo_data = self._infer_ipo(scenario, flow_steps)
        
        # Step 3: Interface Detection
        interfaces = self._infer_interfaces(scenario, flow_steps)

        return ScenarioFlow(
            scenario_id=scenario.scenario_id,
            steps=flow_steps,
            input=ipo_data.get("input", []),
            process=ipo_data.get("process", []),
            output=ipo_data.get("output", []),
            data_flow=ipo_data.get("data_flow", []),
            external_interfaces=interfaces
        )

    def _infer_steps(self, scenario: Scenario, summaries: dict[str, dict]) -> list[dict]:
        prompt = self._build_steps_prompt(scenario, summaries)
        response = self.llm_client.invoke("tier2", prompt)
        return self._parse_json_response(response.text, "flow")

    def _infer_ipo(self, scenario: Scenario, flow_steps: list[dict]) -> dict:
        prompt = self._build_ipo_prompt(scenario, flow_steps)
        response = self.llm_client.invoke("tier2", prompt)
        return self._parse_json_response(response.text)

    def _infer_interfaces(self, scenario: Scenario, flow_steps: list[dict]) -> list[dict]:
        prompt = self._build_interfaces_prompt(scenario, flow_steps)
        response = self.llm_client.invoke("tier2", prompt)
        return self._parse_json_response(response.text, "interfaces")

    def _build_steps_prompt(self, scenario: Scenario, summaries: dict[str, dict]) -> str:
        nodes_info = []
        for node in scenario.nodes:
            s = summaries.get(node.qualified_name, {})
            nodes_info.append(f"- {node.qualified_name} ({node.type}): {s.get('purpose', 'N/A')}")
            if node.state_transition:
                t = node.state_transition
                nodes_info.append(f"  Transition: {t.entity}.{t.field} -> {t.to_state}")

        return (
            "You are a software architect. Convert the following execution path into a coherent business process flow.\n"
            f"Scenario: {scenario.name} (Trigger: {scenario.trigger_type})\n\n"
            "Execution Path Nodes:\n" + "\n".join(nodes_info) + "\n\n"
            "TASK:\n"
            "1. Group functions into business steps.\n"
            "2. Order steps logically.\n"
            "3. Name steps in business terms.\n"
            "4. Identify validations, transformations, persistence, and external interactions.\n\n"
            "OUTPUT JSON:\n"
            '{"flow": [{"step": 1, "name": "Validate Order", "type": "PROCESS", "description": "..."}]}'
        )

    def _build_ipo_prompt(self, scenario: Scenario, flow_steps: list[dict]) -> str:
        return (
            "Extract Input-Process-Output (IPO) for this scenario flow.\n"
            f"Flow Steps: {json.dumps(flow_steps)}\n\n"
            "OUTPUT JSON:\n"
            '{"input": ["..."], "process": ["..."], "output": ["..."], "data_flow": ["A -> B"]}'
        )

    def _build_interfaces_prompt(self, scenario: Scenario, flow_steps: list[dict]) -> str:
        return (
            "Identify external systems involved in this scenario.\n"
            f"Flow Steps: {json.dumps(flow_steps)}\n\n"
            "OUTPUT JSON:\n"
            '{"interfaces": [{"name": "PostgreSQL", "type": "DB", "operation": "INSERT orders"}]}'
        )

    def _parse_json_response(self, text: str, key: str | None = None) -> any:
        try:
            cleaned = text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            
            data = json.loads(cleaned)
            if key:
                if isinstance(data, dict):
                    return data.get(key, [])
                return []
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to parse LLM JSON: {e}")
            return [] if key else {}


def _build_flow_prompt(domain: Domain, summaries: dict[str, dict]) -> str:
    """Build Tier 2 flow analysis prompt.

    Include:
    - Domain name
    - List of entry points (endpoints, batch jobs) with their Tier 1 summaries
    - Call graph edges (caller -> callee)
    - DB models in the domain
    - Ask LLM to return JSON array of flows, each with:
      name, flow_type, description, involved_nodes (qualified_names)

    flow_type must be one of: user_flow, batch_flow, integration_flow, cross_cutting
    """
    lines: list[str] = []
    lines.append(f"# Domain: {domain.name}")
    lines.append("")

    # Entry points with summaries
    lines.append("## Entry Points")
    for ep in domain.entry_points:
        lines.append(f"- {ep.qualified_name} (type={ep.node_type})")
        if ep.qualified_name in summaries:
            s = summaries[ep.qualified_name]
            if "purpose" in s:
                lines.append(f"  Purpose: {s['purpose']}")
            if "business_rules" in s:
                lines.append(f"  Business rules: {s['business_rules']}")
    lines.append("")

    # All nodes with summaries
    lines.append("## Nodes")
    for node in domain.nodes:
        summary_info = ""
        if node.qualified_name in summaries:
            s = summaries[node.qualified_name]
            purpose = s.get("purpose", "")
            if purpose:
                summary_info = f" — {purpose}"
        lines.append(f"- {node.qualified_name} ({node.node_type}){summary_info}")
    lines.append("")

    # Call graph edges
    lines.append("## Call Graph Edges")
    all_edges = domain.internal_edges + domain.external_edges
    for edge in all_edges:
        lines.append(f"- {edge.caller} -> {edge.callee} ({edge.edge_type})")
    lines.append("")

    # DB models
    lines.append("## DB Models")
    for model in domain.db_models:
        lines.append(f"- {model.qualified_name}")
    lines.append("")

    # Instructions
    lines.append("## Task")
    lines.append(
        "Analyze the above domain and identify all business flows. "
        "Return a JSON array where each element has:\n"
        '- "name": short descriptive name for the flow\n'
        '- "flow_type": one of "user_flow", "batch_flow", "integration_flow", "cross_cutting"\n'
        '- "description": 1-2 sentence description of what the flow does\n'
        '- "involved_nodes": list of qualified_name strings that participate in this flow\n'
        "\n"
        "Return ONLY the JSON array, no markdown fences or extra text."
    )

    return "\n".join(lines)


def _parse_flows(text: str) -> list[BusinessFlow]:
    """Parse LLM response into BusinessFlow objects.
    Gracefully handle non-JSON by returning empty list."""
    try:
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence
            first_newline = cleaned.index("\n")
            cleaned = cleaned[first_newline + 1 :]
            # Remove closing fence
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].rstrip()

        data = json.loads(cleaned)
        if not isinstance(data, list):
            logger.warning("LLM response is not a JSON array, wrapping")
            data = [data]

        flows: list[BusinessFlow] = []
        for item in data:
            flow_type = item.get("flow_type", "user_flow")
            if flow_type not in VALID_FLOW_TYPES:
                flow_type = "user_flow"
            flows.append(
                BusinessFlow(
                    name=item.get("name", "Unnamed Flow"),
                    flow_type=flow_type,
                    description=item.get("description", ""),
                    involved_nodes=item.get("involved_nodes", []),
                )
            )
        return flows
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Failed to parse flow analysis response: %s", exc)
        return []


def analyze_domain(
    domain: Domain,
    summaries: dict[str, dict],
    llm_client: LLMClient,
) -> list[BusinessFlow]:
    """Analyze a single domain for business flows using Tier 2 LLM.

    Args:
        domain: Domain with nodes, edges, entry_points
        summaries: dict mapping qualified_name -> Tier 1 summary dict
        llm_client: LLM client instance

    Returns list of BusinessFlow objects.
    """
    prompt = _build_flow_prompt(domain, summaries)
    response = llm_client.invoke("tier2", prompt)
    flows = _parse_flows(response.text)
    logger.info(
        "Domain '%s': identified %d business flows (model=%s)",
        domain.name,
        len(flows),
        response.model,
    )
    return flows


def _load_flows_from_db(
    domain_name: str,
    scan_id: int,
    db_path: Path,
) -> list[BusinessFlow]:
    """Load existing BusinessFlow objects from business_flows table for a domain."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT name, flow_type, description, node_ids FROM business_flows "
            "WHERE scan_id = ? AND domain = ?",
            (scan_id, domain_name),
        ).fetchall()
        flows: list[BusinessFlow] = []
        for row in rows:
            flow_type = row["flow_type"] if row["flow_type"] in VALID_FLOW_TYPES else "user_flow"
            try:
                involved_nodes = json.loads(row["node_ids"]) if row["node_ids"] else []
            except (json.JSONDecodeError, ValueError):
                involved_nodes = []
            flows.append(
                BusinessFlow(
                    name=row["name"],
                    flow_type=flow_type,
                    description=row["description"] or "",
                    involved_nodes=involved_nodes,
                )
            )
        return flows
    finally:
        conn.close()


def analyze_all_domains(
    domains: list[Domain],
    summaries: dict[str, dict],
    llm_client: LLMClient,
    on_progress: Callable | None = None,
    db_path: Path | None = None,
    scan_id: int | None = None,
) -> dict[str, list[BusinessFlow]]:
    """Analyze all domains sequentially (Tier 2 is expensive, no need for concurrency).

    Returns dict mapping domain_name -> list[BusinessFlow].
    Calls on_progress(completed, total) after each domain.

    If db_path and scan_id are provided, domains already present in business_flows
    are skipped (resume support) and their flows are loaded from the DB instead.
    """
    # Skip domains already analyzed (resume support)
    if db_path is not None and scan_id is not None:
        try:
            conn = get_conn(db_path)
            try:
                rows = conn.execute(
                    "SELECT DISTINCT domain FROM business_flows WHERE scan_id = ?",
                    (scan_id,),
                ).fetchall()
                done_domains = {r["domain"] for r in rows}
            finally:
                conn.close()
        except Exception:
            done_domains = set()
    else:
        done_domains = set()

    results: dict[str, list[BusinessFlow]] = {}
    total = len(domains)

    for i, domain in enumerate(domains):
        if domain.name in done_domains:
            # Load existing flows from DB instead of re-invoking LLM
            flows = _load_flows_from_db(domain.name, scan_id, db_path)
            results[domain.name] = flows
        else:
            flows = analyze_domain(domain, summaries, llm_client)
            results[domain.name] = flows
        if on_progress is not None:
            on_progress(i + 1, total)

    return results


def persist_flows(
    flows_by_domain: dict[str, list[BusinessFlow]],
    scan_id: int,
    db_path: Path,
    model_used: str = "",
) -> None:
    """Write flows to business_flows table."""
    conn = get_conn(db_path)
    try:
        for domain_name, flows in flows_by_domain.items():
            for flow in flows:
                conn.execute(
                    """INSERT INTO business_flows
                       (scan_id, domain, flow_type, name, description, node_ids, model_used, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scan_id,
                        domain_name,
                        flow.flow_type,
                        flow.name,
                        flow.description,
                        json.dumps(flow.involved_nodes),
                        model_used,
                        now_iso(),
                    ),
                )
        conn.commit()
    finally:
        conn.close()
