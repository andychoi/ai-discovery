"""Generate mining analysis reports and markdown documentation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .process_miner import MiningResult, BottleneckInfo, EdgeFrequency

logger = logging.getLogger(__name__)


class MiningReporter:
    """Generates markdown and JSON reports from process mining results."""

    @staticmethod
    def generate_markdown_report(result: MiningResult) -> str:
        """Generate a complete markdown report from mining results.

        Args:
            result: MiningResult from ProcessMiner

        Returns:
            Markdown string suitable for inclusion in docs
        """
        if not result.conformance:
            return f"""# Process Mining Analysis: {result.scenario_name}

## Status: FAILED

Mining analysis could not complete. No conformance metrics available.

**Warnings:**
{_format_warnings(result.warnings)}
"""

        # Quality gate emoji
        gate_emoji = "✅" if result.fitness_passed else "⚠️"
        gate_status = "PASS" if result.fitness_passed else "FAIL"

        # Build sections
        sections = [
            f"# Process Mining Analysis: {result.scenario_name}",
            "",
            f"**Scenario ID:** `{result.scenario_id}`",
            f"**Domain:** {result.domain or '(unknown)'}",
            f"**Mined:** {result.mined_at}",
            "",
            _section_conformance(result),
            "",
            _section_quality_gate(result, gate_emoji, gate_status),
            "",
            _section_performance(result),
            "",
            _section_bottlenecks(result),
            "",
            _section_edges(result),
            "",
            _section_diagnostics(result),
        ]

        return "\n".join(sections)

    @staticmethod
    def generate_json_report(result: MiningResult) -> dict:
        """Export mining results as JSON (for ingestion, archival).

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
            "conformance": {
                "fitness": round(result.conformance.fitness, 3) if result.conformance else None,
                "precision": round(result.conformance.precision, 3) if result.conformance else None,
                "generalization": round(result.conformance.generalization, 3) if result.conformance else None,
                "failed_traces_count": result.conformance.failed_traces_count if result.conformance else None,
                "total_traces_count": result.conformance.total_traces_count if result.conformance else None,
            },
            "cycle_time": {
                "avg_seconds": round(result.avg_cycle_time_seconds, 2),
            },
            "bottlenecks": [
                {
                    "activity_name": b.activity_name,
                    "avg_duration_ms": round(b.avg_duration_ms, 1),
                    "median_duration_ms": round(b.median_duration_ms, 1),
                    "std_dev_ms": round(b.std_dev_ms, 1),
                    "max_duration_ms": round(b.max_duration_ms, 1),
                    "min_duration_ms": round(b.min_duration_ms, 1),
                    "frequency": b.frequency,
                    "severity": b.severity,
                }
                for b in result.bottlenecks
            ],
            "edge_frequencies": [
                {
                    "from_activity": e.from_activity,
                    "to_activity": e.to_activity,
                    "count": e.count,
                    "percentage": round(e.percentage, 1),
                }
                for e in result.edge_frequencies
            ],
            "quality_gate": {
                "fitness_threshold": result.fitness_threshold,
                "fitness_passed": result.fitness_passed,
            },
            "unmodeled_paths": result.unmodeled_paths,
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

        logger.info(f"Mining reports saved: {md_path}, {json_path}")

        return {
            "markdown": md_path,
            "json": json_path,
        }


# ─────────────────────────────────────────────────────────────────
# Report section generators
# ─────────────────────────────────────────────────────────────────


def _section_conformance(result: MiningResult) -> str:
    """Conformance metrics section."""
    if not result.conformance:
        return "## Conformance Metrics\n\nNo conformance data available."

    c = result.conformance
    return f"""## Conformance Metrics

| Metric | Value |
|--------|-------|
| **Fitness** | {c.fitness:.1%} |
| **Precision** | {c.precision:.1%} |
| **Generalization** | {c.generalization:.1%} |
| **Failed Traces** | {c.failed_traces_count} / {c.total_traces_count} |

**Interpretation:**
- **Fitness** ({c.fitness:.1%}): % of event log traces that can be replayed on the discovered model
  - Below 0.90 → unmodeled behavior detected
- **Precision** ({c.precision:.1%}): % of model behavior reflected in the event log
  - Below 0.85 → model may be over-generalized
- **Generalization** ({c.generalization:.1%}): model's ability to handle unseen traces
  - Below 0.80 → limited generalization capability
"""


def _section_quality_gate(result: MiningResult, emoji: str, status: str) -> str:
    """Quality gate section."""
    threshold_pct = result.fitness_threshold * 100

    if result.fitness_passed:
        return f"""## Quality Gate: {emoji} {status}

✅ Model fitness ({result.conformance.fitness:.1%}) meets threshold ({threshold_pct:.0f}%)

Process model is well-fitted to observed behavior.
"""
    else:
        unmodeled_pct = (1.0 - result.conformance.fitness) * 100
        warnings_md = _format_warnings(result.warnings)
        return f"""## Quality Gate: {emoji} {status}

⚠️ Model fitness ({result.conformance.fitness:.1%}) below threshold ({threshold_pct:.0f}%)

**Gap:** {unmodeled_pct:.1f}% of traces show unmodeled behavior

### Recommendations:
1. Review `alternate_paths` in scenario definition for missing conditional branches
2. Check for exception handling or error paths not captured in main flow
3. Verify gateway conditions are correctly named in BPMN
4. Consider runtime log ingestion to validate with real execution data

### Warnings:
{warnings_md}
"""


