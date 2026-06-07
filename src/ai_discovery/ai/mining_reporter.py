"""Generate flow-statistics reports and markdown documentation.

Formerly rendered pm4py conformance/bottleneck sections; those were removed
2026-06-06 because the underlying numbers were fabricated (hardcoded fitness,
synthetic timestamps). Only structural edge-frequency statistics remain.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .process_miner import MiningResult, EdgeFrequency

logger = logging.getLogger(__name__)


class MiningReporter:
    """Generates markdown and JSON reports from flow statistics."""

    @staticmethod
    def generate_markdown_report(result: MiningResult) -> str:
        """Generate a complete markdown report from flow statistics.

        Args:
            result: MiningResult from ProcessMiner

        Returns:
            Markdown string suitable for inclusion in docs
        """
        sections = [
            f"# Flow Statistics: {result.scenario_name}",
            "",
            f"**Scenario ID:** `{result.scenario_id}`",
            f"**Domain:** {result.domain or '(unknown)'}",
            f"**Events:** {result.event_count}",
            f"**Generated:** {result.mined_at}",
            "",
            _section_edges(result),
            "",
            _section_diagnostics(result),
        ]

        return "\n".join(sections)

    @staticmethod
    def generate_json_report(result: MiningResult) -> dict:
        """Export flow statistics as JSON (for ingestion, archival).

        Args:
            result: MiningResult from ProcessMiner

        Returns:
            Dict suitable for JSON serialization
        """
        return {
            "scenario_id": result.scenario_id,
            "scenario_name": result.scenario_name,
            "domain": result.domain,
            "mined_at": result.mined_at,
            "event_count": result.event_count,
            "edge_frequencies": [
                {
                    "from_activity": e.from_activity,
                    "to_activity": e.to_activity,
                    "count": e.count,
                    "percentage": round(e.percentage, 1),
                }
                for e in result.edge_frequencies
            ],
            "warnings": result.warnings,
        }

    @staticmethod
    def save_reports(result: MiningResult, output_dir: Path) -> dict[str, Path]:
        """Save both markdown and JSON reports to disk.

        Args:
            result: MiningResult
            output_dir: Directory to save reports in

        Returns:
            Dict with keys "markdown" and "json" → Path objects
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Markdown report
        md_path = output_dir / f"{result.scenario_id}_mining_report.md"
        md_content = MiningReporter.generate_markdown_report(result)
        md_path.write_text(md_content, encoding="utf-8")

        # JSON report
        json_path = output_dir / f"{result.scenario_id}_mining_report.json"
        json_data = MiningReporter.generate_json_report(result)
        json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

        logger.info(f"Flow-statistics reports saved: {md_path}, {json_path}")

        return {
            "markdown": md_path,
            "json": json_path,
        }


# ─────────────────────────────────────────────────────────────────
# Report section generators
# ─────────────────────────────────────────────────────────────────


def _section_edges(result: MiningResult) -> str:
    """Edge frequency section."""
    if not result.edge_frequencies:
        return "## Edge Frequency Analysis\n\nNo edge data available."

    lines = ["## Edge Frequency Analysis", ""]
    lines.append(
        "Shows which transitions occur in the statically inferred scenario flow. "
        "These are code-derived sequences, not observed runtime behavior."
    )
    lines.append("")
    lines.append("| From Activity | To Activity | Count | Frequency |")
    lines.append("|---------------|------------|-------|-----------|")

    for edge in result.edge_frequencies[:20]:
        lines.append(
            f"| {edge.from_activity} | {edge.to_activity} | {edge.count} | {edge.percentage:.1f}% |"
        )

    lines.append("")
    lines.append("**Interpretation:**")
    lines.append("- Repeated edges (count > 1) indicate loops or shared sub-flows in the scenario")
    lines.append("- Verify rare or conditional transitions against BPMN gateways")

    return "\n".join(lines)


def _section_diagnostics(result: MiningResult) -> str:
    """Diagnostics section."""
    lines = ["## Diagnostics", ""]

    if result.warnings:
        lines.append("### Warnings")
        lines.append("")
        for warning in result.warnings:
            lines.append(f"- ⚠️ {warning}")
        lines.append("")
    else:
        lines.append("(no warnings)")
        lines.append("")

    lines.append(
        "> Note: these statistics describe the *as-inferred* flow from static "
        "analysis. They carry no runtime performance or conformance information; "
        "validating against real execution requires runtime event-log ingestion."
    )

    return "\n".join(lines)
