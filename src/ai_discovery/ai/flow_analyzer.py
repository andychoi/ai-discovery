"""Tier 2 flow analyzer — uses mid-tier LLM (Sonnet) to identify business flows per domain."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..ai.llm_client import LLMClient, AdvisorContext
from ..db import get_conn, now_iso
from ..graph.models import Domain, CallEdge, ExecutionNode, Scenario, StateTransition

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
    domain: str | None = None
    steps: list[dict] = field(default_factory=list)  # list of {step: int, name: str, type: str, description: str}
    # Hierarchical processing-logic view. Each node:
    #   {name, type (PHASE|PROCESS|GATEWAY|USER_TASK|DB|EXTERNAL|LOOP),
    #    description, source_ref, children: [<node>], branches: [{condition, steps:[<node>]}]}
    # `steps` (flat) is DERIVED from this via `_flatten_structured_steps`, so the
    # existing sequence/BPMN/flowchart renderers and verify_flow keep working
    # unchanged while leveled rendering reads `structured_steps` directly.
    structured_steps: list[dict] = field(default_factory=list)
    input: list[str] = field(default_factory=list)
    process: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    data_flow: list[str] = field(default_factory=list)
    external_interfaces: list[dict] = field(default_factory=list)
    confidence: float = 1.0
    # CRIT-2: file:line of every execution-slice node the scenario traverses, so
    # the PF doc carries auditable source provenance for its (LLM-narrated) steps.
    source_refs: list[dict] = field(default_factory=list)
    # True once verify_flow has set `confidence` from claim-verification against
    # RAG source (vs the unverified default). Lets the renderer decide whether to
    # trust the score or cap it.
    verified: bool = False


# Leaf step types that map 1:1 onto the legacy flat-step renderers. PHASE and
# LOOP are *structural wrappers* in the hierarchical view — they group children
# but are not themselves emitted into the flat `steps` list.
_LEAF_STEP_TYPES = {"PROCESS", "GATEWAY", "USER_TASK", "DB", "EXTERNAL", "EXTERNAL_API", "QUEUE", "ENTRY"}


def _flatten_structured_steps(structured: list[dict]) -> list[dict]:
    """Flatten the hierarchical `structured_steps` into the legacy flat `steps`
    shape ([{step, name, type, description}]) via depth-first walk.

    PHASE / LOOP wrappers are not emitted (their children are inlined); GATEWAY
    is emitted as a marker step followed by the inlined steps of every branch arm
    — so existing sequence/BPMN/flowchart renderers see the same linear stream of
    typed steps they always have, with the leveled detail preserved separately in
    `structured_steps`.
    """
    flat: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            ntype = (node.get("type") or "PROCESS").upper()
            if ntype in _LEAF_STEP_TYPES:
                flat.append({
                    "step": len(flat) + 1,
                    "name": node.get("name", "Step"),
                    "type": ntype,
                    "description": node.get("description", ""),
                })
            # Recurse: GATEWAY branches, then plain children (PHASE/LOOP bodies).
            for branch in node.get("branches", []) or []:
                walk(branch.get("steps", []))
            walk(node.get("children", []))

    walk(structured)
    return flat


class ScenarioFlowInference:
    """Uses GenAI to reconstruct scenario flows from execution slices."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def infer_flow(self, scenario: Scenario, summaries: dict[str, dict]) -> ScenarioFlow:
        """Staged inference: Flow -> IPO -> Interfaces. Every stage is grounded in
        the execution-slice node context (file:line + Tier-1 summary)."""
        structured = self._infer_steps(scenario, summaries)
        # Legacy flat view is DERIVED from the hierarchy so downstream renderers
        # and verify_flow keep working; the leveled view lives in structured.
        flow_steps = _flatten_structured_steps(structured)
        ipo_data = self._infer_ipo(scenario, flow_steps, summaries)
        interfaces = self._infer_interfaces(scenario, flow_steps, summaries)

        return ScenarioFlow(
            scenario_id=scenario.scenario_id,
            domain=scenario.domain,
            steps=flow_steps,
            structured_steps=structured,
            input=ipo_data.get("input", []),
            process=ipo_data.get("process", []),
            output=ipo_data.get("output", []),
            data_flow=ipo_data.get("data_flow", []),
            external_interfaces=interfaces,
            source_refs=self._source_refs(scenario),
        )

    def verify_flow(self, flow: "ScenarioFlow", db_path) -> "ScenarioFlow":
        """CRIT-2: set `flow.confidence` from claim-verification of the flow
        narrative against RAG-indexed source, instead of an unverified default.

        Extracts claims from the step descriptions and verifies each against the
        code via the shared self-review machinery, then blends a deterministic
        confidence. Marks the flow `verified` so the renderer trusts the score.
        This is LLM-cost-heavy (one review pass per scenario), so callers gate it
        (e.g. prod scans only). On any failure it leaves an explicit low score.
        """
        from .self_review import review_document, get_review_summary
        from .rollup import blend_confidence

        narrative = "\n".join(
            f"{s.get('name', '')}: {s.get('description', '')}" for s in flow.steps
        ).strip()
        if not narrative:
            flow.confidence, flow.verified = 0.3, False
            return flow
        try:
            claims = review_document(narrative, db_path, self.llm_client, max_claims=15)
            summary = get_review_summary(claims)
            flow.confidence = blend_confidence(0, summary)
            # "source-verified" must MEAN it: only when the review actually
            # confirmed claims against retrieved source. If RAG retrieved nothing
            # (empty index, no match), every claim is unverified — that is NOT
            # verification, so the doc keeps its unverified banner + capped score.
            flow.verified = summary.get("verified", 0) > 0
        except Exception as e:  # RAG unavailable / model error — don't fake confidence
            logger.warning("PF verification failed for %s: %s", flow.scenario_id, e)
            flow.confidence, flow.verified = 0.4, False
        return flow

    @staticmethod
    def _source_refs(scenario: Scenario) -> list[dict]:
        """Auditable file:line provenance for each node the scenario traverses."""
        refs: list[dict] = []
        seen: set[str] = set()
        for node in scenario.nodes:
            fp = getattr(node, "file_path", "") or ""
            line = getattr(node, "line_number", 0) or 0
            if not fp or node.qualified_name in seen:
                continue
            seen.add(node.qualified_name)
            refs.append({
                "name": node.name,
                "qualified_name": node.qualified_name,
                "source": f"{fp}:{line}",
            })
        return refs

    def _nodes_context(self, scenario: Scenario, summaries: dict[str, dict]) -> str:
        """Grounded node block fed to every inference prompt: qualified name,
        type, file:line, and the Tier-1 summary (purpose + I/O). Replaces the
        bare name-only listing so the LLM reconstructs steps from real code
        context, not just identifiers (CRIT-2: source-fed prompts)."""
        lines: list[str] = []
        for node in scenario.nodes:
            s = summaries.get(node.qualified_name, {})
            fp = getattr(node, "file_path", "") or "?"
            line = getattr(node, "line_number", 0) or 0
            lines.append(f"- {node.qualified_name} ({node.type}) @ {fp}:{line}")
            purpose = s.get("purpose")
            if purpose:
                lines.append(f"    purpose: {purpose}")
            io = s.get("io_summary")
            if io:
                lines.append(f"    io: {io}")
            if node.state_transition:
                t = node.state_transition
                line = f"    transition: {t.entity}.{t.field} -> {t.to_state}"
                # Surface the real source-level condition so the LLM can attach it
                # to the right branch arm instead of guessing branch structure.
                if t.guard_expr:
                    line += f" WHEN {t.guard_expr}"
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _alternate_paths_context(scenario: Scenario) -> str:
        """Render the slice builder's mined conditional branches so the LLM has
        real branch structure (condition + path) to ground GATEWAY arms in."""
        alts = getattr(scenario, "alternate_paths", None) or []
        if not alts:
            return ""
        lines = ["Known conditional branches (from execution-slice analysis):"]
        for alt in alts:
            cond = alt.get("condition", "?")
            path = alt.get("path", []) or []
            lines.append(f"- WHEN {cond}: {' -> '.join(path) if path else '(steps)'}")
        return "\n".join(lines)

    def _infer_steps(self, scenario: Scenario, summaries: dict[str, dict]) -> list[dict]:
        prompt = self._build_steps_prompt(scenario, summaries)
        response = self.llm_client.invoke_with_advisor(
            "tier2", prompt,
            context=AdvisorContext(domain="scenario_steps", max_advisor_cost_pct=0.3)
        )
        return self._parse_json_response(response.text, "flow")

    def _infer_ipo(self, scenario: Scenario, flow_steps: list[dict], summaries: dict[str, dict]) -> dict:
        prompt = self._build_ipo_prompt(scenario, flow_steps, summaries)
        response = self.llm_client.invoke_with_advisor(
            "tier2", prompt,
            context=AdvisorContext(domain="scenario_ipo", max_advisor_cost_pct=0.15)
        )
        return self._parse_json_response(response.text)

    def _infer_interfaces(self, scenario: Scenario, flow_steps: list[dict], summaries: dict[str, dict]) -> list[dict]:
        prompt = self._build_interfaces_prompt(scenario, flow_steps, summaries)
        response = self.llm_client.invoke_with_advisor(
            "tier2", prompt,
            context=AdvisorContext(domain="scenario_interfaces", max_advisor_cost_pct=0.2)
        )
        return self._parse_json_response(response.text, "interfaces")

    def _build_steps_prompt(self, scenario: Scenario, summaries: dict[str, dict]) -> str:
        alt_block = self._alternate_paths_context(scenario)
        alt_section = f"\n{alt_block}\n" if alt_block else ""
        return (
            "You are a software architect. Convert the following execution path into a coherent,\n"
            "LEVELED business process flow — a 2-3 level hierarchy, not a flat list.\n"
            f"Scenario: {scenario.name} (Trigger: {scenario.trigger_type})\n\n"
            "Execution Path Nodes (with source location and behavior):\n"
            + self._nodes_context(scenario, summaries) + "\n"
            + alt_section + "\n"
            "TASK:\n"
            "1. Group the steps into a few top-level business PHASES (e.g. 'Validate & Price', 'Persist & Notify').\n"
            "2. Under each phase, nest the concrete steps as `children`, ordered logically and named in business terms.\n"
            "3. For conditional logic use type GATEWAY and attach `branches`: one entry per arm with its real\n"
            "   `condition` (prefer the WHEN-condition / branch condition shown above; else describe it) and the\n"
            "   `steps` that run only in that arm. Use an 'else' condition for the fallback arm.\n"
            "4. For repeated/iterative processing over a collection use type LOOP with the repeated work as `children`.\n"
            "5. Use type USER_TASK for manual human steps (approval, review), DB for database operations,\n"
            "   EXTERNAL for external API calls, PROCESS otherwise.\n"
            "6. Set `source_ref` to the node's file:line when the step maps to a specific node.\n"
            "7. Ground every step in the nodes above — do not invent steps with no corresponding node.\n\n"
            "OUTPUT JSON (nested; omit empty children/branches):\n"
            '{"flow": [\n'
            '  {"name": "Validate & Price", "type": "PHASE", "children": [\n'
            '     {"name": "Validate order items", "type": "PROCESS", "description": "...", "source_ref": "svc/order.py:42"},\n'
            '     {"name": "Apply pricing", "type": "GATEWAY", "description": "...",\n'
            '      "branches": [\n'
            '        {"condition": "order.total > 1000", "steps": [{"name": "Apply premium discount", "type": "PROCESS"}]},\n'
            '        {"condition": "else", "steps": [{"name": "Apply standard pricing", "type": "PROCESS"}]}\n'
            '      ]}\n'
            '  ]}\n'
            ']}'
        )

    def _build_ipo_prompt(self, scenario: Scenario, flow_steps: list[dict], summaries: dict[str, dict]) -> str:
        return (
            "Extract Input-Process-Output (IPO) for this scenario flow, grounded in the actual nodes.\n"
            "Execution Path Nodes:\n" + self._nodes_context(scenario, summaries) + "\n\n"
            f"Flow Steps: {json.dumps(flow_steps)}\n\n"
            "Only include inputs/outputs/data flows supported by the nodes above.\n"
            "OUTPUT JSON:\n"
            '{"input": ["..."], "process": ["..."], "output": ["..."], "data_flow": ["A -> B"]}'
        )

    def _build_interfaces_prompt(self, scenario: Scenario, flow_steps: list[dict], summaries: dict[str, dict]) -> str:
        return (
            "Identify external systems involved in this scenario, grounded in the actual nodes.\n"
            "Execution Path Nodes:\n" + self._nodes_context(scenario, summaries) + "\n\n"
            f"Flow Steps: {json.dumps(flow_steps)}\n\n"
            "Only list interfaces evidenced by a node above (DB/queue/external boundary); do not invent.\n"
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


def _retrieve_flow_context(
    domain_name: str,
    db_path: Path,
    llm_client: LLMClient,
    top_k: int = 5,
) -> str:
    """Retrieve relevant source via RAG to ground Tier-2 flow analysis (P1-d).

    Tier-2 otherwise sees only Tier-1 summaries + graph metadata, so its flows
    are an inference over summaries with no source anchor. Pull the top-k code
    chunks for the domain's business process and format them for the prompt.
    Returns "" on any failure — grounding is best-effort, never fatal.
    """
    try:
        from ..rag.retriever import search
    except ImportError:
        return ""
    query = f"{domain_name} business process flow: control flow, state changes, side effects"
    try:
        results = search(query, db_path, llm_client, top_k=top_k)
    except Exception:
        logger.debug("RAG retrieval failed for flow analysis of %s", domain_name)
        return ""
    if not results:
        return ""
    snippets = [
        f"### {r['qualified_name']} ({r['file_path']})\n```\n{r['chunk_text'][:800]}\n```"
        for r in results
    ]
    return "\n\n".join(snippets)


def _build_flow_prompt(domain: Domain, summaries: dict[str, dict], rag_context: str = "") -> str:
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

    # P1-d: retrieved source code, so flows are grounded in real code rather
    # than inferred from summaries alone.
    if rag_context:
        lines.append("## Source Code Context (from RAG retrieval)")
        lines.append(rag_context)
        lines.append("")

    # Instructions
    lines.append("## Task")
    if rag_context:
        lines.append(
            "Ground every flow in the source code and node evidence above; do not "
            "invent flows or steps with no corresponding node or code.\n"
        )
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
    db_path: Path | None = None,
) -> list[BusinessFlow]:
    """Analyze a single domain for business flows using Tier 2 LLM.

    Args:
        domain: Domain with nodes, edges, entry_points
        summaries: dict mapping qualified_name -> Tier 1 summary dict
        llm_client: LLM client instance
        db_path: when provided, retrieve source via RAG to ground the flows (P1-d)

    Returns list of BusinessFlow objects.
    """
    rag_context = (
        _retrieve_flow_context(domain.name, db_path, llm_client)
        if db_path is not None
        else ""
    )
    prompt = _build_flow_prompt(domain, summaries, rag_context=rag_context)
    response = llm_client.invoke_with_advisor(
        "tier2", prompt,
        context=AdvisorContext(domain="flow_analysis", max_advisor_cost_pct=0.3)
    )
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
    budget_exhausted: Callable[[], bool] | None = None,
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
        # P1-e: stop before starting another domain once the budget is spent.
        # Already-analyzed domains (loaded from DB) are free, so only gate LLM work.
        if (
            domain.name not in done_domains
            and budget_exhausted is not None
            and budget_exhausted()
        ):
            logger.warning(
                "Tier-2 budget limit reached after %d/%d domains; skipping the rest",
                i, total,
            )
            break
        if domain.name in done_domains:
            # Load existing flows from DB instead of re-invoking LLM
            flows = _load_flows_from_db(domain.name, scan_id, db_path)
            results[domain.name] = flows
        else:
            flows = analyze_domain(domain, summaries, llm_client, db_path=db_path)
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