def _section_performance(result: MiningResult) -> str:
    """Performance metrics section."""
    return f"""## Performance Metrics

- **Average Cycle Time:** {result.avg_cycle_time_seconds:.1f} seconds
- **Total Bottleneck Activities:** {len(result.bottlenecks)}
- **Critical/High Severity:** {sum(1 for b in result.bottlenecks if b.severity in ('critical', 'high'))}
"""


def _section_bottlenecks(result: MiningResult) -> str:
    """Bottlenecks section."""
    if not result.bottlenecks:
        return "## Bottleneck Analysis\n\nNo significant bottlenecks detected."

    lines = ["## Bottleneck Analysis", ""]
    lines.append("**Severity Legend:** 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low")
    lines.append("")

    severity_emoji = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }

    for i, bottleneck in enumerate(result.bottlenecks[:10], 1):
        emoji = severity_emoji.get(bottleneck.severity, "•")
        lines.append(f"### {i}. {bottleneck.activity_name} {emoji}")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| **Avg Duration** | {bottleneck.avg_duration_ms:.0f} ms |")
        lines.append(f"| **Median** | {bottleneck.median_duration_ms:.0f} ms |")
        lines.append(f"| **Std Dev** | {bottleneck.std_dev_ms:.0f} ms |")
        lines.append(f"| **Min / Max** | {bottleneck.min_duration_ms:.0f} / {bottleneck.max_duration_ms:.0f} ms |")
        lines.append(f"| **Frequency** | {bottleneck.frequency} occurrences |")
        lines.append("")

        # Recommendation based on severity
        if bottleneck.severity == "critical":
            lines.append("**Action:** Prioritize optimization. Consider:")
            lines.append("- Parallelization of independent sub-tasks")
            lines.append("- Caching or memoization of frequent computations")
            lines.append("- Async/deferred processing if blocking on I/O")
        elif bottleneck.severity == "high":
            lines.append("**Action:** Consider optimization in next iteration.")
        lines.append("")

    return "\n".join(lines)


def _section_edges(result: MiningResult) -> str:
    """Edge frequency section."""
    if not result.edge_frequencies:
        return "## Edge Frequency Analysis\n\nNo edge data available."

    lines = ["## Edge Frequency Analysis", ""]
    lines.append("Shows which transitions occur in the observed event log.")
    lines.append("")
    lines.append("| From Activity | To Activity | Count | Frequency |")
    lines.append("|---------------|------------|-------|-----------|")

    for edge in result.edge_frequencies[:20]:
        lines.append(
            f"| {edge.from_activity} | {edge.to_activity} | {edge.count} | {edge.percentage:.1f}% |"
        )

    lines.append("")
    lines.append("**Interpretation:**")
    lines.append("- **100% frequency** = mandatory transition")
    lines.append("- **80–99% frequency** = common path")
    lines.append("- **< 50% frequency** = rare or conditional path (verify against BPMN gateways)")

    return "\n".join(lines)


def _section_diagnostics(result: MiningResult) -> str:
    """Diagnostics and comparison section."""
    lines = ["## Diagnostics & Next Steps", ""]

    if result.unmodeled_paths:
        lines.append("### Unmodeled Paths Detected")
        lines.append("")
        for path in result.unmodeled_paths:
            lines.append(f"- {path}")
        lines.append("")

    if result.warnings:
        lines.append("### Warnings")
        lines.append("")
        for warning in result.warnings:
            lines.append(f"- ⚠️ {warning}")
        lines.append("")

    lines.append("### Comparison: Discovered vs. Original BPMN")
    lines.append("")
    if result.comparison_notes:
        lines.append(result.comparison_notes)
    else:
        lines.append("- Discovered model reflects observed execution behavior")
        lines.append("- Verify discovered model against intended design")
        lines.append("- Discrepancies may indicate bugs, feature requests, or edge cases")
    lines.append("")

    lines.append("### Recommendations")
    lines.append("")
    lines.append("1. **If fitness < 0.90:** Review and update BPMN to include missing paths")
    lines.append("2. **If precision < 0.85:** Refine BPMN gateways to reflect actual branching")
    lines.append("3. **High variance in cycle time:** Identify variable-duration activities for optimization")
    lines.append("4. **Rare edges:** Evaluate if these are error handling paths or genuine alternate flows")

    return "\n".join(lines)


def _format_warnings(warnings: list[str]) -> str:
    """Format warnings as markdown bullet list."""
    if not warnings:
        return "(none)"
    return "\n".join(f"- {w}" for w in warnings)