def persist_scenario_flows(
    scenario_flows: list[ScenarioFlow],
    artifacts: dict[str, dict],
    scan_id: int,
    db_path: Path,
) -> None:
    """Write scenario flows and their artifacts to the scenario_flows table."""
    conn = get_conn(db_path)
    try:
        for flow in scenario_flows:
            art = artifacts.get(flow.scenario_id, {})
            conn.execute(
                """INSERT OR REPLACE INTO scenario_flows
                   (scan_id, scenario_id, domain, steps_json, structured_steps_json,
                    input_json, process_json,
                    output_json, data_flow_json, interfaces_json,
                    mermaid, mermaid_flowchart, bpmn_xml, ipo_md, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    flow.scenario_id,
                    flow.domain,
                    json.dumps(flow.steps),
                    json.dumps(flow.structured_steps),
                    json.dumps(flow.input),
                    json.dumps(flow.process),
                    json.dumps(flow.output),
                    json.dumps(flow.data_flow),
                    json.dumps(flow.external_interfaces),
                    art.get("mermaid", ""),
                    art.get("mermaid_flowchart", ""),
                    art.get("bpmn", ""),
                    art.get("ipo", ""),
                    flow.confidence,
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def load_scenario_flows(scan_id: int, db_path: Path) -> tuple[list[ScenarioFlow], dict[str, dict]]:
    """Load ScenarioFlow records and artifacts from DB for resume support.

    Returns (scenario_flows, artifacts) where artifacts maps scenario_id -> dict.
    """
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM scenario_flows WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
    finally:
        conn.close()

    flows: list[ScenarioFlow] = []
    artifacts: dict[str, dict] = {}

    def _load_json(val: str | None, default):
        if not val:
            return default
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return default

    for row in rows:
        flow = ScenarioFlow(
            scenario_id=row["scenario_id"],
            domain=row["domain"],
            steps=_load_json(row["steps_json"], []),
            structured_steps=_load_json(
                row["structured_steps_json"] if "structured_steps_json" in row.keys() else None,
                [],
            ),
            input=_load_json(row["input_json"], []),
            process=_load_json(row["process_json"], []),
            output=_load_json(row["output_json"], []),
            data_flow=_load_json(row["data_flow_json"], []),
            external_interfaces=_load_json(row["interfaces_json"], []),
            confidence=row["confidence"] or 1.0,
        )
        flows.append(flow)
        artifacts[row["scenario_id"]] = {
            "mermaid": row["mermaid"] or "",
            "mermaid_flowchart": row["mermaid_flowchart"] or "",
            "bpmn": row["bpmn_xml"] or "",
            "ipo": row["ipo_md"] or "",
        }

    return flows, artifacts
